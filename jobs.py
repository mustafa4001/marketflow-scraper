"""Background job manager.

Holds job state in memory, runs the scrape as an asyncio task, and exposes a
progress snapshot the frontend can poll. A single failed product never kills the
job; a blocked listing page stops that marketplace's crawl safely.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from .adapters import get_adapter
from .browser import Browser, BlockedError, detect_block
from .discovery import discover_product_links
from .extraction import extract_static, merge_fields, normalize_record
from .pagination import next_page_url
from .utils import (
    build_search_url, detect_marketplace, dedupe_preserve_order, now_iso,
    validate_and_normalize_url,
)

MAX_PRODUCTS_CAP = 500   # hard ceiling regardless of user input
MAX_PAGES_CAP = 50
DEFAULT_CONCURRENCY = 3


@dataclass
class Job:
    id: str
    config: dict
    status: str = "queued"            # queued | running | done | stopped | error
    current_marketplace: str = ""
    current_page: int = 0
    discovered: int = 0
    processed: int = 0
    success: int = 0
    failed: int = 0
    message: str = ""
    errors: list = field(default_factory=list)
    results: list = field(default_factory=list)
    counts: dict = field(default_factory=lambda: {"amazon": 0, "ebay": 0, "etsy": 0})
    stop_requested: bool = False
    started_at: float = 0.0
    finished_at: float = 0.0

    def snapshot(self) -> dict:
        return {
            "job_id": self.id,
            "status": self.status,
            "current_marketplace": self.current_marketplace,
            "current_page": self.current_page,
            "discovered": self.discovered,
            "processed": self.processed,
            "success": self.success,
            "failed": self.failed,
            "message": self.message,
            "counts": self.counts,
            "total_results": len(self.results),
            "errors": self.errors[-20:],
        }


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def create(self, config: dict) -> Job:
        job = Job(id=uuid.uuid4().hex, config=config)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def start(self, job: Job):
        task = asyncio.create_task(self._run(job))
        self._tasks[job.id] = task

    def request_stop(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.stop_requested = True
        job.message = "Stop requested; finishing current item..."
        return True

    # --- orchestration ---------------------------------------------------------

    def _seed_urls(self, job: Job) -> list[tuple[str, str]]:
        """Return (marketplace, url) seeds for listing-based methods."""
        cfg = job.config
        method = cfg["input_method"]
        seeds: list[tuple[str, str]] = []
        if method == "category_url":
            url = validate_and_normalize_url(cfg["url"])
            mk = detect_marketplace(url)
            if mk in cfg["marketplaces"]:
                seeds.append((mk, url))
        elif method == "keyword":
            for mk in cfg["marketplaces"]:
                seeds.append((mk, build_search_url(mk, cfg["keyword"])))
        return seeds

    async def _run(self, job: Job):
        job.status = "running"
        job.started_at = time.time()
        max_products = min(int(job.config.get("max_products", 20)), MAX_PRODUCTS_CAP)
        max_pages = min(int(job.config.get("max_pages", 3)), MAX_PAGES_CAP)
        concurrency = int(job.config.get("concurrency", DEFAULT_CONCURRENCY))
        method = job.config["input_method"]

        try:
            async with Browser(headless=True) as browser:
                product_urls: list[tuple[str, str]] = []  # (marketplace, url)

                if method == "product_urls":
                    for raw in job.config.get("product_urls", []):
                        try:
                            url = validate_and_normalize_url(raw)
                        except ValueError as e:
                            job.errors.append({"url": raw, "reason": str(e)})
                            continue
                        mk = detect_marketplace(url)
                        if mk in job.config["marketplaces"]:
                            product_urls.append((mk, url))
                    product_urls = dedupe_preserve_order(product_urls)
                    job.discovered = len(product_urls)
                else:
                    product_urls = await self._discover(job, browser, max_products, max_pages)

                if job.stop_requested:
                    return self._finish(job, "stopped")

                await self._process_products(job, browser, product_urls, max_products, concurrency)

            self._finish(job, "stopped" if job.stop_requested else "done")
        except Exception as e:  # never leak a stack trace to the client
            job.status = "error"
            job.message = "Scrape failed due to an internal error."
            job.errors.append({"reason": f"{type(e).__name__}"})
            job.finished_at = time.time()

    async def _discover(self, job, browser, max_products, max_pages) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for marketplace, seed in self._seed_urls(job):
            if job.stop_requested:
                break
            job.current_marketplace = marketplace
            seen_pages: set[str] = set()
            url = seed
            page_num = 0
            while url and page_num < max_pages and len(found) < max_products:
                if job.stop_requested:
                    break
                page_num += 1
                job.current_page = page_num
                job.message = f"Reading {marketplace} listing page {page_num}"
                try:
                    result = await browser.fetch(url)
                except (PlaywrightTimeout, PlaywrightError):
                    job.errors.append({"url": url, "reason": "listing page timed out"})
                    break
                block = detect_block(result, marketplace)
                if block:
                    job.errors.append({"url": url, "reason": block})
                    job.message = f"{marketplace} access restricted: {block}"
                    break  # stop this marketplace safely
                links = discover_product_links(result.html, marketplace, result.final_url)
                for link in links:
                    pair = (marketplace, link)
                    if pair not in found:
                        found.append(pair)
                        job.discovered = len(found)
                        if len(found) >= max_products:
                            break
                seen_pages.add(url)
                url = next_page_url(result.html, marketplace, result.final_url, seen_pages)
        return found[:max_products]

    async def _process_products(self, job, browser, product_urls, max_products, concurrency):
        targets = product_urls[:max_products]
        sem = asyncio.Semaphore(max(1, concurrency))

        async def worker(marketplace: str, url: str):
            async with sem:
                if job.stop_requested:
                    return
                job.message = f"Extracting {marketplace} product"
                try:
                    record = await self._extract_one(browser, marketplace, url)
                    job.results.append(record)
                    job.success += 1
                    job.counts[marketplace] = job.counts.get(marketplace, 0) + 1
                except BlockedError as e:
                    job.failed += 1
                    job.errors.append({"url": url, "reason": str(e)})
                except (PlaywrightTimeout, PlaywrightError):
                    job.failed += 1
                    job.errors.append({"url": url, "reason": "product page timed out"})
                except Exception:
                    job.failed += 1
                    job.errors.append({"url": url, "reason": "extraction error"})
                finally:
                    job.processed += 1

        await asyncio.gather(*(worker(mk, u) for mk, u in targets))

    async def _extract_one(self, browser, marketplace, url, *, retries: int = 1) -> dict:
        adapter = get_adapter(marketplace)
        attempt = 0
        while True:
            page = None
            try:
                page, result = await browser.fetch_page_obj(url)
                block = detect_block(result, marketplace)
                if block:
                    raise BlockedError(block)
                static = extract_static(result.html, result.final_url)
                dom = {}
                if adapter and hasattr(adapter, "dom_extract"):
                    dom = await adapter.dom_extract(page)
                # JSON-LD/OG/meta first (highest priority), selectors fill blanks.
                merged = merge_fields(static, dom)
                return normalize_record(merged, result.final_url, marketplace, now_iso())
            except (PlaywrightTimeout, PlaywrightError):
                if attempt < retries:
                    attempt += 1
                    await asyncio.sleep(1.0)
                    continue
                raise
            finally:
                if page is not None:
                    try:
                        await page.close()
                    except Exception:
                        pass

    def _finish(self, job: Job, status: str):
        job.status = status
        job.finished_at = time.time()
        if status == "done":
            job.message = f"Completed: {job.success} extracted, {job.failed} failed."
        elif status == "stopped":
            job.message = f"Stopped: {job.success} extracted before stop."


manager = JobManager()
