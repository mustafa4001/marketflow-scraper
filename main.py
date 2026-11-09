"""MarketFlow API — FastAPI backend.

Serves the dashboard and drives the scraper job manager. All validation happens
here before any browser is launched; internal errors are never returned verbatim
to the client.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from scraper.jobs import MAX_PAGES_CAP, MAX_PRODUCTS_CAP, manager
from scraper.utils import detect_marketplace, is_allowed_url, to_csv

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="MarketFlow", version="1.0.0")

VALID_MARKETPLACES = {"amazon", "ebay", "etsy"}


class StartRequest(BaseModel):
    marketplaces: list[str] = Field(default_factory=list)
    input_method: str  # category_url | keyword | product_urls
    url: str = ""
    keyword: str = ""
    product_urls: list[str] = Field(default_factory=list)
    max_products: int = 20
    max_pages: int = 3
    concurrency: int = 3

    @field_validator("marketplaces")
    @classmethod
    def _valid_markets(cls, v):
        v = [m.lower().strip() for m in v if m.strip()]
        bad = [m for m in v if m not in VALID_MARKETPLACES]
        if bad:
            raise ValueError(f"unsupported marketplace(s): {', '.join(bad)}")
        return v

    @field_validator("max_products")
    @classmethod
    def _cap_products(cls, v):
        return max(1, min(int(v), MAX_PRODUCTS_CAP))

    @field_validator("max_pages")
    @classmethod
    def _cap_pages(cls, v):
        return max(1, min(int(v), MAX_PAGES_CAP))

    @field_validator("concurrency")
    @classmethod
    def _cap_conc(cls, v):
        return max(1, min(int(v), 6))


class JobRef(BaseModel):
    job_id: str


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "marketflow"}


@app.post("/api/scrape/start")
async def scrape_start(req: StartRequest):
    if not req.marketplaces:
        raise HTTPException(400, "Select at least one marketplace.")
    method = req.input_method
    if method not in ("category_url", "keyword", "product_urls"):
        raise HTTPException(400, "Invalid input method.")

    if method == "category_url":
        if not req.url.strip():
            raise HTTPException(400, "Provide a category or search-results URL.")
        if not is_allowed_url(req.url):
            raise HTTPException(400, "URL is not a supported/allowed marketplace URL.")
        mk = detect_marketplace(req.url)
        if mk not in req.marketplaces:
            raise HTTPException(400, f"URL is for {mk}, which is not selected.")
    elif method == "keyword":
        if not req.keyword.strip():
            raise HTTPException(400, "Provide a search keyword.")
    elif method == "product_urls":
        cleaned = [u.strip() for u in req.product_urls if u.strip()]
        if not cleaned:
            raise HTTPException(400, "Provide at least one product URL.")
        if not any(is_allowed_url(u) for u in cleaned):
            raise HTTPException(400, "None of the product URLs are supported marketplace URLs.")
        req.product_urls = cleaned

    job = manager.create(req.model_dump())
    manager.start(job)
    return {"job_id": job.id, "status": job.status}


@app.post("/api/scrape/stop")
async def scrape_stop(ref: JobRef):
    if not manager.request_stop(ref.job_id):
        raise HTTPException(404, "Unknown job id.")
    return {"job_id": ref.job_id, "status": "stopping"}


@app.get("/api/scrape/status")
async def scrape_status(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job id.")
    return job.snapshot()


@app.get("/api/scrape/results")
async def scrape_results(job_id: str):
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job id.")
    return {"job_id": job.id, "count": len(job.results), "results": job.results}


@app.post("/api/export/csv")
async def export_csv(ref: JobRef):
    job = manager.get(ref.job_id)
    if not job:
        raise HTTPException(404, "Unknown job id.")
    csv_text = to_csv(job.results)
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="marketflow_{job.id}.csv"'},
    )


@app.post("/api/export/json")
async def export_json(ref: JobRef):
    job = manager.get(ref.job_id)
    if not job:
        raise HTTPException(404, "Unknown job id.")
    payload = json.dumps(job.results, ensure_ascii=False, indent=2)
    return Response(
        payload,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="marketflow_{job.id}.json"'},
    )


# Serve the dashboard. Mounted last so /api routes take precedence.
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
