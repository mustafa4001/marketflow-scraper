"""Shared helpers: URL validation, marketplace detection, parsing, CSV export.

Nothing in here touches the network or a browser, so it is fully unit-testable.
"""
from __future__ import annotations

import csv
import io
import ipaddress
import re
import socket
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, quote_plus

# --- Marketplace configuration -------------------------------------------------

# Host suffixes we are willing to open. Anything else is rejected before a
# browser is ever pointed at it (SSRF / scope guard).
MARKETPLACE_DOMAINS: dict[str, tuple[str, ...]] = {
    "amazon": (
        "amazon.com", "amazon.co.uk", "amazon.ca", "amazon.de", "amazon.fr",
        "amazon.es", "amazon.it", "amazon.in", "amazon.com.au", "amazon.co.jp",
        "amazon.com.mx", "amazon.nl", "amazon.se",
    ),
    "ebay": (
        "ebay.com", "ebay.co.uk", "ebay.de", "ebay.ca", "ebay.com.au",
        "ebay.fr", "ebay.it", "ebay.es", "ebay.nl",
    ),
    "etsy": ("etsy.com",),
}

# Exact column order required for the CSV export. Do not reorder.
CSV_COLUMNS: tuple[str, ...] = (
    "time_scraped", "product_url", "product_name", "asin", "brand", "price",
    "original_price", "rating", "review_count", "description", "features",
    "main_image", "images", "gtin", "upc", "color", "model_number", "currency",
    "availability", "seller_name", "weight", "weight_unit", "material",
    "badges", "shipping_info",
)


def now_iso() -> str:
    """UTC timestamp for the time_scraped field."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# --- Marketplace / URL handling ------------------------------------------------

def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().lstrip(".")


def detect_marketplace(url: str) -> str | None:
    """Return 'amazon' | 'ebay' | 'etsy' from a URL host, or None."""
    host = _host(url)
    if not host:
        return None
    for market, suffixes in MARKETPLACE_DOMAINS.items():
        for suffix in suffixes:
            if host == suffix or host.endswith("." + suffix):
                return market
    return None


def _is_private_host(host: str) -> bool:
    """Block localhost / private / link-local targets to prevent SSRF."""
    if host in ("localhost", "localhost.localdomain", "127.0.0.1", "::1", ""):
        return True
    # Literal IP?
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        pass
    # Resolve the hostname and reject if ANY resolved address is private.
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        # Can't resolve -> treat as unsafe.
        return True
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


def validate_and_normalize_url(url: str, *, resolve_dns: bool = True) -> str:
    """Validate a user-supplied URL.

    Rejects non-http(s) schemes, unknown marketplaces, and private/loopback
    targets. Returns the normalized URL or raises ValueError.
    Set resolve_dns=False in tests to skip the network DNS lookup.
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("empty URL")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL has no host")
    if detect_marketplace(url) is None:
        raise ValueError("URL is not a supported marketplace (amazon/ebay/etsy)")
    if resolve_dns and _is_private_host(host):
        raise ValueError("URL resolves to a private or loopback address")
    return url


def is_allowed_url(url: str, *, resolve_dns: bool = True) -> bool:
    try:
        validate_and_normalize_url(url, resolve_dns=resolve_dns)
        return True
    except ValueError:
        return False


def build_search_url(marketplace: str, keyword: str) -> str:
    """Construct a public search-results URL from a keyword."""
    kw = quote_plus((keyword or "").strip())
    if not kw:
        raise ValueError("empty keyword")
    if marketplace == "amazon":
        return f"https://www.amazon.com/s?k={kw}"
    if marketplace == "ebay":
        return f"https://www.ebay.com/sch/i.html?_nkw={kw}"
    if marketplace == "etsy":
        return f"https://www.etsy.com/search?q={kw}"
    raise ValueError(f"unknown marketplace: {marketplace}")


_ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})(?:[/?]|$)")


def extract_asin(url: str) -> str:
    """Pull a 10-char ASIN out of an Amazon product URL, or '' if none."""
    m = _ASIN_RE.search(url or "")
    return m.group(1) if m else ""


# --- Text / value normalization ------------------------------------------------

def clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text


_PRICE_NUM_RE = re.compile(r"[-+]?[0-9][0-9,.\s]*")
_CURRENCY_SYMBOLS = {
    "$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY", "₹": "INR",
    "C$": "CAD", "A$": "AUD",
}


def parse_price(raw) -> tuple[str, str]:
    """Return (price_string, currency_code_or_empty) from a messy price value.

    Keeps the numeric value as a clean string; does not force float so we never
    silently corrupt odd formats.
    """
    if raw is None:
        return "", ""
    text = str(raw).strip()
    if not text:
        return "", ""
    currency = ""
    for sym, code in _CURRENCY_SYMBOLS.items():
        if sym in text:
            currency = code
            break
    # ISO code already present?
    iso = re.search(r"\b(USD|GBP|EUR|JPY|INR|CAD|AUD|MXN|SEK)\b", text, re.I)
    if iso:
        currency = iso.group(1).upper()
    m = _PRICE_NUM_RE.search(text.replace(" ", ""))
    if not m:
        return clean_text(text), currency
    num = m.group(0)
    # Normalize thousands/decimal: strip thousands commas when they look like groupings.
    if "," in num and "." in num:
        num = num.replace(",", "")
    elif num.count(",") == 1 and len(num.split(",")[-1]) == 2:
        num = num.replace(",", ".")  # European decimal comma
    else:
        num = num.replace(",", "")
    return num.strip(), currency


def absolute_url(base: str, src: str) -> str:
    if not src:
        return ""
    src = src.strip()
    if src.startswith("//"):
        return "https:" + src
    return urljoin(base, src)


def dedupe_preserve_order(items) -> list:
    seen = set()
    out = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


# --- CSV export ----------------------------------------------------------------

def record_to_row(record: dict) -> dict:
    """Flatten a product record into CSV-safe scalar strings."""
    row = {}
    for col in CSV_COLUMNS:
        val = record.get(col, "")
        if isinstance(val, (list, tuple)):
            val = " | ".join(clean_text(v) for v in val if clean_text(v))
        elif isinstance(val, dict):
            val = clean_text(val.get("name") or val.get("value") or "")
        row[col] = "" if val is None else str(val)
    return row


def to_csv(records: list[dict]) -> str:
    """Serialize records to a CSV string with correct escaping of commas,
    quotes, newlines and unicode (handled by the stdlib csv module)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(CSV_COLUMNS), extrasaction="ignore")
    writer.writeheader()
    for rec in records:
        writer.writerow(record_to_row(rec))
    return buf.getvalue()
