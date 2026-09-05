"use strict";

const state = {
  attentionItems: [],
  recommendations: [],
  storesMap: {},
  health: null,
  dashboard: null,
  geminiAvailable: false,
  user: { role: "ADMIN", user_id: "guest" },
};

const COLORS = ["#38bdf8", "#818cf8", "#34d399", "#fbbf24", "#f87171",
  "#c084fc", "#2dd4bf", "#fb923c", "#a3e635", "#f472b6", "#60a5fa", "#fde047"];

const fmtMoney = (v) => {
  const n = Number(v) || 0;
  return n >= 1000000 ? "$" + (n / 1000000).toFixed(2) + "M"
    : n >= 1000 ? "$" + (n / 1000).toFixed(1) + "K"
    : "$" + n.toLocaleString(undefined, { maximumFractionDigits: 0 });
};
const fmtNum = (v, d = 0) => {
  const n = Number(v);
  if (isNaN(n)) return "–";
  return n.toLocaleString(undefined, { maximumFractionDigits: d });
};
const pct = (v) => {
  const n = Number(v);
  if (isNaN(n)) return "–";
  return (n > 0 ? "+" : "") + n.toFixed(1) + "%";
};

async function api(path, opts) {
  const o = Object.assign({}, opts);
  const r = await fetch(path, o);
  if (!r.ok) {
    let detail = r.status + " " + path;
    try {
      const j = await r.json();
      if (j && j.detail) detail = j.detail;
    } catch (e) { /* non-JSON error body */ }
    throw new Error(detail);
  }
  return r.json();
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------
function navigate(view) {
  document.querySelectorAll(".nav-link").forEach((n) => n.classList.remove("active"));
  const link = document.querySelector('.nav-link[data-view="' + view + '"]');
  if (link) link.classList.add("active");
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  document.getElementById("view-" + view).classList.add("active");
  if (view === "attention") loadAttention();
  if (view === "products") loadProducts("");
  if (view === "stores") loadStores();
  if (view === "sales") loadSalesTrend();
  if (view === "import") loadImport();
  if (view === "evidence") loadEvidence();
}

function bindNav() {
  document.querySelectorAll(".nav-link").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      navigate(a.dataset.view);
    });
  });
}

// ---------------------------------------------------------------------------
// SVG helpers
// ---------------------------------------------------------------------------
function svgEl(tag, attrs, parent) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(el);
  return el;
}

function clearChart(el) {
  el.innerHTML = "";
}

