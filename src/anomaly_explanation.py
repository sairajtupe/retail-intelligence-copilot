"""Anomaly explanation layer.

Every detected sales SPIKE/DROP is explained in plain English using grounded
data read from the real database: dates, store, product, current stock/cover,
overflowing promotions and seasonal (holiday) flags. Gemini narrates the
explanation when available; a deterministic rule-based explanation built from
the same grounded numbers is always produced as a fallback so the explanation
is never blank. Numeric claims always originate in Python (anomaly finding +
DB), never from the model.
"""
from __future__ import annotations
import json
from datetime import date, timedelta
from typing import Any

from src.config import GEMINI_AVAILABLE, GEMINI_MODEL, log
import src.database as db
import src.anomaly_detection as anomaly_detection
from src.gemini_client import generate_json, GeminiUnavailable
from src.utils import safe_float, safe_int, parse_date, fmt_num, overlaps

_SIGNAL_WORDS = {"SPIKE": ("spiked", "rise"), "DROP": ("dropped", "decline")}


def _window() -> dict:
    md = parse_date(db.max_date()) or date.today()
    return {
        "as_of_date": md.isoformat(),
        "window_7d_start": (md - timedelta(days=7)).isoformat(),
        "window_30d_start": (md - timedelta(days=30)).isoformat(),
    }


def _promo_pct(promo: dict | None) -> float | None:
    if not promo or promo.get("discount_pct") is None:
        return None
    return safe_float(promo["discount_pct"]) * 100


def grounded_context(anomaly: dict) -> dict:
    """Assemble every real data fact available about an anomaly finding."""
    store_id = anomaly.get("store_id")
    product_id = anomaly.get("product_id")
    signal = anomaly.get("signal") or anomaly.get("anomaly_type") or ""
    ctx = {
        **_window(),
        "store_id": store_id,
        "product_id": product_id,
        "signal": signal,
        "anomaly_type": anomaly.get("anomaly_type") or signal,
        "change_pct": anomaly.get("change_pct"),
        "mean_units_7d": anomaly.get("mean_units_7d"),
        "mean_prior_30d": anomaly.get("mean_prior_30d"),
        "z_score": anomaly.get("z_score"),
        "verdict": anomaly.get("verdict"),
        "promo_overlap": bool(anomaly.get("promo_overlap")),
        "promo_name": anomaly.get("promo_name"),
        "promo_discount_pct": anomaly.get("promo_discount_pct"),
        "product_name": anomaly.get("product_name"),
        "category": anomaly.get("category"),
        "brand": None,
        "store_name": None,
        "city": None,
        "region": None,
        "current_stock": None,
        "cover_days": None,
        "avg_daily_demand_30d": None,
        "risk": None,
        "promotions": [],
        "seasonal_flags": [],
        "no_sales_history": False,
        "evidence": list(anomaly.get("evidence") or []),
        "policy_citation": anomaly.get("policy_citation", ""),
    }

    if store_id:
        store = db.fetchone(
            "SELECT store_name, city, region, store_type FROM stores WHERE store_id=?",
            (store_id,))
        if store:
            ctx["store_name"] = store["store_name"]
            ctx["city"] = store["city"]
            ctx["region"] = store["region"]

    if product_id:
        prod = db.fetchone(
            "SELECT product_name, category, brand FROM products WHERE product_id=?",
            (product_id,))
        if prod:
            ctx["product_name"] = prod["product_name"]
            ctx["category"] = prod["category"]
            ctx["brand"] = prod["brand"]

    if store_id and product_id:
        snap = db.fetchone(
            "SELECT stock, cover_days, avg_daily_demand_30d, units_7d, units_30d, risk "
            "FROM metrics_snapshot WHERE store_id=? AND product_id=?",
            (store_id, product_id))
        if snap:
            ctx["current_stock"] = safe_int(snap["stock"])
            ctx["cover_days"] = round(safe_float(snap["cover_days"]), 1)
            ctx["avg_daily_demand_30d"] = round(safe_float(snap["avg_daily_demand_30d"]), 2)
            ctx["units_7d"] = safe_int(snap["units_7d"])
            ctx["units_30d"] = safe_int(snap["units_30d"])
            ctx["risk"] = snap["risk"]

        window_start = (parse_date(ctx["as_of_date"]) - timedelta(days=14)).isoformat()
        ctx["promotions"] = [dict(r) for r in db.query_df("""
            SELECT promotion_name, start_date, end_date, discount_pct, demand_lift_x, promotion_type
            FROM promotions
            WHERE store_id=? AND product_id=? AND end_date >= ? AND start_date <= ?
            ORDER BY start_date""", (store_id, product_id, window_start, ctx["as_of_date"]))]
        ctx["seasonal_flags"] = [
            p["promotion_name"] for p in ctx["promotions"]
            if str(p.get("promotion_type") or "").upper() == "SEASONAL"]

        count = db.fetchone(
            "SELECT COUNT(*) AS n FROM sales WHERE store_id=? AND product_id=?",
            (store_id, product_id))
        ctx["no_sales_history"] = bool(count and safe_int(count["n"]) == 0)

    return ctx


