"""Etsy-specific extraction adapter. Etsy runs heavy bot detection; expect
intermittent blocks from datacenter IPs. JSON-LD Product data is usually present
on listing pages, so the shared engine does most of the work here."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .utils import absolute_url, clean_text, dedupe_preserve_order, parse_price

NAME = "etsy"

_PRODUCT_RE = re.compile(r"/listing/\d+")


def is_product_url(url: str) -> bool:
    return bool(_PRODUCT_RE.search(url or ""))


def extract_listing_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.select("a.listing-link[href], a[href*='/listing/']"):
        href = a.get("href", "")
        if is_product_url(href):
            links.append(absolute_url(base_url, href.split("?")[0]))
    return dedupe_preserve_order(links)


def next_page(html: str, current_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    a = (soup.select_one("a.wt-action-group__item[rel='next'][href]")
         or soup.select_one("a[aria-label='Next page'][href]")
         or soup.select_one("a[rel='next'][href]"))
    if a and a.get("href"):
        return absolute_url(current_url, a["href"])
    return None


def detect_block(html: str, title: str, url: str) -> str | None:
    hay = (html or "").lower()
    if "px-captcha" in hay or "please verify you are a human" in hay:
        return "Etsy human-verification / CAPTCHA"
    if "captcha" in (title or "").lower():
        return "Etsy CAPTCHA page"
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

    rec["product_name"] = await text("h1[data-buy-box-listing-title], h1.wt-text-body-01")
    price_raw = await text("div[data-buy-box-region='price'] p.wt-text-title-larger") \
        or await text("p[data-selector='price-only'] .currency-value")
    if price_raw:
        p, c = parse_price(price_raw)
        rec["price"], rec["currency"] = p, c
    shop = await text("div[data-shop-name], span.wt-text-link-no-underline a span")
    if shop:
        rec["seller_name"] = shop
    rec["review_count"] = re.sub(
        r"[^0-9]", "", await text("div[data-reviews] h2, button[data-reviews-total]")
    )

    try:
        img = await page.query_selector("ul[data-carousel-pane-list] img, img[data-src-zoom-image]")
        if img:
            src = (await img.get_attribute("data-src-zoom-image")
                   or await img.get_attribute("src"))
            if src:
                rec["main_image"] = src
    except Exception:
        pass

    return {k: v for k, v in rec.items() if v}