function lineChart(elId, data, series, opts = {}) {
  const el = document.getElementById(elId);
  clearChart(el);
  if (!data || data.length === 0) { el.innerHTML = "<div class='hint'>No data</div>"; return; }
  const W = el.clientWidth || 640, H = opts.height || 260;
  const pad = { l: 46, r: 14, t: 14, b: 26 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;

  let maxY = 0;
  data.forEach((d) => series.forEach((s) => { maxY = Math.max(maxY, Number(d[s.key])); }));
  maxY = maxY * 1.08 || 1;

  const svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, "aria-label": opts.title || "chart" }, document.createElement("div"));
  el.appendChild(svg);

  const grid = svgEl("g", { class: "grid" }, svg);
  const ticks = 4;
  for (let i = 0; i <= ticks; i++) {
    const y = pad.t + ih - (ih * i / ticks);
    svgEl("line", { x1: pad.l, x2: W - pad.r, y1: y, y2: y }, grid);
    const lab = maxY * i / ticks;
    const t = svgEl("text", { x: pad.l - 8, y: y + 3, "text-anchor": "end", class: "axis" }, grid);
    t.textContent = opts.yFmt ? opts.yFmt(lab) : fmtNum(lab);
  }

  const xStep = iw / (data.length - 1 || 1);
  const nXTicks = Math.min(data.length, 8);
  for (let i = 0; i < data.length; i++) {
    const skip = i % (data.length < 12 ? 1 : Math.ceil(data.length / nXTicks)) !== 0 && i !== data.length - 1;
    if (skip) continue;
    const x = pad.l + i * xStep;
    const t = svgEl("text", { x: x, y: H - 8, "text-anchor": "middle", class: "axis" }, svg);
    const raw = data[i][opts.x || "date"] || "";
    t.textContent = typeof raw === "string" ? raw.slice(5) : raw;
  }

  series.forEach((s, si) => {
    const pts = data.map((d, i) => {
      const x = pad.l + i * xStep;
      const y = pad.t + ih - (Number(d[s.key]) / maxY) * ih;
      return x + "," + y;
    });
    if (s.area && si === 0) {
      const path = "M" + pad.l + "," + (pad.t + ih) + " L" + pts.join(" L") + " L" + (pad.l + (data.length - 1) * xStep) + "," + (pad.t + ih) + " Z";
      svgEl("path", { d: path, fill: s.color, opacity: 0.12 }, svg);
    }
    svgEl("polyline", { points: pts.join(" "), fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round" }, svg);
  });

  if (series.length > 1) {
    const lg = svgEl("g", { class: "legend" }, svg);
    series.forEach((s, i) => {
      const x = pad.l + i * 110;
      svgEl("circle", { cx: x, cy: pad.t - 5, r: 4, fill: s.color }, lg);
      const t = svgEl("text", { x: x + 8, y: pad.t - 1 }, lg);
      t.textContent = s.label;
    });
  }
}

function barList(elId, items, opts = {}) {
  const el = document.getElementById(elId);
  clearChart(el);
  if (!items || items.length === 0) { el.innerHTML = "<div class='hint'>No data</div>"; return; }
  const max = Math.max.apply(null, items.map((it) => Number(it.value) || 0)) || 1;
  const svg = svgEl("svg", { viewBox: "0 0 640 " + (items.length * 26 + 16), "aria-label": opts.title || "bars" }, document.createElement("div"));
  el.appendChild(svg);
  items.forEach((it, i) => {
    const y = 14 + i * 26;
    const w = (Number(it.value) / max) * 470;
    svgEl("rect", { x: 8, y: y - 12, width: Math.max(w, 2), height: 16, rx: 3, fill: it.color || opts.color || COLORS[i % COLORS.length] }, svg);
    const t = svgEl("text", { x: 8, y: y, class: "axis", "font-size": 11 }, svg);
    t.textContent = it.label;
    const tv = svgEl("text", { x: Math.min(8 + w, 520) + 6, y: y, class: "axis", "font-size": 11, fill: "#e6ecf7" }, svg);
    tv.textContent = it.display != null ? it.display : fmtNum(it.value) + (it.suffix || "");
  });
}

function donut(elId, items, opts = {}) {
  const el = document.getElementById(elId);
  clearChart(el);
  const total = items.reduce((a, b) => a + (Number(b.value) || 0), 0);
  if (!total) { el.innerHTML = "<div class='hint'>No data</div>"; return; }
  const svg = svgEl("svg", { viewBox: "0 0 220 220", "aria-label": opts.title || "donut" }, document.createElement("div"));
  el.appendChild(svg);
  const R = 80, C = 2 * Math.PI * R;
  let off = 0;
  items.forEach((it, i) => {
    const frac = (Number(it.value) || 0) / total;
    const len = frac * C;
    const circ = svgEl("circle", {
      cx: 110, cy: 110, r: R, fill: "none",
      stroke: it.color || COLORS[i % COLORS.length],
      "stroke-width": 24, "stroke-dasharray": len + " " + (C - len),
      "stroke-dashoffset": -off, "transform": "rotate(-90 110 110)",
    }, svg);
    off += len;
  });
  const c = svgEl("circle", { cx: 110, cy: 110, r: 52, fill: "#111a2c" }, svg);
  const t = svgEl("text", { x: 110, y: 106, "text-anchor": "middle", fill: "#e6ecf7", "font-size": 20, "font-weight": 700 }, svg);
  t.textContent = opts.centerValue || fmtNum(total);
  const tl = svgEl("text", { x: 110, y: 124, "text-anchor": "middle", fill: "#8fa0bd", "font-size": 10 }, svg);
  tl.textContent = opts.centerLabel || "total";

  let ty = 42;
  items.forEach((it, i) => {
    svgEl("circle", { cx: 248, cy: ty - 4, r: 5, fill: it.color || COLORS[i % COLORS.length] }, svg);
    const t2 = svgEl("text", { x: 260, y: ty, class: "axis", "font-size": 11 }, svg);
    const share = ((Number(it.value) || 0) / total * 100).toFixed(1);
    t2.textContent = it.label + "  " + share + "%";
    ty += 19;
  });
  // widen svg to fit legend by adjusting viewBox min-x
  svg.setAttribute("viewBox", "-20 0 560 220");
}

// ---------------------------------------------------------------------------
// Health + status
// ---------------------------------------------------------------------------
async function initHealth() {
  try {
    const h = await api("/api/health");
    state.geminiAvailable = !!h.gemini_available;
    const dot = document.getElementById("statusDot");
    dot.classList.add("ok");
    document.getElementById("statusText").textContent =
      "DB ok · " + fmtNum(h.database.metrics_snapshot || 0) + " pairs · as of " + (h.as_of_date || "");
    document.getElementById("asofDate").textContent = "As of " + (h.as_of_date || "");
    const badge = document.getElementById("aiBadge");
    if (badge) {
      badge.textContent = h.gemini_available ? "LLM narration enabled" : "Deterministic engine (offline)";
      if (h.gemini_available) badge.classList.add("gem");
    }
  } catch (e) {
    document.getElementById("statusDot").classList.add("err");
    document.getElementById("statusText").textContent = "Cannot reach backend";
  }
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------
async function loadOverview() {
  const d = await api("/api/dashboard");
  state.dashboard = d;
  state.storesMap = Object.fromEntries((d.stores || []).map((s) => [s.store_id, s]));
  renderKpis(d.kpis);
  const catSel = document.getElementById("trendCategory");
  if (catSel) {
    catSel.innerHTML = '<option value="">All categories</option>' +
      (d.categories || []).map((c) => `<option value="${escapeHtml(c.category)}">${escapeHtml(c.category)}</option>`).join("");
  }
  lineChart("trendChart", d.trend, [
    { key: "revenue", color: "#38bdf8", label: "Revenue", area: true },
    { key: "units", color: "#818cf8", label: "Units" },
  ], { height: 240, yFmt: fmtMoney });
  donut("categoryChart", (d.categories || []).slice(0, 8).map((c) => ({
    label: c.category, value: c.revenue,
  })), { centerValue: fmtMoney(d.categories.reduce((a, b) => a + Number(b.revenue), 0)), centerLabel: "revenue" });
  barList("topProductsChart", (d.top_products || []).slice(0, 8).map((p) => ({
    label: p.product_name, value: Number(p.revenue), display: fmtMoney(p.revenue),
  })), { title: "Top products" });
  renderAttentionPreview(d.attention || []);
  state.attentionItems = d.attention || [];
  state.recommendations = d.attention || [];
}

function renderKpis(k) {
  const cards = [
    { label: "Revenue 30d", value: fmtMoney(k.revenue_30d), delta: pct(k.revenue_change_pct) },
    { label: "Units 30d", value: fmtNum(k.units_30d), delta: pct(k.units_change_pct) },
    { label: "Margin 30d", value: fmtMoney(k.margin_30d), delta: null },
    { label: "Inventory value", value: fmtMoney(k.inventory_value), delta: null },
    { label: "Critical stock-out", value: fmtNum(k.stockout_critical), delta: null, cls: "alert-tint" },
    { label: "Overstock items", value: fmtNum(k.overstock_items), delta: null, cls: "hot-tint" },
    { label: "Slow-moving", value: fmtNum(k.slow_moving_items + (k.dead_stock_items || 0)), delta: null },
    { label: "Product-store pairs", value: fmtNum(k.product_store_pairs), delta: null },
  ];
  document.getElementById("kpiGrid").innerHTML = cards.map((c, i) => `
    <div class="kpi ${c.cls || ""}">
      <div class="kpi-label">${c.label}<span>#${i + 1}</span></div>
      <div class="kpi-value">${c.value}</div>
      ${c.delta ? `<div class="kpi-delta ${Number(c.delta) >= 0 && !c.delta.startsWith("-") ? "delta-up" : "delta-down"}">${c.delta} vs prev 30d</div>` : ""}
    </div>`).join("");
}

const SEV_DOT = { CRITICAL: "🔴", HIGH: "🟠", MEDIUM: "🟡", LOW: "🔵" };

function attentionItemHTML(it) {
  const dot = SEV_DOT[it.priority] || "🔵";
  const meta = it.issue_type === "DEAD_STOCK" || it.issue_type === "SLOW_MOVING"
    ? it.category + " · " + it.store_id
    : (it.metrics && it.metrics.current_stock != null ? it.store_id + " · stock " + fmtNum(it.metrics.current_stock) : it.store_id + " · " + it.category);
  return `
    <div class="att sev-${it.priority}">
      <div class="att-main">
        <div class="issue-type"><span class="dot">${dot}</span>${it.issue_type.replace(/_/g, " ")}
          <span class="pill pill-${it.priority}">${it.priority}</span></div>
        <div class="att-reason">${escapeHtml(it.reason)}</div>
        <div class="att-meta">${escapeHtml(it.product_name)} · ${meta}</div>
        ${attentionExplHTML(it)}
      </div>
      <button class="btn-why" data-issue="${it.issue_id}">Why?</button>
    </div>`;
}

function attentionExplHTML(it) {
  if (!it.explanation) return "";
  const src = it.llm_used ? "Gemini" : "rules";
  const canAsk = state.geminiAvailable && !it.llm_used && it.store_id && it.product_id;
  const signal = it.signal || (it.issue_type === "SALES_DROP" ? "DROP"
    : it.issue_type === "SALES_SPIKE" ? "SPIKE" : "");
  return `
    <div class="att-expl ${it.llm_used ? "gem" : ""}">
      <div class="att-expl-label">Why it happened · ${src}</div>
      <div class="att-expl-text" data-fallback="${escapeHtml(it.explanation)}">${escapeHtml(it.explanation)}</div>
      ${canAsk ? `<button class="btn-explain" data-store="${escapeHtml(it.store_id)}"
        data-product="${escapeHtml(it.product_id)}" data-signal="${escapeHtml(signal)}">Ask Gemini</button>` : ""}
    </div>`;
}

function bindExplainButtons(root) {
  (root || document).querySelectorAll(".btn-explain").forEach((b) =>
    b.addEventListener("click", askGeminiExplain));
}

async function askGeminiExplain(e) {
  const btn = e.currentTarget;
  const box = btn.closest(".att-expl");
  const textEl = box.querySelector(".att-expl-text");
  const fallback = textEl.dataset.fallback || textEl.textContent;
  btn.disabled = true;
  btn.textContent = "…";
  textEl.textContent = "Generating Gemini explanation…";
  try {
    const qs = new URLSearchParams({
      store_id: btn.dataset.store, product_id: btn.dataset.product,
      signal: btn.dataset.signal || "SPIKE", use_llm: "true",
    });
    const d = await api("/api/anomaly/explain?" + qs.toString());
    textEl.textContent = d.explanation || fallback;
    box.classList.add("gem");
    const lbl = box.querySelector(".att-expl-label");
    if (lbl) lbl.textContent = "Why it happened · Gemini";
  } catch (err) {
    textEl.textContent = fallback;
  } finally {
    btn.remove();
  }
}

function renderAttentionPreview(items) {
  const el = document.getElementById("attentionPreview");
  el.innerHTML = items.length ? items.slice(0, 6).map(attentionItemHTML).join("")
    : "<div class='hint'>Nothing needs attention right now.</div>";
  bindExplainButtons(el);
}

// ---------------------------------------------------------------------------
// Attention
// ---------------------------------------------------------------------------
async function loadAttention() {
  const scope = document.getElementById("attScope").value;
  const store = document.getElementById("attStore").value;
  const qs = "scope=" + encodeURIComponent(scope) + "&max_items=30" + (store ? "&store_id=" + store : "");
  const d = await api("/api/attention?" + qs);
  state.attentionItems = d.items;
  state.recommendations = d.recommendations || [];
  document.getElementById("attentionList").innerHTML = d.items.length
    ? d.items.map(attentionItemHTML).join("")
    : "<div class='hint'>No matching items.</div>";
  bindExplainButtons(document.getElementById("attentionList"));
}

async function populateStoreFilter() {
  try {
    const s = await api("/api/stores");
    const opts = '<option value="">All stores</option>' + s.stores.map((x) =>
      `<option value="${x.store_id}">${escapeHtml(x.store_id)} · ${escapeHtml(x.store_name)}</option>`).join("");
    const attSel = document.getElementById("attStore");
    if (attSel) attSel.innerHTML = opts;
    const trendSel = document.getElementById("trendStore");
    if (trendSel) trendSel.innerHTML = opts;
  } catch (e) { /* keep empty */ }
}

// ---------------------------------------------------------------------------
// Inventory
// ---------------------------------------------------------------------------
async function loadInventory() {
  const d = await api("/api/inventory/health");
  barList("coverageChart", (d.coverage_distribution || []).map((b) => ({
    label: b.bucket, value: b.count, suffix: " (" + (Number(b.pct) || 0).toFixed(1) + "%)",
  })), { title: "Coverage buckets" });
  const h = d.health;
  document.getElementById("invHealth").innerHTML = [
    ["Total inventory value", fmtMoney(h.total_inventory_value)],
    ["Overstock excess value", fmtMoney(h.overstock_value)],
    ["Overstock excess units", fmtNum(h.overstock_excess_units)],
    ["Dead stock value", fmtMoney(h.dead_stock_value)],
    ["Turnover (annualized)", h.inventory_turnover_annualized ? h.inventory_turnover_annualized + "×" : "–"],
  ].map(([k, v]) => `<div class="kv"><span class="k">${k}</span><span class="v">${v}</span></div>`).join("");

  document.getElementById("stockoutRisks").innerHTML =
    "<table><thead><tr><th>Priority</th><th>Product</th><th>Store</th><th class='num'>Stock</th>" +
    "<th class='num'>Cover</th><th class='num'>Demand/day</th><th class='num'>Lead</th>" +
    "<th class='num'>Order qty</th><th></th></tr></thead><tbody>" +
    (d.stockout_risks || []).map((r) => `
      <tr><td><span class="pill pill-${r.risk}">${r.risk}</span></td>
      <td>${escapeHtml(r.product_name)}</td><td>${r.store_id}</td>
      <td class="num">${fmtNum(r.stock)}</td><td class="num">${fmtNum(r.cover_days, 1)}d</td>
      <td class="num">${fmtNum(r.avg_daily_demand_30d, 1)}</td>
      <td class="num">${r.lead_time_days}d</td>
      <td class="num"><b>${fmtNum(r.recommended_order_qty)}</b></td>
      <td><button class="btn-why" data-issue="REC:${r.store_id}:${r.product_id}:STOCKOUT_RISK">Why?</button></td></tr>`).join("") +
    "</tbody></table>";

  document.getElementById("reorderList").innerHTML =
    "<table><thead><tr><th>Product</th><th>Store</th><th class='num'>Current</th>" +
    "<th class='num'>Target</th><th class='num'>Suggested</th><th class='num'>Demand/day</th></tr></thead><tbody>" +
    (d.reorder_suggestions || []).map((r) => `
      <tr><td>${escapeHtml(r.product_name)}</td><td>${r.store_id}</td>
      <td class="num">${fmtNum(r.current_stock)}</td><td class="num">${fmtNum(r.target_level, 1)}</td>
      <td class="num"><b>${fmtNum(r.suggested_quantity)}</b></td>
      <td class="num">${fmtNum(r.avg_daily_demand_30d, 1)}</td></tr>`).join("") +
    "</tbody></table>";

  document.getElementById("overstockList").innerHTML =
    "<table><thead><tr><th>Product</th><th>Store</th><th>Category</th><th class='num'>Stock</th>" +
    "<th class='num'>Cover</th><th class='num'>Units 30d</th><th class='num'>Excess units</th>" +
    "<th class='num'>Excess value</th><th>Action</th></tr></thead><tbody>" +
    (d.overstock || []).map((r) => `
      <tr><td>${escapeHtml(r.product_name)}</td><td>${r.store_id}</td><td>${escapeHtml(r.category || "")}</td>
      <td class="num">${fmtNum(r.stock)}</td><td class="num">${fmtNum(r.cover_days, 1)}d</td>
      <td class="num">${fmtNum(r.units_30d)}</td><td class="num">${fmtNum(r.excess_units)}</td>
      <td class="num">${fmtMoney(r.excess_value)}</td>
      <td>${escapeHtml(r.action || "")}</td></tr>`).join("") +
    "</tbody></table>";

  document.getElementById("transferList").innerHTML =
    "<table><thead><tr><th>Product</th><th>From</th><th>To</th><th class='num'>Source cover</th>" +
    "<th class='num'>Dest cover</th><th class='num'>Suggested units</th><th class='num'>Value</th></tr></thead><tbody>" +
    (d.transfers || []).map((r) => `
      <tr><td>${escapeHtml(r.product_name)}</td><td>${r.from_store}</td><td>${r.to_store}</td>
      <td class="num">${fmtNum(r.source_cover_days, 1)}d</td><td class="num">${fmtNum(r.dest_cover_days, 1)}d</td>
      <td class="num"><b>${fmtNum(r.recommended_units)}</b></td>
      <td class="num">${fmtMoney(r.transfer_value)}</td></tr>`).join("") +
    "</tbody></table>";

  try {
    const slow = await api("/api/inventory/slow-moving?limit=20");
    document.getElementById("slowMovingList").innerHTML =
      "<table><thead><tr><th>Product</th><th>Store</th><th>Category</th><th class='num'>Stock</th>" +
      "<th class='num'>Cover</th><th class='num'>Units 30d</th><th class='num'>Demand/day</th><th>Action</th></tr></thead><tbody>" +
      (slow.items || []).map((r) => `
        <tr><td>${escapeHtml(r.product_name)}</td><td>${r.store_id}</td><td>${escapeHtml(r.category || "")}</td>
        <td class="num">${fmtNum(r.stock)}</td><td class="num">${fmtNum(r.cover_days, 1)}d</td>
        <td class="num">${fmtNum(r.units_30d)}</td><td class="num">${fmtNum(r.avg_daily_demand_30d, 2)}</td>
        <td>${escapeHtml(r.action || "")}</td></tr>`).join("") +
      "</tbody></table>";
  } catch (e) {
    document.getElementById("slowMovingList").innerHTML = "<div class='hint'>" + escapeHtml(e.message) + "</div>";
  }
}

// ---------------------------------------------------------------------------
// Sales
// ---------------------------------------------------------------------------
function anomalyLineHTML(a) {
  const cls = a.signal === "SPIKE" ? "spike" : "drop";
  const pctHtml = a.change_pct != null
    ? `<span class="trend-anom-pct ${a.change_pct >= 0 ? "up" : "down"}">${pct(a.change_pct)}</span>`
    : "";
  return `<div class="trend-anom ${cls}">
    <div class="trend-anom-head">
      <span class="pill pill-MEDIUM">${escapeHtml(a.signal || "")}</span>
      <b>${escapeHtml(a.product_name || "")}</b> @ ${escapeHtml(a.store_id || "")} ${pctHtml}
    </div>
    <div class="att-expl-text">${escapeHtml(a.explanation)}</div>
  </div>`;
}

async function loadSalesTrend() {
  const days = document.getElementById("trendDays").value;
  const store = document.getElementById("trendStore").value;
  const cat = document.getElementById("trendCategory").value;
  const qs = new URLSearchParams();
  qs.set("days", days);
  if (store) qs.set("store_id", store);
  if (cat) qs.set("category", cat);
  const d = await api("/api/sales/trend?" + qs);
  lineChart("salesTrendChart", d.points, [
    { key: "revenue", color: "#34d399", label: "Revenue", area: true },
  ], { height: 320, yFmt: fmtMoney });
  const tx = document.getElementById("trendAnomalies");
  if (tx) {
    tx.innerHTML = (d.anomalies && d.anomalies.length)
      ? "<div class='trend-anomalies'>" + d.anomalies.map(anomalyLineHTML).join("") + "</div>"
      : "<div class='hint'>No spikes or drops detected in this view.</div>";
  }
  const st = state.dashboard ? state.dashboard.stores : [];
  if (st.length) {
    barList("storeRevenueChart", st.map((s) => ({
      label: s.store_name || s.store_id, value: Number(s.revenue_30d), display: fmtMoney(s.revenue_30d),
    })), { title: "Revenue by store" });
    barList("storeUnitsChart", st.map((s) => ({
      label: s.store_name || s.store_id, value: Number(s.units_30d),
    })), { title: "Units by store" });
  }
}

// ---------------------------------------------------------------------------
// Products
// ---------------------------------------------------------------------------
let searchTimer = null;
async function loadProducts(q) {
  const d = await api("/api/products?q=" + encodeURIComponent(q) + "&limit=30");
  const el = document.getElementById("productTable");
  el.innerHTML = "<table><thead><tr><th>ID</th><th>Product</th><th>Category</th><th>Brand</th><th>SKU</th></tr></thead><tbody>" +
    (d.items || []).map((p) => `<tr data-pid="${p.product_id}" class="prow">
      <td>${p.product_id}</td><td>${escapeHtml(p.product_name)}</td>
      <td>${escapeHtml(p.category)}</td><td>${escapeHtml(p.brand || "")}</td><td>${p.SKU || ""}</td></tr>`).join("") +
    "</tbody></table><div class='hint'>" + d.count + " of " + d.total + " products shown. Click a row for detail.</div>";
  el.querySelectorAll(".prow").forEach((row) => row.addEventListener("click", async () => {
    const pid = row.dataset.pid;
    const storeSel = document.getElementById("attStore").value || undefined;
    loadProductDetail(pid, storeSel);
  }));
}

async function loadProductDetail(pid, storeId) {
  const qs = storeId ? "?store_id=" + storeId : "";
  const d = await api("/api/products/" + pid + qs);
  const box = document.getElementById("productDetail");
  box.hidden = false;
  document.getElementById("productDetailTitle").textContent = d.product.product_name + " · " + pid;
  barList("productMonthlyChart", (d.trend || []).map((t) => ({
    label: String(t.date).slice(5), value: t.units, suffix: " units",
  })), { title: "Daily units (30 days)" });
  const an = d.anomaly || {};
  const rows = [
    ["SKU", d.product.SKU], ["Category", d.product.category],
    ["Brand", d.product.brand], ["Unit cost", "$" + (Number(d.product.unit_cost) || 0).toFixed(2)],
    ["Selling price", "$" + (Number(d.product.selling_price) || 0).toFixed(2)],
    ["Current stock" + (d.stores_snapshot && d.stores_snapshot.length ? " @" + d.stores_snapshot[0].store_id : ""), d.current_stock != null ? fmtNum(d.current_stock) : "–"],
    ["Cover", d.cover_days != null ? fmtNum(d.cover_days, 1) + "d" : "–"],
    ["Risk", d.risk || "–"],
    ["Anomaly signal", (an.signal || "none")],
    ["Anomaly change", an.change_pct != null ? pct(an.change_pct) : "–"],
    ["Promo overlap", an.promo_overlap ? (an.promo_name || "yes") : "no"],
    ["Active stores", d.stores_snapshot ? d.stores_snapshot.length : 0],
  ];
  document.getElementById("productDetailInfo").innerHTML =
    '<dl class="kv-detail">' + rows.map(([k, v]) =>
      `<dt>${k}</dt><dd>${escapeHtml(String(v))}</dd>`).join("") + "</dl>";
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ---------------------------------------------------------------------------
// Stores
// ---------------------------------------------------------------------------
async function loadStores() {
  const d = await api("/api/stores");
  document.getElementById("storeCards").innerHTML = (d.stores || []).map((s) => `
    <div class="store-card">
      <h4>${escapeHtml(s.store_name || s.store_id)} <span class="pill pill-${s.revenue_change_pct >= 0 ? "LOW" : "HIGH"}">${s.store_id}</span></h4>
      <div class="loc">${s.region || ""} · ${s.store_type || ""}</div>
      <div class="big">${fmtMoney(s.revenue_30d)}</div>
      <div class="row"><span>Units 30d</span><span>${fmtNum(s.units_30d)}</span></div>
      <div class="row"><span>Rev/unit</span><span>${fmtMoney(s.revenue_per_unit)}</span></div>
      <div class="row"><span>Margin</span><span>${s.margin_pct != null ? s.margin_pct + "%" : "–"}</span></div>
      <div class="row"><span>Revenue Δ</span><span>${pct(s.revenue_change_pct)}</span></div>
      <div class="row"><span>Units Δ</span><span>${pct(s.units_change_pct)}</span></div>
    </div>`).join("");
}

// ---------------------------------------------------------------------------
// Import data
// ---------------------------------------------------------------------------
let importFileObj = null;
let importReport = null;
let importBusy = false;

function setImportStatus(msg, isError) {
  const el = document.getElementById("importStatus");
  el.textContent = msg || "";
  el.style.color = isError ? "#f87171" : "";
}

function setupImport() {
  const dz = document.getElementById("dropzone");
  const fi = document.getElementById("importFile");
  dz.addEventListener("click", () => fi.click());
  fi.addEventListener("change", () => {
    if (fi.files.length) setImportFile(fi.files[0]);
    fi.value = "";
  });
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("over"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("over");
    if (e.dataTransfer.files.length) setImportFile(e.dataTransfer.files[0]);
  });
  document.getElementById("downloadSample").addEventListener("click", downloadSample);
  document.getElementById("validateBtn").addEventListener("click", validateImport);
  document.getElementById("importBtn").addEventListener("click", runImport);
}

function setImportFile(f) {
  importFileObj = f;
  importReport = null;
  document.getElementById("importFileInfo").textContent =
    f.name + " · " + (f.size / 1024).toFixed(1) + " KB · " + f.type || "unknown type";
  document.getElementById("importActions").hidden = false;
  document.getElementById("importBtn").hidden = true;
  document.getElementById("importPreview").innerHTML = "";
  setImportStatus("");
}

async function downloadSample() {
  const t = document.getElementById("sampleType").value;
  try {
    const r = await fetch("/api/import/sample?type=" + t);
    if (!r.ok) throw new Error(r.status + " /api/import/sample");
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "sample_" + t.toLowerCase() + ".csv";
    a.click();
    URL.revokeObjectURL(a.href);
    setImportStatus("Downloaded sample_" + t.toLowerCase() + ".csv — try importing it.");
  } catch (e) {
    setImportStatus(e.message, true);
  }
}

async function validateImport() {
  if (!importFileObj) { setImportStatus("Choose a file first.", true); return; }
  if (importBusy) return;
  importBusy = true;
  setImportStatus("Validating…");
  try {
    const fd = new FormData();
    fd.append("file", importFileObj, importFileObj.name);
    const report = await api("/api/import/validate", { method: "POST", body: fd });
    importReport = report;
    renderImportReport(report);
    setImportStatus(report.message || ("Type detected: " + report.type + " · " + report.records + " records."));
  } catch (e) {
    setImportStatus(e.message, true);
  } finally {
    importBusy = false;
  }
}

function renderImportReport(report) {
  const cols = report.columns || [];
  const head = "<table><thead><tr>" + cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("") + "</tr></thead><tbody>";
  const body = (report.preview || []).map((row) =>
    "<tr>" + cols.map((c) => `<td>${escapeHtml(row[c] != null ? row[c] : "")}</td>`).join("") + "</tr>").join("");
  document.getElementById("importPreview").innerHTML =
    head + body + "</tbody></table>" +
    `<div class="import-summary"><span class="pill pill-MEDIUM">${escapeHtml(report.type)}</span> ` +
    `<span class="tag ${report.status === "ready" ? "tag-ok" : (report.status === "partial" ? "tag-warn" : "tag-bad")}">${escapeHtml(report.status)}</span>` +
    ` · ${fmtNum(report.records)} records · ${fmtNum(report.valid_count)} valid` +
    (report.error_counts && Object.keys(report.error_counts).length
      ? ` · errors: ${Object.entries(report.error_counts).map(([k, v]) => `${escapeHtml(k)} ×${v}`).join(", ")}` : "") +
    (report.warning_counts && Object.keys(report.warning_counts).length
      ? ` · warnings: ${Object.entries(report.warning_counts).map(([k, v]) => `${escapeHtml(k)} ×${v}`).join(", ")}` : "") + "</div>";

  const canImport = report.valid_count > 0;
  document.getElementById("importBtn").hidden = !canImport;
}

async function runImport() {
  if (!importFileObj) { setImportStatus("Choose a file first.", true); return; }
  if (importBusy) return;
  importBusy = true;
  setImportStatus("Importing… this recomputes summaries and forecasts, so it can take a moment.");
  try {
    const fd = new FormData();
    fd.append("file", importFileObj, importFileObj.name);
    const sum = await api("/api/import", { method: "POST", body: fd });
    renderImportSummary(sum);
    setImportStatus((sum.message || "Import completed.") + " Imported " + fmtNum(sum.imported) +
      " of " + fmtNum(sum.records) + " records.");
    document.getElementById("importPreview").innerHTML = "";
    await loadImportHistory();
    await loadInventory();
    if (state.dashboard) await loadOverview();
  } catch (e) {
    setImportStatus(e.message, true);
  } finally {
    importBusy = false;
    importReport = null;
  }
}

function renderImportSummary(sum) {
  const el = document.getElementById("importHistory");
  const one = ` <div class="import-summary"><span class="tag ${sum.status === "success" ? "tag-ok" : "tag-bad"}">${escapeHtml(sum.status)}</span> ` +
    ` re-imported <b>${escapeHtml(sum.filename)}</b> — ${fmtNum(sum.imported)} imported, ${fmtNum(sum.skipped)} skipped` +
    (sum.reasons && sum.reasons.length ? ` · ${sum.reasons.map((r) => `${escapeHtml(r.reason)} ×${r.count}`).join(", ")}` : "") + "</div>";
  el.insertAdjacentHTML("afterbegin", one);
}

async function loadImportHistory() {
  try {
    const d = await api("/api/import/history?limit=30");
    const rows = (d.items || []).slice(0, 12);
    document.getElementById("importHistory").innerHTML =
      "<table><thead><tr><th>When</th><th>File</th><th>Type</th><th class='num'>Records</th>" +
      "<th class='num'>Imported</th><th class='num'>Skipped</th><th>Errors</th><th>Status</th></tr></thead><tbody>" +
      rows.map((h) => `
        <tr><td>${escapeHtml(h.created_at || "")}</td><td>${escapeHtml(h.filename || "")}</td>
        <td>${escapeHtml(h.file_type || "")}</td><td class="num">${fmtNum(h.records)}</td>
        <td class="num">${fmtNum(h.imported)}</td><td class="num">${fmtNum(h.skipped)}</td>
        <td>${escapeHtml(((h.errors || []).map((e) => e.reason + " ×" + e.count).join(", ") || "—"))}</td>
        <td><span class="tag ${h.status === "success" ? "tag-ok" : "tag-bad"}">${escapeHtml(h.status || "")}</span></td></tr>`).join("") +
      "</tbody></table>" + (rows.length ? "" : "<div class='hint'>No imports recorded yet.</div>");
  } catch (e) {
    document.getElementById("importHistory").innerHTML = "<div class='hint'>" + escapeHtml(e.message) + "</div>";
  }
}

function loadImport() {
  loadImportHistory();
}

// ---------------------------------------------------------------------------
// Copilot
// ---------------------------------------------------------------------------
function chatAdd(role, html) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.innerHTML = html;
  document.getElementById("chatLog").appendChild(div);
  document.getElementById("chatLog").scrollTop = document.getElementById("chatLog").scrollHeight;
  return div;
}

function renderCopilotAnswer(r) {
  const metrics = (r.key_metrics || []).filter((m) => m && m.label && m.value !== null && m.value !== undefined)
    .map((m) => `<div class="rc-item"><b>${escapeHtml(m.label)}</b>${escapeHtml(String(m.value))}</div>`).join("");
  const recs = (r.recommendations || []).length ? `
    <div class="rc">
      ${(r.recommendations || []).slice(0, 6).map((x) =>
        `<div class="rc-item"><b>${escapeHtml(x.issue || x.priority || "")}</b>${escapeHtml(String(x.product_name || x.label || ""))}</div>`).join("")}
    </div>` : "";
  const cites = (r.policy_citations || []).length ? `
    <div class="cites">${r.policy_citations.map((c) => `<span class="cite">${escapeHtml(String(c))}</span>`).join("")}</div>` : "";
  const limits = (r.limitations || []).length ? `
    <div class="limitations">⚠ ${r.limitations.map(escapeHtml).join(" · ")}</div>` : "";
  const misc = r.needs_clarification && r.clarification_message != null && r.clarification_message !== r.answer
    ? `<div class="limitations">${escapeHtml(r.clarification_message)}</div>` : "";
  const src = (r.data_used && r.data_used.index) ? "policy index + " : "";
  const footer = `<div class="cites">${src}${(r.data_used && r.data_used.tables || []).slice(0, 6).join(", ")}` +
    ` · ${r.llm_used ? "LLM narration" : "deterministic engine"} · confidence ${r.confidence || "medium"}</div>`;
  return `<div class="answer">${escapeHtml(r.answer)}</div>${metrics}${recs}${cites}${limits}${misc}${footer}`;
}

async function sendChat(text) {
  text = (text || "").trim();
  if (!text) return;
  chatAdd("user", escapeHtml(text));
  const typing = chatAdd("bot", '<span class="bubble-typing">Analyzing…</span>');
  try {
    const r = await api("/api/copilot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: text }),
    });
    typing.remove();
    chatAdd("bot", renderCopilotAnswer(r));
  } catch (e) {
    typing.remove();
    chatAdd("bot", '<div class="answer">Sorry — I hit an error: ' + escapeHtml(e.message) + "</div>");
  }
}

