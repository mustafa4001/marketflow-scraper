"""eBay-specific extraction adapter. eBay is the most automation-tolerant of the
three, so this adapter is where you'll get the most reliable results."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .utils import absolute_url, clean_text, dedupe_preserve_order, parse_price

NAME = "ebay"

_PRODUCT_RE = re.compile(r"/(?:itm|p)/")


def is_product_url(url: str) -> bool:
    return "/itm/" in (url or "") or bool(re.search(r"/p/\d+", url or ""))


def extract_listing_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    # Preferred: real search-result links first (keeps result ordering).
    for a in soup.select("a.s-item__link[href], li.s-item a[href], ul.srp-results a[href]"):
        href = a.get("href", "")
        if is_product_url(href):
            links.append(absolute_url(base_url, href.split("?")[0]))
    # Then a general scan to catch any product link missing the result class.
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if is_product_url(href):
            links.append(absolute_url(base_url, href.split("?")[0]))
    return dedupe_preserve_order(links)


def next_page(html: str, current_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    a = (soup.select_one("a.pagination__next[href]")
         or soup.select_one("a[type='next'][href]")
         or soup.select_one("a[aria-label='Next page'][href]"))
    if a and a.get("href") and "disabled" not in (a.get("class") or []):
        return absolute_url(current_url, a["href"])
    return None


def detect_block(html: str, title: str, url: str) -> str | None:
    hay = (html or "").lower()
    if "pardon our interruption" in hay or "px-captcha" in hay:
        return "eBay bot interstitial / CAPTCHA"
    if "signin.ebay" in url:
        return "eBay sign-in required"
    return None


async def dom_extract(page) -> dict:
    rec: dict = {}

    async def text(selector: str) -> str:
        try:
            el = await page.query_selector(selector)
            if el:
                return clean_text(await el.inner_text())
        except Exception:
            return ""
        return ""

    rec["product_name"] = await text("h1.x-item-title__mainTitle span") or await text("h1 .ux-textspans")
    price_raw = await text("div.x-price-primary span.ux-textspans") or await text(".x-bin-price__content span")
    if price_raw:
        p, c = parse_price(price_raw)
        rec["price"], rec["currency"] = p, c
    orig = await text("span.x-additional-info__textual-display span.ux-textspans--STRIKETHROUGH")
    if orig:
        rec["original_price"], _ = parse_price(orig)
    rec["availability"] = await text("div.x-quantity__availability span.ux-textspans")
    rec["seller_name"] = await text("div.x-sellercard-atf__info__about-seller a span.ux-textspans")

    try:
        img = await page.query_selector("div.ux-image-carousel-item img, img.img-scale-down")
        if img:
            src = await img.get_attribute("src") or await img.get_attribute("data-src")
            if src:
                rec["main_image"] = src
    except Exception:
        pass

    # item specifics
    try:
        rows = await page.query_selector_all("div.ux-labels-values--inline, dl.ux-labels-values")
        for row in rows:
            label_el = await row.query_selector(".ux-labels-values__labels .ux-textspans")
            value_el = await row.query_selector(".ux-labels-values__values .ux-textspans")
            if not label_el or not value_el:
                continue
            key = clean_text(await label_el.inner_text()).lower()
            val = clean_text(await value_el.inner_text())
            if not val:
                continue
            if "brand" in key and not rec.get("brand"):
                rec["brand"] = val
            elif "color" in key and not rec.get("color"):
                rec["color"] = val
            elif "material" in key and not rec.get("material"):
                rec["material"] = val
            elif ("mpn" in key or "model" in key) and not rec.get("model_number"):
                rec["model_number"] = val
            elif "upc" in key and not rec.get("upc"):
                rec["upc"] = val
    except Exception:
        pass

    return {k: v for k, v in rec.items() if v}
