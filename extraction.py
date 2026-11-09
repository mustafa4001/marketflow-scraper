"""Marketplace-agnostic extraction engine.

Priority order, per spec:
  1. JSON-LD Product structured data
  2. OpenGraph metadata
  3. Standard HTML metadata
  4. (marketplace CSS selectors live in the adapters and fill remaining blanks)
  5. visible page content

Everything here works on an HTML string via BeautifulSoup, so it is testable
offline against saved fixtures. Only extract what is present; never fabricate.
"""
from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from .utils import (
    CSV_COLUMNS, absolute_url, clean_text, dedupe_preserve_order,
    extract_asin, parse_price,
)

_PRODUCT_TYPES = {"product", "productgroup", "individualproduct"}


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


# --- JSON-LD -------------------------------------------------------------------

def _iter_jsonld_objects(data):
    """Yield every dict node from arbitrarily nested JSON-LD (lists, @graph)."""
    if isinstance(data, list):
        for item in data:
            yield from _iter_jsonld_objects(item)
    elif isinstance(data, dict):
        yield data
        if "@graph" in data:
            yield from _iter_jsonld_objects(data["@graph"])


def _type_matches(obj: dict) -> bool:
    t = obj.get("@type")
    if isinstance(t, list):
        return any(str(x).lower() in _PRODUCT_TYPES for x in t)
    return str(t).lower() in _PRODUCT_TYPES


def extract_jsonld_products(soup: BeautifulSoup) -> list[dict]:
    """Return all JSON-LD objects that look like Products. Invalid blocks are
    skipped rather than raising."""
    products = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            # Some sites concatenate multiple JSON objects; try a lenient split.
            try:
                data = json.loads(raw.replace("}\n{", "},{").join(["[", "]"]))
            except Exception:
                continue
        for obj in _iter_jsonld_objects(data):
            if _type_matches(obj):
                products.append(obj)
    return products


def _as_name(value) -> str:
    if isinstance(value, dict):
        return clean_text(value.get("name") or value.get("@id") or "")
    if isinstance(value, list) and value:
        return _as_name(value[0])
    return clean_text(value)


def _collect_images(value, base_url: str) -> list[str]:
    out: list[str] = []

    def add(v):
        if isinstance(v, str):
            out.append(absolute_url(base_url, v))
        elif isinstance(v, dict):
            url = v.get("url") or v.get("contentUrl") or v.get("@id")
            if url:
                out.append(absolute_url(base_url, url))
        elif isinstance(v, list):
            for item in v:
                add(item)

    add(value)
    return dedupe_preserve_order(out)


def _first_offer(offers):
    """Offers may be a dict, a list, or an AggregateOffer with .offers."""
    if isinstance(offers, dict):
        if "offers" in offers:
            return _first_offer(offers["offers"])
        return offers
    if isinstance(offers, list) and offers:
        # Prefer the lowest-priced concrete offer.
        def price_key(o):
            try:
                return float(str(o.get("price", "")).replace(",", "") or "inf")
            except (TypeError, ValueError):
                return float("inf")
        return sorted([o for o in offers if isinstance(o, dict)], key=price_key)[0] \
            if any(isinstance(o, dict) for o in offers) else {}
    return {}


def from_jsonld(obj: dict, base_url: str) -> dict:
    """Map a single JSON-LD Product object to partial record fields."""
    rec: dict = {}
    rec["product_name"] = _as_name(obj.get("name"))
    rec["brand"] = _as_name(obj.get("brand"))
    rec["description"] = clean_text(obj.get("description"))
    rec["color"] = clean_text(obj.get("color"))
    rec["material"] = _as_name(obj.get("material"))
    rec["model_number"] = clean_text(obj.get("model") or obj.get("mpn"))
    rec["gtin"] = clean_text(
        obj.get("gtin") or obj.get("gtin13") or obj.get("gtin14")
        or obj.get("gtin12") or obj.get("gtin8")
    )
    rec["upc"] = clean_text(obj.get("gtin12") or obj.get("upc"))
    sku = clean_text(obj.get("sku"))

    # weight
    weight = obj.get("weight")
    if isinstance(weight, dict):
        rec["weight"] = clean_text(weight.get("value"))
        rec["weight_unit"] = clean_text(weight.get("unitCode") or weight.get("unitText"))
    elif weight:
        rec["weight"] = clean_text(weight)

    # images
    imgs = _collect_images(obj.get("image"), base_url)
    if imgs:
        rec["main_image"] = imgs[0]
        rec["images"] = imgs

    # offers
    offer = _first_offer(obj.get("offers"))
    if offer:
        price, cur = parse_price(offer.get("price") or offer.get("lowPrice"))
        rec["price"] = price
        rec["currency"] = cur or clean_text(offer.get("priceCurrency"))
        avail = clean_text(offer.get("availability"))
        rec["availability"] = avail.rsplit("/", 1)[-1] if avail else ""
        seller = offer.get("seller")
        rec["seller_name"] = _as_name(seller)
        if offer.get("highPrice"):
            rec["original_price"], _ = parse_price(offer.get("highPrice"))

    # ratings
    rating = obj.get("aggregateRating")
    if isinstance(rating, dict):
        rec["rating"] = clean_text(rating.get("ratingValue"))
        rec["review_count"] = clean_text(
            rating.get("reviewCount") or rating.get("ratingCount")
        )

    # additionalProperty -> fill color/material/model/upc when named
    props = obj.get("additionalProperty")
    if isinstance(props, list):
        for p in props:
            if not isinstance(p, dict):
                continue
            name = clean_text(p.get("name")).lower()
            val = clean_text(p.get("value"))
            if not val:
                continue
            if "color" in name and not rec.get("color"):
                rec["color"] = val
            elif "material" in name and not rec.get("material"):
                rec["material"] = val
            elif ("model" in name) and not rec.get("model_number"):
                rec["model_number"] = val
            elif "upc" in name and not rec.get("upc"):
                rec["upc"] = val

    if sku and not rec.get("model_number"):
        rec["model_number"] = sku
    return {k: v for k, v in rec.items() if v}


