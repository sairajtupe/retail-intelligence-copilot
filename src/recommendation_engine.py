"""Recommendation engine.

Builds structured recommendations from deterministic analytics. Rules in
documents/recommendation_policy.md (REC-01..08) and OVR-01..07.
"""
from __future__ import annotations
from typing import Any

import src.database as db
from src.utils import safe_float, safe_int, fmt_num, fmt_money, steady_id


def build_for(store_id: str | None = None, max_items: int = 30,
              include_suppressions: bool = False) -> list[dict]:
    rows = db.query_df(f"""
        SELECT m.store_id, m.product_id, p.product_name, p.category, p.brand,
               p.unit_cost, p.selling_price, p.minimum_order_quantity,
               m.stock, m.units_7d, m.units_30d, m.avg_daily_demand_30d,
               m.avg_daily_demand_7d, m.cover_days, m.lead_time_days, m.reorder_point, m.history_days,
               m.risk, m.risk_score, m.anomaly, m.overstock, m.slow_moving,
               m.dead_stock, m.transfer_flag, m.data_quality,
               m.overstock_value, m.days_since_last_sale
        FROM metrics_snapshot m JOIN products p ON p.product_id = m.product_id
        WHERE m.history_days = 0 OR m.history_days >= 21""" +
        (f" AND m.store_id = '{store_id}'" if store_id else "") +
        " ORDER BY m.risk_score DESC, m.overstock_value DESC, m.stock DESC")

    out: list[dict] = []
    for r in rows:
        rec = _recommend_for(r)
        if rec:
            out.append(rec)
        if len(out) >= max_items and not include_suppressions:
            break
    return out[: max_items if not include_suppressions else max_items]


def _recommend_for(r: dict) -> dict | None:
    stock = safe_int(r["stock"])
    ads = safe_float(r["avg_daily_demand_30d"])
    lt = safe_int(r["lead_time_days"])
    cover = safe_float(r["cover_days"])

    if r["data_quality"] == "NEGATIVE_STOCK":
        return _rec(r, "DATA_QUALITY_ISSUE", "HIGH",
                    "Reconcile stock and correct the negative closing stock record",
                    f"Negative closing stock recorded with {safe_int(r['history_days'])}d history",
                    "documents/edge_cases.md [EDG-02]",
                    {"stock": stock, "current_cover_days": cover})

    if r["risk"] in ("STOCKED_OUT", "CRITICAL", "HIGH"):
        lead_demand = ads * lt
        safety = max(ads * lt * 0.3, 5.0)
        order = int(round(max(lead_demand + safety - stock, 0)))
        action = "Place an urgent replenishment order" if r["risk"] in ("STOCKED_OUT", "CRITICAL") else "Expedite replenishment"
        return _rec(r, "STOCKOUT_RISK", "CRITICAL" if r["risk"] in ("STOCKED_OUT", "CRITICAL") else "HIGH",
                    f"{action} for {order if order > 0 else 'at least'} units",
                    f"stock {stock} vs reorder point {safe_int(r['reorder_point'])}; "
                    f"cover {fmt_num(cover,1)}d vs lead time {lt}d",
                    "documents/replenishment_policy.md [STK-01, STK-04]; documents/data_definitions.md [DEF-06]",
                    {"stock": stock, "cover_days": cover, "reorder_point": safe_int(r["reorder_point"]),
                     "recommended_order_qty": order})

    if r["overstock"]:
        excess = safe_int(r["overstock_value"])
        reduction = int(safe_float(r["stock"]) - max(ads * 90, 0))
        return _rec(r, "OVERSTOCK", "MEDIUM",
                    f"Reduce stock to free liquidity (e.g. promote, transfer, or stop reordering)",
                    f"cover {fmt_num(cover,1)}d exceeds {90}d with {safe_int(r['units_30d'])} units moved in 30d",
                    "documents/overstock_policy.md [OVR-01..07]",
                    {"stock": stock, "cover_days": cover, "units_30d": safe_int(r["units_30d"]),
                     "overstock_excess_units": excess})

    if r["slow_moving"] or r["dead_stock"]:
        kind = "SLOW_MOVING" if r["slow_moving"] else "DEAD_STOCK"
        return _rec(r, kind, "MEDIUM" if kind == "SLOW_MOVING" else "LOW",
                    "Bundle, discount, or delist; stop future orders",
                    f"{'<1 unit/day' if kind == 'SLOW_MOVING' else '0 units in 30d'}, cover {fmt_num(cover,1)}d",
                    "documents/slow_moving_policy.md [SLM-03]",
                    {"stock": stock, "cover_days": cover, "units_30d": safe_int(r["units_30d"])})

    if r["anomaly"] == "SPIKE":
        return _rec(r, "SALES_SPIKE", "MEDIUM",
                    "Check supply continuity; confirm orders cover the accelerated demand",
                    f"{fmt_num(r['avg_daily_demand_7d'],2)} ADS(7d) vs {fmt_num(ads,2)} ADS(30d)",
                    "documents/sales_anomaly_policy.md [ANM-D-01/03]",
                    {"units_7d": safe_int(r["units_7d"]), "units_30d": safe_int(r["units_30d"])})

    if r["anomaly"] == "DROP":
        return _rec(r, "SALES_DROP", "MEDIUM",
                    "Review pricing/availability; check for promotion overlap on competing items",
                    f"{fmt_num(r['avg_daily_demand_7d'],2)} ADS(7d) vs {fmt_num(ads,2)} ADS(30d)",
                    "documents/sales_anomaly_policy.md [ANM-D-02/05]",
                    {"units_7d": safe_int(r["units_7d"]), "units_30d": safe_int(r["units_30d"])})

    return None