// ---------------------------------------------------------------------------
// Evidence
// ---------------------------------------------------------------------------
function renderEvidenceBlocks(ev) {
  if (!ev || !ev.length) return "<div class='hint'>No recorded calculations for this item.</div>";
  return ev.map((b) => `
    <div class="eblock">
      <div class="esrc">${escapeHtml(b.source || "record")}</div>
      <div class="etext">${escapeHtml(b.text || "")}</div>
      ${b.formula ? `<div class="ecal">Φ ${escapeHtml(b.formula)}</div>` : ""}
      ${b.calculation ? `<div class="ecal">= ${escapeHtml(b.calculation)}</div>` : ""}
      ${b.policy ? `<div class="epol">📄 ${escapeHtml(b.policy)}</div>` : ""}
    </div>`).join("");
}

async function openDrawer(issueId, label) {
  const drawer = document.getElementById("evidenceDrawer");
  const backdrop = document.getElementById("drawerBackdrop");
  drawer.hidden = false;
  backdrop.hidden = false;
  document.getElementById("drawerBody").innerHTML = "<div class='hint'>Loading…</div>";
  document.querySelector(".drawer-head h3").textContent = label || "Evidence trail";
  try {
    const ev = await api("/api/evidence/" + encodeURIComponent(issueId));
    const m = ev.metrics || {};
    const chips = Object.keys(m).slice(0, 5).map((k) =>
      `<div class="metric-chip"><b>${escapeHtml(String(m[k]))}</b><span>${escapeHtml(k.replace(/_/g, " "))}</span></div>`).join("");
    document.getElementById("drawerBody").innerHTML = `
      <div class="eblock"><div class="esrc">ISSUE</div>
        <div class="etext">${escapeHtml(ev.issue_type)} · ${escapeHtml(ev.product)} @ ${escapeHtml(ev.store)}</div>
        <div class="etext">${escapeHtml(ev.reason || "")}</div></div>
      <div class="metric-strip">${chips}</div>
      ${renderEvidenceBlocks(ev.evidence)}`;
  } catch (e) {
    document.getElementById("drawerBody").innerHTML = "<div class='hint'>" + escapeHtml(e.message) + "</div>";
  }
}

