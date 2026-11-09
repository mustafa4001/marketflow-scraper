"""Next-page detection. Each marketplace uses different selectors, so this just
delegates to the adapter and guards against loops."""
from __future__ import annotations

from .adapters import get_adapter


def next_page_url(html: str, marketplace: str, current_url: str, seen: set[str]) -> str | None:
    adapter = get_adapter(marketplace)
    if not adapter:
        return None
    nxt = adapter.next_page(html, current_url)
    if not nxt or nxt in seen or nxt == current_url:
        return None
    return nxt
