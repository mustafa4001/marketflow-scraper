# MarketFlow — Multi-Marketplace Product Scraper

An API-key-free product scraper for **eBay, Etsy, and (best-effort) Amazon**. Enter a
category/search URL, keyword, or a list of product URLs, press Start, and MarketFlow
discovers product links from accessible listing pages, visits each product page,
extracts available fields, and exports them to CSV and JSON.

It uses **browser automation (Playwright + Chromium)** and **public page extraction**
(JSON-LD structured data, OpenGraph, and marketplace-specific selectors). No official
marketplace API keys are required.

---

## Read this first — what actually works

MarketFlow never bypasses CAPTCHA, logins, or anti-bot protections. It detects a block
and stops that request safely. That design choice has direct consequences on where it
runs:

| Marketplace | From your home/residential IP | From a cloud host (Render) |
|-------------|-------------------------------|-----------------------------|
| **eBay**    | Reliable                      | Usually works, occasional blocks |
| **Etsy**    | Mostly works                  | Intermittent (heavy bot detection) |
| **Amazon**  | Flaky                         | **Usually blocked** → returns "access restricted" |

**Amazon on a cloud host will frequently hit a robot check and return few or zero
products. This is expected, not a bug.** If you need reliable Amazon data you must use
paid access (Amazon's official Product Advertising API or a residential-proxy scraping
API). MarketFlow is built eBay/Etsy-first.

All three marketplaces prohibit automated scraping in their Terms of Service regardless
of whether you use their API. Use this tool for small-scale, personal data collection,
understand the ToS exposure, and do not resell scraped marketplace data.

---

## Run it locally (recommended for best results)

Local runs use your own IP, which is less likely to be blocked than a datacenter IP.

```bash
# 1. Install dependencies
pip install -r requirements.txt
python -m playwright install chromium

# 2. Start the server
python main.py
# or: uvicorn main:app --host 0.0.0.0 --port 8000

# 3. Open the dashboard
#    http://localhost:8000
```

---

## Deploy to Render

### 1. Download and extract
Unzip `marketflow.zip` to a folder on your computer.

### 2. Put it on GitHub
```bash
cd marketflow
git init
git add .
git commit -m "MarketFlow initial commit"
# create an empty repo on github.com first, then:
git remote add origin https://github.com/<your-username>/marketflow.git
git branch -M main
git push -u origin main
```
(Or use GitHub Desktop / drag-and-drop upload — any method that gets these files into a repo.)

### 3. Connect GitHub to Render
1. Sign in at https://render.com.
2. **New → Web Service → Build and deploy from a Git repository.**
3. Connect your GitHub account and pick the `marketflow` repo.

### 4. Deploy
Render reads `render.yaml` automatically (Docker runtime, health check at `/api/health`).
Confirm and deploy. The first build is slow — it installs Chromium.

### 5. Open the dashboard
When the deploy is live, open the service URL Render gives you (e.g.
`https://marketflow.onrender.com`). You'll see the MarketFlow dashboard.

---

## How to use the scraper

1. **Pick marketplaces** (eBay and Etsy are checked by default).
2. **Choose an input method:**
   - **Category / Search URL** — paste a listing or search-results URL.
   - **Keyword** — type a term; MarketFlow builds a public search URL per marketplace.
   - **Product URLs** — paste product page URLs, one per line (no discovery/pagination).
3. Set **Max products** and **Max pages**.
4. Press **Start Scraping**. Watch live progress; press **Stop** any time.
5. Use the results table to search, filter by marketplace, sort by price/rating, or remove rows.
6. **Export CSV** or **Export JSON.**

### Example URLs
- `https://www.ebay.com/sch/i.html?_nkw=coffee+grinder`
- `https://www.etsy.com/search?q=coffee+grinder`
- `https://www.amazon.com/s?k=coffee+grinder` (best-effort)

---

## Free-hosting limitations (honest)

Browser automation is heavy. On Render's **free** tier expect:

- **Memory:** Chromium is RAM-hungry. Large jobs can exceed the free instance and get
  killed. Keep Max products modest (≤ 20–30) on free hosting.
- **Sleep:** Free services sleep after inactivity. The first request after sleep is slow,
  and a job can be interrupted if the instance spins down.
- **Execution time:** Long scrapes may be cut short. Smaller, repeated jobs are safer.
- **No persistent storage:** Results live in memory for the job's lifetime. Export before
  the instance sleeps or redeploys. The `exports/` folder is not used for durable storage.

For heavier use, run locally or move to a paid instance with ≥ 1–2 GB RAM.

---

## Extracted fields (CSV column order)

`time_scraped, product_url, product_name, asin, brand, price, original_price, rating,
review_count, description, features, main_image, images, gtin, upc, color, model_number,
currency, availability, seller_name, weight, weight_unit, material, badges, shipping_info`

Only fields present on the page or in its structured data are filled. Missing fields are
left blank — never fabricated.

---

## Project structure

```
marketflow/
├── main.py                 # FastAPI app + endpoints + static serving
├── requirements.txt
├── Dockerfile
├── render.yaml
├── README.md
├── scraper/
│   ├── utils.py            # URL validation/SSRF guard, parsing, CSV, dedup
│   ├── extraction.py       # JSON-LD / OpenGraph / meta engine + normalization
│   ├── browser.py          # Playwright wrapper + block detection
│   ├── discovery.py        # product-link discovery
│   ├── pagination.py       # next-page detection
│   ├── adapters.py         # marketplace registry
│   ├── amazon.py / ebay.py / etsy.py   # per-marketplace adapters
│   └── jobs.py             # background job manager / orchestration
├── static/                 # dashboard (index.html, style.css, app.js)
├── tests/                  # offline unit tests (extraction, utils, CSV)
└── exports/
```

## API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/scrape/start` | Start a job, returns `job_id` |
| POST | `/api/scrape/stop` | Request a graceful stop |
| GET | `/api/scrape/status?job_id=` | Progress snapshot (poll this) |
| GET | `/api/scrape/results?job_id=` | Collected records |
| POST | `/api/export/csv` | Download CSV |
| POST | `/api/export/json` | Download JSON |
| GET | `/api/health` | Health check |

## Tests

Offline tests (no network/browser) cover extraction, parsing, CSV escaping, URL
validation, and dedup:

```bash
pip install pytest
pytest -q
```