function closeDrawer() {
  document.getElementById("evidenceDrawer").hidden = true;
  document.getElementById("drawerBackdrop").hidden = true;
}

async function loadEvidence() {
  const d = await api("/api/attention?scope=all&max_items=100");
  const all = (d.items || []).map((it) => ({
    label: it.issue_type + " · " + it.product_name + " @ " + it.store_id,
    issue_id: it.issue_id, priority: it.priority, reason: it.reason,
  }));
  const el = document.getElementById("evidenceList");
  el.innerHTML = all.map((it) => `
    <div class="att sev-${it.priority}">
      <div class="att-main">
        <div class="issue-type"><span class="dot">${SEV_DOT[it.priority] || "🔵"}</span>${escapeHtml(it.label)}</div>
        <div class="att-reason">${escapeHtml(it.reason)}</div>
      </div>
      <button class="btn-why" data-issue="${it.issue_id}">Show evidence</button>
    </div>`).join("") || "<div class='hint'>Nothing recorded.</div>";
  bindWhyButtons();
}

function bindWhyButtons() {
  document.querySelectorAll(".btn-why").forEach((b) => b.addEventListener("click", (e) => {
    const id = e.currentTarget.dataset.issue;
    openDrawer(id.replace(/^REC:/, ""), "Evidence trail");
  }));
}

