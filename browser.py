"""Playwright Chromium wrapper.

Responsible for opening pages, returning rendered HTML, and detecting when a
marketplace has blocked us (CAPTCHA / sign-in / access denied). We never try to
solve or bypass a block; we surface it and stop that request.
"""
from __future__ import annotations

from dataclasses import dataclass

from playwright.async_api import async_playwright

from .adapters import get_adapter

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Generic block signals checked for every marketplace, in addition to the
# adapter-specific detect_block().
_GENERIC_SIGNALS = (
    "access denied",
    "request blocked",
    "unusual traffic",
    "are you a human",
    "verify you are a human",
)


class BlockedError(Exception):
    """Raised when a page is blocked, gated, or CAPTCHA-protected."""


@dataclass
class FetchResult:
    html: str
    final_url: str
    status: int
    title: str


def detect_block(result: FetchResult, marketplace: str | None) -> str | None:
    if result.status in (403, 429, 503):
        # Could still be a soft page; fall through to content checks too.
        hard = f"HTTP {result.status} from marketplace"
    else:
        hard = None
    hay = (result.html or "").lower()
    for sig in _GENERIC_SIGNALS:
        if sig in hay:
            return f"Access restricted: '{sig}'"
    adapter = get_adapter(marketplace) if marketplace else None
    if adapter and hasattr(adapter, "detect_block"):
        reason = adapter.detect_block(result.html, result.title, result.final_url)
        if reason:
            return reason
    return hard


class Browser:
    """Async context manager owning one Chromium browser + context."""

    def __init__(self, *, headless: bool = True, timeout_ms: int = 30000):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._pw = None
        self._browser = None
        self._context = None

    async def __aenter__(self):
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        self._context = await self._browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        self._context.set_default_timeout(self.timeout_ms)
        return self

    async def __aexit__(self, *exc):
        # Close in reverse order; swallow shutdown errors.
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
        ):
            try:
                if closer:
                    await closer()
            except Exception:
                pass
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass

    async def fetch(self, url: str, *, wait: str = "domcontentloaded") -> FetchResult:
        """Open a page and return its rendered HTML. Always closes the page."""
        page = await self._context.new_page()
        try:
            response = await page.goto(url, wait_until=wait, timeout=self.timeout_ms)
            status = response.status if response else 0
            html = await page.content()
            title = await page.title()
            return FetchResult(html=html, final_url=page.url, status=status, title=title)
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def fetch_page_obj(self, url: str, *, wait: str = "domcontentloaded"):
        """Open a page and return (page, FetchResult) WITHOUT closing it, so a
        marketplace adapter can run DOM selectors. Caller must close the page."""
        page = await self._context.new_page()
        response = await page.goto(url, wait_until=wait, timeout=self.timeout_ms)
        status = response.status if response else 0
        html = await page.content()
        title = await page.title()
        return page, FetchResult(html=html, final_url=page.url, status=status, title=title)
