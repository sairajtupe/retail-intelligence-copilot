"""Inventory engine: stock-out risk, reorder sizing, transfer opportunities.

All numbers are deterministic facts (DEF-01..08, STK-01..06, TRF-01..03).
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Any

from src.config import THRESHOLDS
import src.database as db
from src.utils import safe_float, safe_int, parse_date, fmt_num, fmt_money, steady_id


def _pair_rows() -> list[dict]:
    return db.query_df(f"""
        SELECT m.store_id, m.product_id, p.product_name, p.category,
               p.unit_cost, p.selling_price, p.minimum_order_quantity, p.target_stock_days,
               sup.avg_lead_time_days, sup.on_time_rate,
               m.stock, m.avg_daily_demand_30d, m.avg_daily_demand_7d,
               m.units_30d, m.units_7d, m.cover_days, m.lead_time_days,
               m.reorder_point, m.risk, m.risk_score, m.history_days,
               m.overstock, m.overstock_excess, m.transfer_flag
        FROM metrics_snapshot m
        JOIN products p ON p.product_id = m.product_id
        LEFT JOIN suppliers sup ON sup.supplier_id = p.supplier_id""")


def stockout_risk(store_id: str | None = None, limit: int = 30) -> list[dict]:
    pairs = _pair_rows()
    if store_id:
        pairs = [r for r in pairs if r["store_id"] == store_id]

    out = []
    for r in pairs:
        if r["history_days"] and r["history_days"] < THRESHOLDS["min_history_days"]:
            for rr in [r]:
                out.append(_risk_row(rr, "INSUFFICIENT_HISTORY"))
            continue
        if safe_int(r["stock"]) <= 0:
            out.append(_risk_row(r, "STOCKED_OUT"))
            continue
        cd = safe_float(r["cover_days"])
        lt = safe_int(r["lead_time_days"])
        if cd <= lt:
            out.append(_risk_row(r, "CRITICAL"))
        elif cd <= lt + 3:
            out.append(_risk_row(r, "HIGH"))
        elif cd <= lt * 1.5:
            out.append(_risk_row(r, "MEDIUM"))
    out.sort(key=lambda x: (x["risk"] != "CRITICAL", x["risk"] != "HIGH",
                            -safe_float(x["risk_score"])))
    return out[:limit]


def _risk_row(r: dict, level: str) -> dict:
    stock = safe_int(r["stock"])
    ads = safe_float(r["avg_daily_demand_30d"])
    lt = safe_int(r["lead_time_days"])
    lead_demand = ads * lt
    safety = max(ads * lt * THRESHOLDS["safety_stock_ratio"], THRESHOLDS["safety_stock_floor"])
    recommended = max(lead_demand + safety - stock, 0)

    return {
        "store_id": r["store_id"], "product_id": r["product_id"],
        "product_name": r["product_name"], "category": r["category"],
        "risk": level,
        "stock": stock,
        "avg_daily_demand_30d": round(ads, 2),
        "avg_daily_demand_7d": round(safe_float(r["avg_daily_demand_7d"]), 2),
        "lead_time_demand": round(lead_demand, 1),
        "safety_stock": round(safety, 1),
        "cover_days": round(safe_float(r["cover_days"]), 1),
        "lead_time_days": lt,
        "reorder_point": safe_int(r["reorder_point"]),
        "recommended_order_qty": int(round(recommended)),
        "risk_score": r["risk_score"],
        "supplier_lead_time": safe_int(r["avg_lead_time_days"] or lt),
        "supplier_on_time_rate": safe_float(r["on_time_rate"]),
        "evidence": _stockout_evidence(r, level),
    }


def _stockout_evidence(r: dict, level: str) -> list[dict]:
    stock = safe_int(r["stock"])
    ads = safe_float(r["avg_daily_demand_30d"])
    lt = safe_int(r["lead_time_days"])
    return [{
        "source": "metrics_snapshot/inventory",
        "text": (f"Stock {stock} units, ADS_30D {fmt_num(ads, 2)}, "
                 f"cover {fmt_num(safe_float(r['cover_days']), 1)}d, "
                 f"lead time {lt}d, reorder point {safe_int(r['reorder_point'])}"),
        "calculation": f"cover = stock / ADS_30D = {stock} / {fmt_num(ads, 3)} = {fmt_num(safe_float(r['cover_days']), 1)}d",
        "formula": "cover_days = stock / avg_daily_demand_30d (DEF-02)",
        "policy": "documents/inventory_policy.md; documents/replenishment_policy.md [STK-01]",
    }, {
        "source": "products/suppliers",
        "text": f"Lead time {lt}d, safety stock floor {fmt_num(THRESHOLDS['safety_stock_floor'])}",
        "policy": "documents/data_definitions.md [DEF-03..05]",
    }]


def reorder_suggestions(store_id: str | None = None, limit: int = 20) -> list[dict]:
    pairs = _pair_rows()
    if store_id:
        pairs = [r for r in pairs if r["store_id"] == store_id]
    out = []
    for r in pairs:
        if safe_int(r["risk_score"] or 0) < 0.4 and r["risk"] not in ("CRITICAL", "HIGH"):
            continue
        if r["risk"] in ("INSUFFICIENT_HISTORY",):
            continue
        ads = round(safe_float(r["avg_daily_demand_30d"]), 2)
        lt = safe_int(r["lead_time_days"])
        target = ads * lt + max(ads * lt * THRESHOLDS["safety_stock_ratio"], THRESHOLDS["safety_stock_floor"])
        order = max(target - safe_int(r["stock"]), 0)
        out.append({
            "store_id": r["store_id"], "product_id": r["product_id"],
            "product_name": r["product_name"],
            "current_stock": safe_int(r["stock"]),
            "avg_daily_demand_30d": round(ads, 2),
            "lead_time_days": lt,
            "target_level": round(target, 1),
            "suggested_quantity": int(round(order)),
            "risk": r["risk"],
            "evidence": [{"source": "reorder-point calc",
                          "text": f"target = ADS_30D×lead_time×1.3 = {fmt_num(ads,2)}×{lt}×1.3 = {fmt_num(target,1)}; "
                                  f"order = target − stock = {fmt_num(target,1)} − {safe_int(r['stock'])} = {fmt_num(order,0)}",
                          "formula": "reorder_quantity = max(target_stock_qty − current_stock, 0) (REC-04)",
                          "policy": "documents/replenishment_policy.md [STK-04]"}],
        })
    out.sort(key=lambda x: -x["suggested_quantity"])
    return out[:limit]


def transfer_opportunities(store_id: str | None = None, limit: int = 20) -> list[dict]:
    """Pairs where one store is overstocked (excess) and another is at risk.

    TRF-01: source cover >> destination cover with excess on the source side.
    """
    rows = db.query_df("""
        SELECT p.product_id, p.product_name, p.unit_cost,
               sa.store_id AS source_store, sa.stock AS source_stock,
               sa.cover_days AS source_cover, sa.overstock_excess AS source_excess,
               sb.store_id AS dest_store, sb.stock AS dest_stock,
               sb.cover_days AS dest_cover, sb.risk AS dest_risk,
               sb.avg_daily_demand_30d AS dest_ads
        FROM metrics_snapshot sa
        JOIN metrics_snapshot sb ON sa.product_id = sb.product_id AND sa.store_id <> sb.store_id
        JOIN products p ON p.product_id = sa.product_id
        WHERE sa.overstock = 1 AND sa.overstock_excess > 10
          AND sb.avg_daily_demand_30d > 0
          AND sb.cover_days <= 4 AND sb.risk IN ('CRITICAL','HIGH')
          AND sa.cover_days > sb.cover_days + 20""")
    out = []
    for r in rows:
        if store_id and r["source_store"] != store_id and r["dest_store"] != store_id:
            continue
        qty = min(int(safe_float(r["source_excess"])),
                  max(int(safe_float(r["dest_ads"])) * 7, 10))
        out.append({
            "product_id": r["product_id"], "product_name": r["product_name"],
            "from_store": r["source_store"], "to_store": r["dest_store"],
            "source_stock": safe_int(r["source_stock"]),
            "source_cover_days": round(safe_float(r["source_cover"]), 1),
            "source_excess": safe_int(r["source_excess"]),
            "dest_stock": safe_int(r["dest_stock"]),
            "dest_cover_days": round(safe_float(r["dest_cover"]), 1),
            "dest_risk": r["dest_risk"],
            "recommended_units": qty,
            "transfer_value": round(qty * safe_float(r["unit_cost"]), 2),
            "evidence": [{"source": "metrics_snapshot cross-store",
                          "text": (f"Source {r['source_store']}: stock {safe_int(r['source_stock'])}, "
                                   f"cover {fmt_num(safe_float(r['source_cover']),1)}d, EXCESS {safe_int(r['source_excess'])}. "
                                   f"Dest {r['dest_store']}: stock {safe_int(r['dest_stock'])}, "
                                   f"cover {fmt_num(safe_float(r['dest_cover']),1)}d ({r['dest_risk']})"),
                          "policy": "documents/store_operations_policy.md [TRF-01..03]"}],
        })
    return sorted(out, key=lambda x: -x["recommended_units"])[:limit]