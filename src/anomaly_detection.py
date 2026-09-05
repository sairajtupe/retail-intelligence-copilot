"""Promotion-aware anomaly detection.

Distinguishes natural demand movements from promotion-driven ones using the
promotions table (PRM rules in documents/sales_anomaly_policy.md).
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Any
from statistics import mean, pstdev

from src.config import THRESHOLDS
import src.database as db
from src.utils import safe_float, safe_int, parse_date, overlaps, steady_id, fmt_num


def _pair_history(days: int = 60) -> dict[tuple, dict]:
    """Return per (store, product) daily units over the trailing window."""
    start = (parse_date(db.max_date()) - timedelta(days=days + 2)).isoformat()
    rows = db.query_df(f"""
        SELECT date, store_id, product_id, SUM(units_sold) units
        FROM sales WHERE date >= '{start}'
        GROUP BY date, store_id, product_id ORDER BY date""")
    out: dict[tuple, dict] = {}
    for r in rows:
        key = (r["store_id"], r["product_id"])
        d = out.setdefault(key, {"daily": {}, "last_day": None})
        d["daily"][parse_date(r["date"])] = safe_float(r["units"])
        d["last_day"] = r["date"]
    return out


def _active_promos(window_start: date, window_end: date) -> list[dict]:
    rows = db.query_df(f"""
        SELECT promo_id, promotion_name, product_id, store_id,
               start_date, end_date, discount_pct, demand_lift_x
        FROM promotions
        WHERE end_date >= '{window_start.isoformat()}' AND start_date <= '{window_end.isoformat()}'""")
    for r in rows:
        r["start_date"] = parse_date(r["start_date"])
        r["end_date"] = parse_date(r["end_date"])
    return rows


def detect_anomalies(limit: int = 100, days: int = 60) -> list[dict]:
    """Detect SPIKE/DROP signals per store-product and mark promo overlap.

    A signal is recorded as a genuine (sales) anomaly only when no promotion
    overlaps it; otherwise it is tagged promo-driven and surfaced as such.
    """
    history = _pair_history(days)
    promos = _active_promos(parse_date(db.max_date()) - timedelta(days=14),
                            parse_date(db.max_date()))

    promo_by_pair: dict[tuple, list] = {}
    for p in promos:
        promo_by_pair.setdefault((p["store_id"], p["product_id"]), []).append(p)

    md = parse_date(db.max_date()) or date.today()
    w7_start = md - timedelta(days=7)
    w30_start = md - timedelta(days=30)
    w60_start = md - timedelta(days=60)

    findings: list[dict] = []
    for key, h in history.items():
        daily = h["daily"]
        recent = [v for d, v in sorted(daily.items()) if w7_start < d <= md]
        prior = [v for d, v in sorted(daily.items()) if w30_start < d <= w7_start]
        window = [v for d, v in sorted(daily.items()) if w60_start < d <= md]
        if len(window) < 21 or len(recent) < 4 or len(prior) < 4:
            continue

        r7, r30 = mean(recent), mean(prior)
        if plus_eps_recent := r7 <= 0 or r30 <= 0:
            if r7 > 0 and r30 == 0:
                r30 = r7 * 0.05

        sd = pstdev(recent) if len(recent) > 1 else 0
        z = (r7 - r30) / (sd + 1e-9) if r30 > 0 else 0
        change_pct = (r7 - r30) / (r30 + 1e-9) * 100 if r30 > 0 else 0

        signal = None
        if change_pct <= -THRESHOLDS["drop_threshold_pct"]:
            signal = "DROP"
        elif change_pct >= THRESHOLDS["promo_spike_override_pct"]:
            signal = "SPIKE"
        elif z > THRESHOLDS["spike_z_threshold"] and change_pct >= THRESHOLDS["drop_threshold_pct"]:
            signal = "SPIKE"

        if not signal:
            continue

        promo = _matching_promo(promo_by_pair.get(key, []), w7_start, md)

        # Promotion-verified spike: a promotion was live in the final week and
        # demand is meaningfully elevated - attribute the spike to the promo
        # rather than classifying it as a natural anomaly (PRM rules).
        if not promo and _matching_promo(promo_by_pair.get(key, []), md - timedelta(days=14), md) \
                and r30 > 0 and r7 / r30 >= 1.4:
            promo = _matching_promo(promo_by_pair.get(key, []), md - timedelta(days=14), md)
            signal = "SPIKE"

        store_id, product_id = key
        product = db.fetchone("SELECT product_name, category, brand FROM products WHERE product_id=?",
                              (product_id,))

        findings.append({
            "store_id": store_id,
            "product_id": product_id,
            "product_name": product["product_name"] if product else product_id,
            "category": product["category"] if product else "",
            "signal": signal,
            "anomaly_type": signal,
            "z_score": round(min(max(z, -999.99), 999.99), 2),
            "change_pct": round(change_pct, 1),
            "mean_units_7d": round(r7, 2),
            "mean_prior_30d": round(r30, 2),
            "promo_overlap": bool(promo),
            "promo_name": promo["promotion_name"] if promo else None,
            "promo_discount_pct": promo["discount_pct"] if promo else None,
            "verdict": "promotion_driven" if promo else "natural_signal",
            "evidence": [
                {"source": "sales/rolling-7d",
                 "text": f"Mean daily units last 7 days: {fmt_num(r7, 1)} vs {fmt_num(r30, 1)} in the prior 30"},
                *([{"source": "promotions",
                    "text": f"Promotion '{promo['promotion_name']}' active {promo['start_date']} to {promo['end_date']} "
                            f"(-{safe_int(promo['discount_pct'])}%, demand lift ~{fmt_num(promo['demand_lift_x'])}x)"}]
                   if promo else []),
            ],
            "policy_citation": "documents/sales_anomaly_policy.md [ANM-D-01/02/04, PRM-01]",
            "issue_id": steady_id(store_id, product_id, signal),
        })

    findings.sort(key=lambda x: (-abs(x["change_pct"])))
    return findings[:limit]


def _matching_promo(promos: list[dict], start: date, end: date) -> dict | None:
    for p in promos:
        if overlaps(start, end, p["start_date"], p["end_date"]):
            return p
    return None


def pair_anomalies(store_id: str, product_id: str, days: int = 60) -> dict | None:
    """Anomaly analysis for a single store-product pair (for product pages)."""
    md = parse_date(db.max_date()) or date.today()
    w7_start = md - timedelta(days=7)
    w30_start = md - timedelta(days=30)
    w60_start = md - timedelta(days=60)

    rows = db.query_df(f"""
        SELECT date, SUM(units_sold) units FROM sales
        WHERE store_id = '{store_id}' AND product_id = '{product_id}'
          AND date >= '{w60_start.isoformat()}'
        GROUP BY date ORDER BY date""")
    if not rows:
        return None
    daily = {parse_date(r["date"]): safe_float(r["units"]) for r in rows}
    if len(daily) < 21:
        return {"verdict": "insufficient_history", "signal": "INSUFFICIENT_HISTORY",
                "history_days": len(daily)}

    recent = [v for d, v in daily.items() if w7_start < d <= md]
    prior = [v for d, v in daily.items() if w30_start < d <= w7_start]
    r7, r30 = mean(recent), mean(prior)
    change_pct = (r7 - r30) / (r30 + 1e-9) * 100
    promo = _matching_promo([p for p in _active_promos(w7_start, md)
                             if p["store_id"] == store_id and p["product_id"] == product_id],
                            w7_start, md)
    if not promo:
        promo = _matching_promo([p for p in _active_promos(md - timedelta(days=14), md)
                                 if p["store_id"] == store_id and p["product_id"] == product_id],
                                w7_start, md)

    signal = "NORMAL"
    if change_pct <= -THRESHOLDS["drop_threshold_pct"]:
        signal = "DROP"
    elif change_pct >= THRESHOLDS["promo_spike_override_pct"] or (r30 > 0 and r7 / r30 >= 2.5):
        signal = "SPIKE"
    elif promo and r30 > 0 and r7 / r30 >= 1.4:
        signal = "SPIKE"

    return {"verdict": "promotion_driven" if promo else "natural_signal",
            "signal": signal,
            "mean_units_7d": round(r7, 2), "mean_prior_30d": round(r30, 2),
            "change_pct": round(change_pct, 1),
            "promo_overlap": bool(promo),
            "promo_name": promo["promotion_name"] if promo else None,
            "history_days": len(daily)}