def deterministic_explanation(ctx: dict) -> str:
    """Rule-based plain-English explanation. Never returns an empty string."""
    product = ctx.get("product_name") or ctx.get("product_id") or "the item"
    store = ctx.get("store_name") or ctx.get("store_id") or "the store"
    signal = (ctx.get("signal") or "").upper()
    verb, _ = _SIGNAL_WORDS.get(signal, ("moved", "movement"))

    if ctx.get("no_sales_history"):
        return (f"No sales history is recorded for {product} at {store}, so the "
                f"{signal.lower() or 'anomaly'} signal cannot be explained from sales data yet.")

    r7 = safe_float(ctx.get("mean_units_7d"))
    r30 = safe_float(ctx.get("mean_prior_30d"))
    chg = ctx.get("change_pct")
    if chg is None and not (r7 or r30):
        return f"{product} at {store} was flagged for a {signal.lower()} signal in the last 7 days."

    parts = [f"Sales of {product} at {store} {verb} from {fmt_num(r30, 1)} to {fmt_num(r7, 1)} "
             f"units/day (prior 30 days vs last 7)."]
    if chg is not None:
        parts[0] = parts[0].rstrip(".") + f" That is a {chg:+.1f}% change."

    if ctx.get("promo_overlap") and ctx.get("promo_name"):
        promo_line = f"This coincides with the promotion '{ctx['promo_name']}'"
        pct = _promo_pct(ctx) or (safe_float(ctx.get("promo_discount_pct")) * 100)
        if pct:
            promo_line += f" ({pct:.0f}% discount)"
        promo_line += ", so the movement is attributed to the promotion rather than a natural anomaly."
        parts.append(promo_line)
    else:
        parts.append(f"No promotion overlapped this window, so this is treated as a natural demand signal.")

    if ctx.get("seasonal_flags"):
        parts.append("A seasonal/holiday promotion is also on record for this pair: "
                     + ", ".join(str(f) for f in ctx["seasonal_flags"]) + ".")

    if ctx.get("current_stock") is not None:
        stock_line = f"Current stock is {safe_int(ctx['current_stock'])} units"
        if ctx.get("cover_days") is not None:
            stock_line += f" (~{fmt_num(ctx['cover_days'], 1)} days of cover)"
        parts.append(stock_line + ".")

    if ctx.get("evidence"):
        parts.append("Evidence: " + "; ".join(
            str(e.get("text", "")) for e in ctx["evidence"][:2]) + ".")

    return " ".join(parts) if parts else \
        f"A {signal.lower() or 'sales'} signal was recorded for {product} at {store}."


def gemini_explanation(ctx: dict) -> str:
    """Gemini narration of a grounded anomaly. Raises on any failure so the
    caller can fall back to the deterministic explanation."""
    system_prompt = (
        "You are the narration layer of a Retail Intelligence Copilot. You are given "
        "DETERMINISTIC grounded facts computed from a retail database.\n"
        "Rules:\n"
        "1. NEVER compute, invent, estimate or re-derive any number - reuse exactly the "
        "values provided, or omit what is missing.\n"
        "2. Write a short plain-English explanation (2-4 sentences) of why a sales "
        "SPIKE/DROP occurred, naming the product and store.\n"
        "3. If a promotion overlaps the window, attribute the movement to it; otherwise "
        "describe it as a natural demand signal.\n"
        "4. Mention seasonal/holiday flags, current stock and days of cover only if given.\n"
        "5. You may quote the evidence records verbatim; do not invent new evidence.\n"
        'Respond with JSON ONLY: {"explanation": "..."}.')
    user_prompt = (
        "GROUNDED ANOMALY CONTEXT (JSON):\n"
        + json.dumps({k: v for k, v in ctx.items() if k != "evidence"}, default=str)
        + "\n\nEVIDENCE RECORDS:\n"
        + json.dumps(ctx.get("evidence") or [], default=str))
    resp = generate_json(system_prompt, user_prompt, temperature=0.2)
    explanation = ""
    if isinstance(resp, dict):
        explanation = str(resp.get("explanation") or "").strip()
    elif isinstance(resp, str) and resp.strip():
        explanation = resp.strip()
    if not explanation:
        raise GeminiUnavailable("Gemini returned an empty explanation")
    return explanation


