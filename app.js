"use strict";

const $ = (id) => document.getElementById(id);
const state = { jobId: null, poll: null, results: [], removed: new Set() };

// ---- connection indicator ----
async function checkHealth() {
  try {
    const r = await fetch("/api/health");
    const ok = r.ok;
    $("connDot").className = "dot " + (ok ? "live" : "down");
    $("connText").textContent = ok ? "Connected" : "Backend down";
  } catch {
    $("connDot").className = "dot down";
    $("connText").textContent = "Backend down";
  }
}

// ---- input method tabs ----
let method = "category_url";
document.querySelectorAll("#methodTabs button").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll("#methodTabs button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    method = b.dataset.method;
    document.querySelectorAll(".method-pane").forEach((p) => {
      p.classList.toggle("hidden", p.dataset.pane !== method);
    });
  });
});

function selectedMarkets() {
  return [...document.querySelectorAll(".checks input:checked")].map((c) => c.value);
}

function showError(msg) {
  const el = $("formError");
  el.textContent = msg;
  el.classList.toggle("hidden", !msg);
}

// ---- start / stop ----
async function startScrape() {
  showError("");
  const markets = selectedMarkets();
  if (!markets.length) return showError("Select at least one marketplace.");

  const body = {
    marketplaces: markets,
    input_method: method,
    url: $("urlInput").value,
    keyword: $("keywordInput").value,
    product_urls: $("urlsInput").value.split("\n").map((s) => s.trim()).filter(Boolean),
    max_products: parseInt($("maxProducts").value) || 20,
    max_pages: parseInt($("maxPages").value) || 3,
  };

  try {
    const r = await fetch("/api/scrape/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) return showError(data.detail || "Could not start scrape.");
    state.jobId = data.job_id;
    state.results = [];
    state.removed = new Set();
    $("startBtn").disabled = true;
    $("stopBtn").disabled = false;
    startPolling();
  } catch {
    showError("Network error contacting backend.");
  }
}

async function stopScrape() {
  if (!state.jobId) return;
  $("stopBtn").disabled = true;
  try {
    await fetch("/api/scrape/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: state.jobId }),
    });
  } catch {}
}

// ---- polling ----
function startPolling() {
  stopPolling();
  state.poll = setInterval(pollStatus, 1500);
  pollStatus();
}
function stopPolling() {
  if (state.poll) clearInterval(state.poll);
  state.poll = null;
}

async function pollStatus() {
  if (!state.jobId) return;
  try {
    const r = await fetch("/api/scrape/status?job_id=" + state.jobId);
    if (!r.ok) return;
    const s = await r.json();
    renderProgress(s);
    if (["done", "stopped", "error"].includes(s.status)) {
      stopPolling();
      $("startBtn").disabled = false;
      $("stopBtn").disabled = true;
      await loadResults();
    }
  } catch {}
}

function renderProgress(s) {
  $("pMarket").textContent = s.current_marketplace || "—";
  $("pPage").textContent = s.current_page;
  $("pDiscovered").textContent = s.discovered;
  $("pProcessed").textContent = s.processed;
  $("pSuccess").textContent = s.success;
  $("pFailed").textContent = s.failed;
  $("statusMsg").textContent = s.message || s.status;

  const dot = $("statusDot");
  dot.className = "statusdot " + (s.status === "running" ? "run" : s.status === "error" ? "err" : (s.status === "done" || s.status === "stopped") ? "done" : "");

  const target = Math.max(s.discovered, 1);
  $("barFill").style.width = Math.min(100, Math.round((s.processed / target) * 100)) + "%";

  $("sTotal").textContent = s.total_results;
  $("sSuccess").textContent = s.success;
  $("sFailed").textContent = s.failed;
  $("sAmazon").textContent = s.counts.amazon || 0;
  $("sEbay").textContent = s.counts.ebay || 0;
  $("sEtsy").textContent = s.counts.etsy || 0;
}

// ---- results ----
async function loadResults() {
  try {
    const r = await fetch("/api/scrape/results?job_id=" + state.jobId);
    const data = await r.json();
    state.results = data.results || [];
    renderTable();
  } catch {}
}