def _rec(r: dict, issue_type: str, priority: str, action: str, reason: str,
         policy: str, metrics: dict) -> dict:
    return {
        "store_id": r["store_id"],
        "product_id": r["product_id"],
        "product_name": r["product_name"],
        "category": r["category"],
        "issue_type": issue_type,
        "priority": priority,
        "recommended_action": action,
        "reason": reason,
        "metrics": metrics,
        "assumptions": [
            "Demand stays at the trailing 30-day rate absent a promotion",
            "Supply lead time equals the supplier's recorded lead time",
        ],
        "evidence": _rec_evidence(r, issue_type),
        "policy_citations": [policy],
        "confidence": _confidence(issue_type, r),
    }


def _rec_evidence(r: dict, issue_type: str) -> list[dict]:
    e = [{
        "source": "metrics_snapshot",
        "record": {
            "store_id": r["store_id"], "product_id": r["product_id"],
            "product_name": r["product_name"], "category": r["category"],
            "stock": safe_int(r["stock"]),
            "units_30d": safe_int(r["units_30d"]),
            "units_7d": safe_int(r["units_7d"]),
            "cover_days": round(safe_float(r["cover_days"]), 1),
            "lead_time_days": safe_int(r["lead_time_days"]),
            "reorder_point": safe_int(r["reorder_point"]),
            "risk": r["risk"],
        },
        "formulas": ["cover_days = stock / ADS_30D (DEF-02)",
                     "risk_class: STOCKED_OUT/CRITICAL/HIGH/MEDIUM/LOW (DEF-07)"],
    }]
    if r["risk"] in ("STOCKED_OUT", "CRITICAL", "HIGH", "MEDIUM"):
        e.append({"source": "replenishment",
                  "calculation": f"order_qty = max(ADS_30D×lead_time×1.3 − stock, 0) "
                                 f"= max({fmt_num(safe_float(r['avg_daily_demand_30d']),2)}×{safe_int(r['lead_time_days'])}×1.3 − {safe_int(r['stock'])} , 0)",
                  "policy": "documents/replenishment_policy.md [STK-04, REC-04]"})
    return e


def _confidence(issue_type: str, r: dict) -> str:
    history = safe_int(r["history_days"] or 0)
    if history < 21:
        return "LOW"
    base = {"STOCKOUT_RISK": "HIGH", "DATA_QUALITY_ISSUE": "HIGH",
            "OVERSTOCK": "MEDIUM", "SLOW_MOVING": "MEDIUM", "DEAD_STOCK": "MEDIUM",
            "SALES_SPIKE": "MEDIUM", "SALES_DROP": "MEDIUM"}.get(issue_type, "MEDIUM")
    if issue_type == "STOCKOUT_RISK" and history >= 60 and safe_float(r["risk_score"] or 0) >= 0.7:
        return "HIGH"
    return base


def suppression_reasons() -> list[dict]:
    """Items intentionally not recommended and why (REC-08 / EDG)."""
    rows = db.query_df("""
        SELECT m.store_id, m.product_id, p.product_name, p.category, m.stock,
               m.history_days, m.data_quality, m.risk
        FROM metrics_snapshot m JOIN products p ON p.product_id = m.product_id
        WHERE (m.history_days > 0 AND m.history_days < 21) OR m.data_quality != ''
        ORDER BY m.history_days""")
    return [{
        "store_id": r["store_id"], "product_id": r["product_id"],
        "product_name": r["product_name"],
        "history_days": safe_int(r["history_days"]),
        "data_quality": r["data_quality"] or "",
        "reason": ("Insufficient history" if r["data_quality"] == "INSUFFICIENT_HISTORY"
                   else "Negative stock record" if r["data_quality"] == "NEGATIVE_STOCK"
                   else f"History {safe_int(r['history_days'])}d < 21d minimum (REC-08)"),
        "policy": "documents/recommendation_policy.md [REC-08]",
    } for r in rows]