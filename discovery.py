"""Discover individual product links from a listing/search page."""
from __future__ import annotations

from .adapters import get_adapter
from .utils import dedupe_preserve_order


def discover_product_links(html: str, marketplace: str, base_url: str) -> list[str]:
    adapter = get_adapter(marketplace)
    if not adapter:
        return []
    links = adapter.extract_listing_links(html, base_url)
    return dedupe_preserve_order(links)


def is_product_url(url: str, marketplace: str) -> bool:
    adapter = get_adapter(marketplace)
    return bool(adapter and adapter.is_product_url(url))