def explain_anomaly(anomaly: dict, use_llm: bool = True) -> dict:
    """Explain one anomaly: Gemini narration when available + grounded, with a
    deterministic fallback so `explanation` is never blank."""
    ctx = grounded_context(anomaly)
    fallback = deterministic_explanation(ctx)
    if use_llm and GEMINI_AVAILABLE:
        try:
            gem = gemini_explanation(ctx)
            return {"explanation": gem, "source": "gemini", "llm_used": True,
                    "grounded": ctx, "evidence": ctx["evidence"],
                    "policy_citation": ctx["policy_citation"]}
        except (GeminiUnavailable, ValueError) as exc:
            log.info("Gemini explanation unavailable (%s); using deterministic fallback", exc)
        except Exception as exc:  # noqa: BLE001
            log.warning("Gemini explanation failed (%s); using deterministic fallback", exc)
    return {"explanation": fallback, "source": "deterministic", "llm_used": False,
            "grounded": ctx, "evidence": ctx["evidence"],
            "policy_citation": ctx["policy_citation"]}


def explain_finding(anomaly: dict, use_llm: bool = False) -> dict:
    """Return a copy of the finding enriched with an inline explanation."""
    result = explain_anomaly(anomaly, use_llm=use_llm)
    out = dict(anomaly)
    out["explanation"] = result["explanation"]
    out["explanation_source"] = result["source"]
    out["llm_used"] = result["llm_used"]
    out["grounded"] = result["grounded"]
    out.setdefault("evidence", result["evidence"])
    out.setdefault("policy_citation", result["policy_citation"])
    return out


def find_anomaly(store_id: str, product_id: str, signal: str | None = None) -> dict | None:
    """Locate the most recent detection for a store-product pair."""
    for a in anomaly_detection.detect_anomalies(limit=300):
        if a["store_id"] == store_id and a["product_id"] == product_id \
                and (not signal or a["signal"] == (signal or "").upper()):
            return a
    return None


def build_anomaly(store_id: str, product_id: str, signal: str) -> dict:
    """Minimal anomaly dict for pairs with no current detection, so the explainer
    can still produce a grounded (and non-blank) explanation."""
    return {
        "store_id": store_id, "product_id": product_id,
        "signal": signal or "SPIKE", "anomaly_type": signal or "SPIKE",
        "product_name": None, "category": None, "brand": None,
        "change_pct": None, "mean_units_7d": None, "mean_prior_30d": None,
        "z_score": None, "verdict": None, "promo_overlap": False,
        "promo_name": None, "promo_discount_pct": None,
        "evidence": [], "policy_citation": "documents/sales_anomaly_policy.md [ANM-D-01/02/04]",
    }


def _anomaly_from_item(item: dict, signal: str) -> dict:
    metrics = item.get("metrics") or {}
    return {
        "store_id": item.get("store_id"), "product_id": item.get("product_id"),
        "signal": signal, "anomaly_type": signal,
        "product_name": item.get("product_name"), "category": item.get("category"),
        "change_pct": None,
        "mean_units_7d": round(safe_float(metrics.get("units_7d")) / 7.0, 2)
        if metrics.get("units_7d") is not None else None,
        "mean_prior_30d": metrics.get("average_daily_sales_30d"),
        "z_score": None, "verdict": "natural_signal",
        "promo_overlap": False, "promo_name": None, "promo_discount_pct": None,
        "evidence": list(item.get("evidence") or []),
        "policy_citation": "documents/sales_anomaly_policy.md [ANM-D-01/02/04, PRM-01]",
    }


def enrich_items(items: list[dict], anomalies: list[dict], use_llm: bool = False) -> list[dict]:
    """Attach an inline, never-blank explanation to attention items.

    SPIKE/DROP items from the detected list are explained directly; attention
    items flagged SALES_SPIKE/SALES_DROP are matched to their detection by
    store+product, falling back to a grounded context built from the item.
    """
    by_pair: dict[tuple, dict] = {}
    for a in anomalies:
        by_pair.setdefault((a.get("store_id"), a.get("product_id")), a)

    out: list[dict] = []
    for it in items:
        it = dict(it)
        label = (it.get("issue_type") or it.get("signal") or "").upper()
        if "SPIKE" in label:
            want = "SPIKE"
        elif "DROP" in label:
            want = "DROP"
        else:
            out.append(it)
            continue

        anomaly = by_pair.get((it.get("store_id"), it.get("product_id")))
        if anomaly is None or anomaly.get("signal") != want:
            anomaly = _anomaly_from_item(it, want)
        exp = explain_finding(anomaly, use_llm=use_llm)
        it["explanation"] = exp["explanation"]
        it["explanation_source"] = exp["explanation_source"]
        it["llm_used"] = exp["llm_used"]
        it["grounded"] = exp["grounded"]
        it["evidence"] = exp["evidence"]
        out.append(it)
    return out