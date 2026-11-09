"""Amazon-specific extraction adapter.

Best-effort only: Amazon aggressively blocks datacenter-IP automation, so from a
cloud host this adapter will frequently hit a robot check and the job will stop
that request safely. That is expected and documented, not a bug.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .utils import absolute_url, clean_text, dedupe_preserve_order, parse_price

NAME = "amazon"

_PRODUCT_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d)/[A-Z0-9]{10}")


def is_product_url(url: str) -> bool:
    return bool(_PRODUCT_RE.search(url or ""))


def extract_listing_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if is_product_url(href):
            full = absolute_url(base_url, href.split("?")[0].split("/ref=")[0])
            links.append(full)
    return dedupe_preserve_order(links)


def next_page(html: str, current_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    a = soup.select_one("a.s-pagination-next:not(.s-pagination-disabled)")
    if a and a.get("href"):
        return absolute_url(current_url, a["href"])
    return None


def detect_block(html: str, title: str, url: str) -> str | None:
    hay = (html or "").lower()
    title_l = (title or "").lower()
    if "robot check" in title_l or "enter the characters you see" in hay:
        return "Amazon robot check / CAPTCHA"
    if "/errors/validatecaptcha" in hay or "api-services-support@amazon" in hay and "captcha" in hay:
        return "Amazon CAPTCHA page"
    if "ap/signin" in url or "to continue shopping" in title_l:
        return "Amazon sign-in required"
    return None


async def dom_extract(page) -> dict:
    """Fill fields from the rendered DOM where JSON-LD is thin (Amazon's usual
    case). Uses defensive per-field try/except so one missing node never aborts
    the record."""
    rec: dict = {}

    async def text(selector: str) -> str:
        try:
            el = await page.query_selector(selector)
            if el:
                return clean_text(await el.inner_text())
        except Exception:
            return ""
        return ""

    rec["product_name"] = await text("#productTitle")
    price_raw = (await text("#corePrice_feature_div .a-offscreen")
                 or await text("span.a-price span.a-offscreen")
                 or await text("#priceblock_ourprice"))
    if price_raw:
        p, c = parse_price(price_raw)
        rec["price"], rec["currency"] = p, c
    orig = await text("span.a-price.a-text-price span.a-offscreen")
    if orig:
        rec["original_price"], _ = parse_price(orig)
    rec["rating"] = (await text("#acrPopover span.a-icon-alt")).split(" ")[0]
    rec["review_count"] = re.sub(r"[^0-9]", "", await text("#acrCustomerReviewText"))
    rec["brand"] = (await text("#bylineInfo")).replace("Brand:", "").replace("Visit the", "").strip()
    rec["availability"] = await text("#availability span")

    # feature bullets
    try:
        bullets = await page.query_selector_all("#feature-bullets li span.a-list-item")
        feats = [clean_text(await b.inner_text()) for b in bullets]
        feats = [f for f in feats if f]
        if feats:
            rec["features"] = feats
    except Exception:
        pass

    # main image
    try:
        img = await page.query_selector("#landingImage, #imgBlkFront")
        if img:
            src = await img.get_attribute("src")
            if src:
                rec["main_image"] = src
    except Exception:
        pass

    # detail table (brand / color / material / model / weight)
    try:
        rows = await page.query_selector_all(
            "#productDetails_techSpec_section_1 tr, #productDetails_detailBullets_sections1 tr, "
            "table.a-keyvalue tr"
        )
        for row in rows:
            key_el = await row.query_selector("th")
            val_el = await row.query_selector("td")
            if not key_el or not val_el:
                continue
            key = clean_text(await key_el.inner_text()).lower()
            val = clean_text(await val_el.inner_text())
            if not val:
                continue
            if "color" in key and not rec.get("color"):
                rec["color"] = val
            elif "material" in key and not rec.get("material"):
                rec["material"] = val
            elif ("item model number" in key or "model" in key) and not rec.get("model_number"):
                rec["model_number"] = val
            elif "weight" in key and not rec.get("weight"):
                rec["weight"] = val
            elif "manufacturer" in key and not rec.get("brand"):
                rec["brand"] = val
    except Exception:
        pass

    return {k: v for k, v in rec.items() if v}