function marketOf(url) {
  if (/amazon\./.test(url)) return "amazon";
  if (/ebay\./.test(url)) return "ebay";
  if (/etsy\./.test(url)) return "etsy";
  return "";
}

function numeric(v) {
  const n = parseFloat(String(v || "").replace(/[^0-9.]/g, ""));
  return isNaN(n) ? null : n;
}

function visibleRows() {
  const q = $("searchBox").value.toLowerCase();
  const mf = $("filterMarket").value;
  let rows = state.results
    .map((rec, i) => ({ rec, i }))
    .filter(({ i }) => !state.removed.has(i))
    .filter(({ rec }) => {
      const mk = marketOf(rec.product_url);
      if (mf && mk !== mf) return false;
      if (q) {
        const hay = (rec.product_name + " " + rec.brand).toLowerCase();
        return hay.includes(q);
      }
      return true;
    });

  const sort = $("sortBy").value;
  if (sort === "price_asc") rows.sort((a, b) => (numeric(a.rec.price) ?? 1e15) - (numeric(b.rec.price) ?? 1e15));
  if (sort === "price_desc") rows.sort((a, b) => (numeric(b.rec.price) ?? -1) - (numeric(a.rec.price) ?? -1));
  if (sort === "rating_desc") rows.sort((a, b) => (numeric(b.rec.rating) ?? -1) - (numeric(a.rec.rating) ?? -1));
  return rows;
}

function esc(s) {
  return String(s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function renderTable() {
  const body = $("resultsBody");
  const rows = visibleRows();
  if (!rows.length) {
    body.innerHTML = '<tr class="empty"><td colspan="11">No products to show.</td></tr>';
    return;
  }
  body.innerHTML = rows
    .map(({ rec, i }) => {
      const mk = marketOf(rec.product_url);
      const img = rec.main_image ? `<img src="${esc(rec.main_image)}" alt="" onerror="this.style.visibility='hidden'">` : "";
      const status = rec.product_name ? '<span class="status-ok">OK</span>' : '<span class="status-bad">partial</span>';
      return `<tr>
        <td>${img}</td>
        <td class="name">${esc(rec.product_name) || "—"}</td>
        <td>${mk ? `<span class="market-tag market-${mk}">${mk}</span>` : "—"}</td>
        <td>${esc(rec.price) || "—"}</td>
        <td>${esc(rec.original_price) || "—"}</td>
        <td>${esc(rec.rating) || "—"}</td>
        <td>${esc(rec.review_count) || "—"}</td>
        <td>${esc(rec.brand) || "—"}</td>
        <td><a class="url-link" href="${esc(rec.product_url)}" target="_blank" rel="noopener">open ↗</a></td>
        <td>${status}</td>
        <td><button class="row-del" data-i="${i}" title="Remove">✕</button></td>
      </tr>`;
    })
    .join("");

  body.querySelectorAll(".row-del").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.removed.add(parseInt(btn.dataset.i));
      renderTable();
    });
  });
}

// ---- export ----
async function exportFile(kind) {
  if (!state.jobId) return;
  try {
    const r = await fetch("/api/export/" + kind, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: state.jobId }),
    });
    if (!r.ok) return;
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `marketflow_${state.jobId}.${kind}`;
    a.click();
    URL.revokeObjectURL(a.href);
  } catch {}
}

// ---- wiring ----
$("startBtn").addEventListener("click", startScrape);
$("stopBtn").addEventListener("click", stopScrape);
$("searchBox").addEventListener("input", renderTable);
$("filterMarket").addEventListener("change", renderTable);
$("sortBy").addEventListener("change", renderTable);
$("clearBtn").addEventListener("click", () => {
  state.results = [];
  state.removed = new Set();
  renderTable();
});
$("exportCsv").addEventListener("click", () => exportFile("csv"));
$("exportJson").addEventListener("click", () => exportFile("json"));

checkHealth();
setInterval(checkHealth, 20000);
