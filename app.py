"""Retail Intelligence Copilot — FastAPI server.

Serves the dark-theme dashboard, a Gemini-powered copilot chat and the
deterministic data import pipeline. Run with:  python app.py
(http://localhost:8000)
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

GEMINI_MODEL = "gemini-3.5-flash-lite"

from src.copilot import answer as copilot_answer, INSUFFICIENT_DATA_MSG
from src.importer import (
    read_file as importer_read_file,
    detect_type as importer_detect_type,
    _mapping_for as importer_mapping_for,
    validate as importer_validate,
    perform_import as importer_perform_import,
)
from src.schemas import CopilotRequest
import src.analytics as analytics

# ---------------------------------------------------------------------------
# Rich mock data (30 days) — no zeros. Populates the dashboard immediately.
# ---------------------------------------------------------------------------
MOCK = {
    "kpis": {
        "revenue_30d": 286800,
        "units_30d": 4520,
        "margin_30d": 142000,
        "inventory_value": 5312000,
        "critical_stockout": 3,
        "overstock_items": 7,
        "slow_moving_items": 12,
        "product_store_pairs": 21000,
    },
    "labels": [f"Day {i+1}" for i in range(30)],
    "revenue_trend": [int(9500 + i * 90 + (i % 7) * 380) for i in range(30)],
    "units_trend": [int(140 + i + (i % 3) * 17) for i in range(30)],
    "category_share": [
        {"label": "Beverages", "value": 240},
        {"label": "Snacks", "value": 198},
        {"label": "Electronics", "value": 152},
        {"label": "Household", "value": 134},
        {"label": "Beauty", "value": 87},
        {"label": "Fitness", "value": 64},
    ],
    "stores": [
        {"store_id": "S01", "name": "Downtown Pune Market", "city": "Pune", "revenue_30d": 45888, "units_30d": 723},
        {"store_id": "S02", "name": "Phoenix Mall Store", "city": "Pune", "revenue_30d": 40152, "units_30d": 633},
        {"store_id": "S03", "name": "Hometown Grocers", "city": "Nashik", "revenue_30d": 28680, "units_30d": 452},
        {"store_id": "S04", "name": "Mumbai Central", "city": "Mumbai", "revenue_30d": 45888, "units_30d": 723},
        {"store_id": "S05", "name": "Delhi Heights", "city": "Delhi", "revenue_30d": 40152, "units_30d": 633},
        {"store_id": "S06", "name": "Bangalore Tech Park", "city": "Bangalore", "revenue_30d": 45888, "units_30d": 723},
        {"store_id": "S07", "name": "Hyderabad Hub", "city": "Hyderabad", "revenue_30d": 40152, "units_30d": 633},
    ],
    "products": [
        {"name": "Smartwatch Pro", "category": "Electronics", "revenue_30d": 42150, "units_30d": 85},
        {"name": "Ergonomic Chair", "category": "Office", "revenue_30d": 36480, "units_30d": 42},
        {"name": "Mechanical Keyboard", "category": "Electronics", "revenue_30d": 28940, "units_30d": 96},
        {"name": "Bluetooth Earbuds Mini", "category": "Electronics", "revenue_30d": 26910, "units_30d": 180},
        {"name": "Portable Charger 20K", "category": "Electronics", "revenue_30d": 22480, "units_30d": 185},
        {"name": "Bluetooth Speaker X", "category": "Electronics", "revenue_30d": 21890, "units_30d": 131},
        {"name": "Organic Face Cream 50ml", "category": "Beauty", "revenue_30d": 14670, "units_30d": 196},
        {"name": "USB-C Hub Adapter", "category": "Electronics", "revenue_30d": 13760, "units_30d": 240},
        {"name": "Cola Max 300ml", "category": "Beverages", "revenue_30d": 12410, "units_30d": 620},
        {"name": "Green Tea Matcha 500ml", "category": "Beverages", "revenue_30d": 11240, "units_30d": 540},
        {"name": "Protein Shake Vanilla", "category": "Fitness", "revenue_30d": 10020, "units_30d": 410},
        {"name": "EnergyBar Protein Pack", "category": "Snacks", "revenue_30d": 9860, "units_30d": 410},
        {"name": "LED Desk Lamp Pro", "category": "Household", "revenue_30d": 9330, "units_30d": 240},
        {"name": "Winter Scarf Premium", "category": "Seasonal", "revenue_30d": 8120, "units_30d": 310},
        {"name": "Silicone Baking Mat", "category": "Kitchen", "revenue_30d": 6540, "units_30d": 380},
    ],
    "attention": [
        {"issue": "CRITICAL stock-out", "product": "Cola Max 300ml", "store": "S01",
         "detail": "stock 13, cover 1.3d, lead 8d"},
        {"issue": "HIGH stock-out risk", "product": "EnergyBar Protein Pack", "store": "S01",
         "detail": "stock 25, cover 6d"},
        {"issue": "Overstock", "product": "LED Desk Lamp Pro", "store": "S02",
         "detail": "stock 300, cover 333d"},
    ],
}

app = FastAPI(title="Retail Intelligence Copilot")

ROOT = Path(__file__).parent
FRONTEND = ROOT / "frontend" / "index.html"


class ChatMessage(BaseModel):
    text: str


def copilot_reply_markdown(resp: dict) -> str:
    """Render the structured copilot response as markdown for the chat UI."""
    answer = resp.get("answer") or INSUFFICIENT_DATA_MSG
    parts = [answer]

    key_metrics = resp.get("key_metrics") or []
    if key_metrics:
        parts.append("### Key metrics")
        parts += [f"- **{m.get('label', '')}:** {m.get('value', '')}" for m in key_metrics]

    recs = resp.get("recommendations") or []
    if recs:
        parts.append("### Recommendations")
        for r in recs:
            parts.append(
                f"- **[{r.get('priority', 'MEDIUM')}]** {r.get('recommended_action', '')}"
                + (f" — {r.get('reason', '')}" if r.get("reason") else "")
            )

    evidence = resp.get("evidence") or []
    if evidence:
        parts.append("### Evidence used")
        for e in evidence[:6]:
            rec = e.get("record") or {}
            rec_str = ", ".join(f"{k}={v}" for k, v in rec.items())
            parts.append(f"- `{e.get('source', '')}` → {rec_str} ({e.get('policy', '')})")

    citations = resp.get("policy_citations") or []
    if citations:
        parts.append("### Policy references")
        parts += [f"- `{c.get('document', '')}` [{c.get('rule_id', '')}]" for c in citations]

    if resp.get("llm_used"):
        parts.append("\n*Explained by Gemini based on deterministic local data.*")
    return "\n".join(line for line in parts if line)


@app.get("/")
def serve_dashboard():
    return FileResponse(FRONTEND)


@app.get("/api/dashboard")
def dashboard():
    try:
        payload = build_live_dashboard()
        if payload is not None:
            return JSONResponse(payload)
    except Exception:  # noqa: BLE001 - fall back to mock on any failure
        pass
    return JSONResponse(MOCK)


def build_live_dashboard() -> dict | None:
    """Shape a dashboard payload (identical to MOCK) from the real DB."""
    k = analytics.dashboard_kpis()["kpis"]
    trend = analytics.trend(30) or []
    live_kpis = {
        "revenue_30d": _round(k["revenue_30d"]),
        "units_30d": k["units_30d"],
        "margin_30d": _round(k["margin_30d"]),
        "inventory_value": _round(k["inventory_value"]),
        "critical_stockout": k.get("stockout_critical", 0),
        "overstock_items": k.get("overstock_items", 0),
        "slow_moving_items": k.get("slow_moving_items", 0),
        "product_store_pairs": k.get("product_store_pairs", 0),
    }
    labels = []
    revenue_trend = []
    units_trend = []
    for t in trend:
        d = str(t.get("date"))
        labels.append(d)
        revenue_trend.append(int(round(t.get("revenue", 0) or 0)))
        units_trend.append(int(round(t.get("units", 0) or 0)))
    if not labels:
        return None

    cats = analytics.category_performance(30) or []
    category_share = [{"label": c["category"], "value": int(round(c.get("units", 0) or 0))}
                      for c in cats][:6]

    stores_in = []
    for s in analytics.store_comparison():
        stores_in.append({
            "store_id": s["store_id"],
            "name": s.get("store_name", s["store_id"]),
            "city": s.get("region", ""),
            "revenue_30d": _round(s.get("revenue_30d", 0)),
            "units_30d": _round(s.get("units_30d", 0)),
        })

    products = []
    for p in analytics.top_products(30, 15):
        products.append({
            "name": p["product_name"],
            "category": p.get("category", ""),
            "revenue_30d": _round(p.get("revenue", 0)),
            "units_30d": _round(p.get("units", 0)),
        })

    attention = []
    for a in analytics.attention_items("all", max_items=6):
        metric = a.get("metrics") or {}
        label = a.get("issue_type", "ALERT")
        attention.append({
            "issue": label.replace("_", " "),
            "product": a.get("product_name", ""),
            "store": a.get("store_id", ""),
            "detail": a.get("reason", "") or "",
        })

    payload = dict(MOCK)
    payload.update({
        "kpis": live_kpis,
        "labels": labels,
        "revenue_trend": revenue_trend,
        "units_trend": units_trend,
        "category_share": category_share,
        "stores": stores_in,
        "products": products,
        "attention": attention,
    })
    return payload


def _round(v) -> int:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return 0


@app.post("/api/copilot")
def copilot(req: CopilotRequest):
    resp = copilot_answer(req)
    return JSONResponse(resp)


@app.post("/api/chat")
def chat(msg: ChatMessage):
    try:
        resp = copilot_answer(CopilotRequest(query=msg.text, use_llm=True))
        reply = copilot_reply_markdown(resp)
    except Exception:  # noqa: BLE001 - never fail the user
        reply = INSUFFICIENT_DATA_MSG
    return {"reply": reply}


@app.post("/api/import")
async def import_file(file: UploadFile = File(...)):
    """Validate and import an uploaded CSV / XLSX / JSON file (multipart)."""
    content = await file.read()
    df, err = importer_read_file(file.filename, content)
    if err or df is None:
        return JSONResponse({"ok": False, "message": err or "Could not read file"}, status_code=400)

    dtype, missing = importer_detect_type(df)
    if dtype is None:
        return JSONResponse(
            {"ok": False, "message": f"Cannot detect data type: {', '.join(missing)}"},
            status_code=400)

    report = importer_validate(df, dtype, importer_mapping_for(df, dtype))
    summary = importer_perform_import(report)

    report_public = {k: v for k, v in report.items() if k != "_valid_rows"}
    return JSONResponse({"ok": summary.get("status") == "success", "report": report_public,
                         "summary": summary})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)