// ---------------------------------------------------------------------------
// utils
// ---------------------------------------------------------------------------
function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------------------
// boot
// ---------------------------------------------------------------------------
function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

async function startApp() {
  bindNav();
  await initHealth();
  await populateStoreFilter();

  document.getElementById("attScope").addEventListener("change", loadAttention);
  document.getElementById("attStore").addEventListener("change", loadAttention);
  document.getElementById("trendDays").addEventListener("change", loadSalesTrend);
  document.getElementById("trendStore").addEventListener("change", loadSalesTrend);
  document.getElementById("trendCategory").addEventListener("change", loadSalesTrend);
  document.getElementById("productSearch").addEventListener("input",
    debounce((e) => loadProducts(e.target.value), 250));
  document.getElementById("chatSend").addEventListener("click", () => {
    sendChat(document.getElementById("chatInput").value);
    document.getElementById("chatInput").value = "";
  });
  document.getElementById("chatInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      sendChat(document.getElementById("chatInput").value);
      document.getElementById("chatInput").value = "";
    }
  });
  document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
    sendChat(c.textContent.trim());
  }));
  document.getElementById("drawerClose").addEventListener("click", closeDrawer);
  document.getElementById("drawerBackdrop").addEventListener("click", closeDrawer);
  setupImport();

  await loadOverview();
}
window.__startApp = startApp;

function boot() {
  const importBtnTop = document.getElementById("importBtnTop");
  if (importBtnTop) importBtnTop.addEventListener("click", () => navigate("import"));
  startApp();
}
document.addEventListener("DOMContentLoaded", boot);