# --- OpenGraph / meta ----------------------------------------------------------

def _meta(soup: BeautifulSoup, *, prop=None, name=None) -> str:
    if prop:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return clean_text(tag["content"])
    if name:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return clean_text(tag["content"])
    return ""


def from_opengraph(soup: BeautifulSoup, base_url: str) -> dict:
    rec = {}
    title = _meta(soup, prop="og:title")
    if title:
        rec["product_name"] = title
    img = _meta(soup, prop="og:image") or _meta(soup, prop="og:image:url")
    if img:
        rec["main_image"] = absolute_url(base_url, img)
        rec["images"] = [absolute_url(base_url, img)]
    price = _meta(soup, prop="product:price:amount") or _meta(soup, prop="og:price:amount")
    if price:
        p, c = parse_price(price)
        rec["price"] = p
        if c:
            rec["currency"] = c
    cur = _meta(soup, prop="product:price:currency") or _meta(soup, prop="og:price:currency")
    if cur:
        rec["currency"] = cur
    avail = _meta(soup, prop="product:availability") or _meta(soup, prop="og:availability")
    if avail:
        rec["availability"] = avail
    brand = _meta(soup, prop="product:brand") or _meta(soup, prop="og:brand")
    if brand:
        rec["brand"] = brand
    desc = _meta(soup, prop="og:description")
    if desc:
        rec["description"] = desc
    return {k: v for k, v in rec.items() if v}


def from_meta(soup: BeautifulSoup) -> dict:
    rec = {}
    if soup.title and soup.title.string:
        rec["product_name"] = clean_text(soup.title.string)
    desc = _meta(soup, name="description") or _meta(soup, name="twitter:description")
    if desc:
        rec["description"] = desc
    return {k: v for k, v in rec.items() if v}


# --- merge + normalize ---------------------------------------------------------

def merge_fields(*partials: dict) -> dict:
    """First non-empty value for each field wins. Pass partials in priority
    order (highest priority first)."""
    out: dict = {}
    for part in partials:
        for key, val in (part or {}).items():
            if val and not out.get(key):
                out[key] = val
    return out


def extract_static(html: str, url: str) -> dict:
    """Run the HTML-only extractors (JSON-LD > OG > meta) and merge."""
    soup = _soup(html)
    jsonld_parts = [from_jsonld(obj, url) for obj in extract_jsonld_products(soup)]
    jsonld = merge_fields(*jsonld_parts) if jsonld_parts else {}
    og = from_opengraph(soup, url)
    meta = from_meta(soup)
    return merge_fields(jsonld, og, meta)


def normalize_record(partial: dict, url: str, marketplace: str, scraped_at: str) -> dict:
    """Produce a full record containing every one of the 25 columns."""
    rec = {col: "" for col in CSV_COLUMNS}
    rec.update({k: v for k, v in (partial or {}).items() if k in CSV_COLUMNS})
    rec["time_scraped"] = scraped_at
    rec["product_url"] = url
    if marketplace == "amazon" and not rec.get("asin"):
        rec["asin"] = extract_asin(url)
    # images -> keep list internally; CSV flattening happens at export time
    if isinstance(rec.get("images"), str) and rec["images"]:
        rec["images"] = [rec["images"]]
    # normalize availability tail (e.g. schema.org/InStock -> InStock)
    if isinstance(rec.get("availability"), str) and "/" in rec["availability"]:
        rec["availability"] = rec["availability"].rsplit("/", 1)[-1]
    return rec
