"""Deterministic sales & inventory analytics engine.

Everything here is computed from local SQLite + pandas. This is the source of
truth; the LLM only explains these facts.
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Any

from src.config import THRESHOLDS, log
import src.database as db
from src.utils import safe_float, safe_int, steady_id, fmt_num, fmt_money, parse_date


def _last_n_days(n: int) -> list[str]:
    md = db.max_date("sales", "date")
    end = parse_date(md) or date.today()
    return [(end - timedelta(days=n - i)).isoformat() for i in range(n)]


def max_date() -> str:
    return db.max_date("sales", "date") or ""


def dashboard_kpis(store_ids: list[str] | None = None) -> dict[str, Any]:
    """Top-level KPI cards for the Overview screen."""
    md = max_date()
    d30 = (parse_date(md) - timedelta(days=30)).isoformat()
    d7 = (parse_date(md) - timedelta(days=7)).isoformat()

    _stores_sql = ", ".join("'" + s + "'" for s in store_ids) if store_ids else ""
    sid = f" AND store_id IN ({_stores_sql})" if store_ids else ""
    mid = f" AND m.store_id IN ({_stores_sql})" if store_ids else ""

    sales30 = db.fetchone(f"""
        SELECT SUM(units_sold) units, SUM(revenue) revenue,
               SUM(gross_margin) margin, COUNT(DISTINCT date) days
        FROM daily_sales_summary WHERE date >= '{d30}'{sid}""") or {}
    sales7 = db.fetchone(f"""
        SELECT SUM(units_sold) units, SUM(revenue) revenue
        FROM daily_sales_summary WHERE date >= '{d7}'{sid}""") or {}

    inv_value = db.scalar(f"""
        SELECT SUM(m.stock * p.unit_cost) FROM metrics_snapshot m
        JOIN products p ON p.product_id = m.product_id WHERE m.stock > 0{mid}""", default=0.0)

    counts = db.query_df(f"""
        SELECT risk, COUNT(*) c FROM metrics_snapshot m WHERE 1=1{mid} GROUP BY risk""")
    risk = {r["risk"]: r["c"] for r in counts}
    overstock = db.scalar(f"SELECT COUNT(*) FROM metrics_snapshot m WHERE m.overstock=1{mid}")
    slow = db.scalar(f"SELECT COUNT(*) FROM metrics_snapshot m WHERE m.slow_moving=1{mid}")
    dead = db.scalar(f"SELECT COUNT(*) FROM metrics_snapshot m WHERE m.dead_stock=1{mid}")

    prev = db.fetchone(f"""
        SELECT SUM(units_sold) units, SUM(revenue) revenue
        FROM daily_sales_summary WHERE date BETWEEN '{_iso(parse_date(d30) - timedelta(days=30))}' AND '{d30}'{sid}""") or {}

    revenue_pct = _pct_change(sales30.get("revenue"), prev.get("revenue"))
    units_pct = _pct_change(sales30.get("units"), prev.get("units"))

    return {
        "as_of_date": md,
        "window_days": 30,
        "kpis": {
            "revenue_30d": _num(sales30.get("revenue")),
            "revenue_change_pct": revenue_pct,
            "units_30d": safe_int(sales30.get("units")),
            "units_change_pct": units_pct,
            "margin_30d": _num(sales30.get("margin")),
            "revenue_7d": _num(sales7.get("revenue")),
            "units_7d": safe_int(sales7.get("units")),
            "inventory_value": _num(inv_value),
            "stockout_critical": safe_int(risk.get("CRITICAL")),
            "stockout_high": safe_int(risk.get("HIGH")),
            "stocked_out": safe_int(risk.get("STOCKED_OUT")),
            "overstock_items": safe_int(overstock),
            "slow_moving_items": safe_int(slow),
            "dead_stock_items": safe_int(dead),
            "product_store_pairs": (safe_int(risk.get("LOW"))
                                    + safe_int(risk.get("MEDIUM"))
                                    + safe_int(risk.get("HIGH"))
                                    + safe_int(risk.get("CRITICAL"))
                                    + safe_int(risk.get("STOCKED_OUT"))),
        },
        "active_days_30": safe_int(sales30.get("days")),
    }


def _pct_change(cur: Any, prev: Any) -> float:
    c, p = safe_float(cur), safe_float(prev)
    if p == 0:
        return 0.0
    return round((c - p) / abs(p) * 100, 1)


def _num(v: Any) -> float:
    return round(safe_float(v), 2)


def _iso(d: date) -> str:
    return d.isoformat()


def trend(days: int = 30, store_ids: list[str] | None = None) -> list[dict]:
    start = (parse_date(max_date()) - timedelta(days=days - 1)).isoformat()
    sids = ", ".join("'" + s + "'" for s in store_ids) if store_ids else ""
    sid = f" AND store_id IN ({sids})" if sids else ""
    rows = db.query_df(f"""
        SELECT date, SUM(units_sold) units, SUM(revenue) revenue,
               SUM(gross_margin) margin
        FROM daily_sales_summary WHERE date >= '{start}'{sid}
        GROUP BY date ORDER BY date""")
    return [{"date": r["date"], "units": safe_int(r["units"]),
             "revenue": _num(r["revenue"]), "margin": _num(r["margin"])} for r in rows]


def category_performance(days: int = 30, store_ids: list[str] | None = None) -> list[dict]:
    start = (parse_date(max_date()) - timedelta(days=days)).isoformat()
    sids = ", ".join("'" + s + "'" for s in store_ids) if store_ids else ""
    sid = f" AND s.store_id IN ({sids})" if sids else ""
    rows = db.query_df(f"""
        SELECT p.category, SUM(s.units_sold) units, SUM(s.revenue) revenue,
               SUM(s.gross_margin) margin
        FROM sales s JOIN products p ON p.product_id = s.product_id
        WHERE s.date >= '{start}'{sid}
        GROUP BY p.category ORDER BY revenue DESC""")
    total_rev = sum(r["revenue"] for r in rows) or 1.0
    out = []
    for r in rows:
        out.append({
            "category": r["category"], "units": safe_int(r["units"]),
            "revenue": _num(r["revenue"]), "margin": _num(r["margin"]),
            "share_pct": round(safe_float(r["revenue"]) / total_rev * 100, 1),
        })
    return out


def store_comparison(store_ids: list[str] | None = None) -> list[dict]:
    md = parse_date(max_date())
    d30 = (md - timedelta(days=30)).isoformat()
    d60 = (md - timedelta(days=60)).isoformat()
    sids = ", ".join("'" + s + "'" for s in store_ids) if store_ids else ""
    sid = f" AND s.store_id IN ({sids})" if sids else ""
    rows = db.query_df(f"""
        SELECT s.store_id, st.store_name, st.region, st.store_type,
               SUM(s.units_sold) units, SUM(s.revenue) revenue, SUM(s.gross_margin) margin
        FROM daily_sales_summary s
        JOIN stores st ON st.store_id = s.store_id
        WHERE s.date >= '{d30}'{sid} GROUP BY s.store_id""")
    prev = db.query_df(f"""
        SELECT store_id, SUM(units_sold) units, SUM(revenue) revenue
        FROM daily_sales_summary WHERE date >= '{d60}' AND date < '{d30}'
        GROUP BY store_id""")
    prev_map = {r["store_id"]: r for r in prev}
    out = []
    for r in rows:
        p = prev_map.get(r["store_id"], {})
        revenue = safe_float(r["revenue"])
        units = safe_float(r["units"])
        out.append({
            "store_id": r["store_id"], "store_name": r["store_name"],
            "region": r["region"], "store_type": r["store_type"],
            "revenue_30d": _num(revenue), "units_30d": safe_int(units),
            "margin_30d": _num(r["margin"]),
            "revenue_per_unit": round(revenue / units, 2) if units else 0,
            "margin_pct": round(safe_float(r["margin"]) / revenue * 100, 1) if revenue else 0,
            "revenue_change_pct": _pct_change(revenue, p.get("revenue")),
            "units_change_pct": _pct_change(units, p.get("units")),
        })
    return sorted(out, key=lambda x: -x["revenue_30d"])


def top_products(days: int = 30, limit: int = 10, store_ids: list[str] | None = None) -> list[dict]:
    start = (parse_date(max_date()) - timedelta(days=days)).isoformat()
    sids = ", ".join("'" + s + "'" for s in store_ids) if store_ids else ""
    sid = f" AND s.store_id IN ({sids})" if sids else ""
    rows = db.query_df(f"""
        SELECT s.product_id, p.product_name, p.category,
               SUM(s.units_sold) units, SUM(s.revenue) revenue, SUM(s.gross_margin) margin
        FROM sales s JOIN products p ON p.product_id = s.product_id
        WHERE s.date >= '{start}'{sid}
        GROUP BY s.product_id ORDER BY revenue DESC LIMIT {limit}""")
    return [{**r, "units": safe_int(r["units"]), "revenue": _num(r["revenue"]),
             "margin": _num(r["margin"])} for r in rows]


def inventory_health() -> dict[str, Any]:
    """Inventory health aggregates for the Inventory section."""
    total_value = db.scalar("""
        SELECT SUM(m.stock * p.unit_cost) FROM metrics_snapshot m
        JOIN products p ON p.product_id = m.product_id WHERE m.stock > 0""", default=0.0)
    overstock_value = db.scalar("""
        SELECT SUM(m.overstock_excess * p.unit_cost) FROM metrics_snapshot m
        JOIN products p ON p.product_id = m.product_id WHERE m.overstock = 1""", default=0.0)
    dead_value = db.scalar("""
        SELECT SUM(m.stock * p.unit_cost) FROM metrics_snapshot m
        JOIN products p ON p.product_id = m.product_id WHERE m.dead_stock = 1""", default=0.0)

    turnover = db.fetchone(f"""
        SELECT (SUM(s.cost) / NULLIF(SUM(m.stock * p.unit_cost), 0)) AS ratio
        FROM sales s
        JOIN products p ON p.product_id = s.product_id
        JOIN metrics_snapshot m ON m.product_id = s.product_id AND m.store_id = s.store_id
        WHERE s.date >= '{_iso(parse_date(max_date()) - timedelta(days=30))}'
          AND m.stock > 0""")

    buckets = [
        {"name": "Healthy", "lower": 0, "upper": 45},
        {"name": "Watch", "lower": 45, "upper": 90},
        {"name": "Overstocked", "lower": 90, "upper": 1e18},
    ]
    health = []
    total_pairs = db.scalar("SELECT COUNT(*) FROM metrics_snapshot WHERE stock > 0", default=1) or 1
    for b in buckets:
        n = db.scalar(f"""
            SELECT COUNT(*) FROM metrics_snapshot
            WHERE stock > 0 AND cover_days > {b['lower']} AND cover_days <= {b['upper']}""")
        health.append({"bucket": b["name"], "count": safe_int(n),
                       "pct": round(safe_int(n) / total_pairs * 100, 1)})
    total_pct = round(sum(x["pct"] for x in health), 1)
    if total_pct != 100.0:
        largest = max(range(len(health)), key=lambda i: health[i]["count"])
        health[largest]["pct"] = round(health[largest]["pct"] + (100.0 - total_pct), 1)

    return {
        "total_inventory_value": _num(total_value),
        "overstock_value": _num(overstock_value),
        "overstock_excess_units": safe_int(db.scalar(
            "SELECT SUM(overstock_excess) FROM metrics_snapshot WHERE overstock=1")),
        "dead_stock_value": _num(dead_value),
        "inventory_turnover_annualized":
            round(safe_float(turnover["ratio"]) * 12, 2) if turnover and safe_float(turnover["ratio"]) > 0 else 0,
        "coverage_buckets": health,
    }


# ---------------------------------------------------------------------------
# Attention items
# ---------------------------------------------------------------------------

def attention_items(scope: str = "all", store_id: str | None = None, max_items: int = 20,
                    include_low: bool = False) -> list[dict]:
    """Attention items surfaced on the dashboard (Attention Today section)."""
    items: list[dict] = []

    where = ("WHERE (m.dead_stock = 1"
             " OR m.data_quality = 'NEGATIVE_STOCK'"
             " OR (m.data_quality = '' AND m.history_days >= {minh}))").format(
                 minh=THRESHOLDS['min_history_days'])
    if store_id:
        where += f" AND m.store_id = '{store_id}'"

    pairs = db.query_df(f"""
        SELECT m.store_id, m.product_id, p.product_name, p.category, p.brand,
               p.selling_price, p.unit_cost,
               m.stock, m.units_7d, m.units_30d, m.avg_daily_demand_30d,
               m.cover_days, m.lead_time_days, m.reorder_point, m.history_days,
               m.risk, m.risk_score, m.anomaly, m.overstock, m.slow_moving,
               m.dead_stock, m.transfer_flag, m.data_quality,
               m.overstock_excess, m.overstock_value
        FROM metrics_snapshot m JOIN products p ON p.product_id = m.product_id
        {where}
        ORDER BY m.risk_score DESC, m.overstock_value DESC""")

    for r in pairs:
        if len(items) >= max_items:
            break
        row: dict = {
            "store_id": r["store_id"], "product_id": r["product_id"],
            "product_name": r["product_name"], "category": r["category"],
            "stock": safe_int(r["stock"]), "reorder_point": safe_int(r["reorder_point"]),
            "current_scale": r["overstock_value"],
        }
        if scope in ("all", "stockout") and r["risk"] in ("CRITICAL", "HIGH"):
            items.append(_issue(r, "STOCKOUT_RISK", "HIGH" if r["risk"] == "CRITICAL" else "MEDIUM",
                                f"Stock-out risk: {safe_int(r['stock'])} units, "
                                f"{_fmt(safe_float(r['cover_days']))} days of cover vs "
                                f"{safe_int(r['lead_time_days'])} day lead time"))
        if scope in ("all", "overstock") and r["overstock"]:
            items.append(_issue(r, "OVERSTOCK", "MEDIUM",
                                f"Overstock: {safe_int(r['stock'])} units, cover "
                                f"{_fmt(safe_float(r['cover_days']))} days, "
                                f"{_fmt(safe_float(r.get('overstock_excess')))} excess units "
                                f"({fmt_money(r.get('overstock_value'))})"))
        if scope in ("all", "slow_moving") and r["slow_moving"]:
            items.append(_issue(r, "SLOW_MOVING", "LOW",
                                f"Slow moving: only {safe_int(r['units_30d'])} units sold in 30 days "
                                f"against {safe_int(r['stock'])} in stock"))
        if scope in ("all", "spike") and r["anomaly"] == "SPIKE":
            items.append(_issue(r, "SALES_SPIKE", "MEDIUM",
                                f"Sales spike: {safe_int(r['units_7d'])} units in last 7 days "
                                f"vs {safe_int(r['units_30d'])} in prior 30"))
        if scope in ("all", "drop") and r["anomaly"] == "DROP":
            items.append(_issue(r, "SALES_DROP", "MEDIUM",
                                f"Sales drop: {safe_int(r['units_7d'])} units in last 7 days "
                                f"vs {safe_int(r['units_30d'])} in prior 30"))
        if scope in ("all", "dead_stock") and r["dead_stock"]:
            items.append(_issue(r, "DEAD_STOCK", "LOW",
                                f"Dead stock: no sales in 30 days, {safe_int(r['stock'])} units held"))
        if scope in ("all", "data_quality") and r["data_quality"] == "NEGATIVE_STOCK":
            items.append(_issue(r, "DATA_QUALITY_ISSUE", "HIGH", "Negative stock record detected"))

    return items[:max_items]


def _issue(r: dict, issue_type: str, priority: str, reason: str) -> dict:
    return {
        "issue_id": steady_id(r["store_id"], r["product_id"], issue_type),
        "issue_type": issue_type,
        "priority": priority,
        "store_id": r["store_id"], "product_id": r["product_id"],
        "product_name": r["product_name"], "category": r["category"],
        "reason": reason,
        "metrics": {
            "current_stock": safe_int(r["stock"]),
            "average_daily_sales_30d": _num(safe_float(r["avg_daily_demand_30d"])),
            "units_7d": safe_int(r["units_7d"]),
            "units_30d": safe_int(r["units_30d"]),
            "cover_days": _num(safe_float(r["cover_days"])),
            "lead_time_days": safe_int(r["lead_time_days"]),
            "reorder_point": safe_int(r["reorder_point"]),
            "risk_score": round(safe_float(r.get("risk_score")), 2) if r.get("risk_score") is not None else None,
            "overstock_excess": safe_int(r.get("overstock_excess")),
            "overstock_value": _num(r.get("overstock_value")),
        },
        "evidence": _issue_evidence(r, issue_type),
    }


def _issue_evidence(r: dict, issue_type: str) -> list[dict]:
    e: list[dict] = []
    if issue_type in ("STOCKOUT_RISK",):
        e.append({"source": "metrics_snapshot/inventory",
                  "text": (f"Store {r['store_id']} - {r['product_name']}: stock {safe_int(r['stock'])} units, "
                           f"ADS_30D {_fmt(safe_float(r['avg_daily_demand_30d']))}, "
                           f"lead time {safe_int(r['lead_time_days'])} days, "
                           f"cover {_fmt(safe_float(r['cover_days']))} days, "
                           f"reorder point {safe_int(r['reorder_point'])}")})
    elif issue_type in ("OVERSTOCK", "SLOW_MOVING", "DEAD_STOCK"):
        e.append({"source": "metrics_snapshot/inventory",
                  "text": (f"Store {r['store_id']} - {r['product_name']}: stock {safe_int(r['stock'])} units, "
                           f"units_30d {safe_int(r['units_30d'])}, "
                           f"units_7d {safe_int(r['units_7d'])}, "
                           f"cover {_fmt(safe_float(r['cover_days']))} days")})
    elif issue_type in ("SALES_SPIKE", "SALES_DROP"):
        e.append({"source": "sales/daily_sales_summary",
                  "text": (f"Store {r['store_id']} - {r['product_name']}: units_7d {safe_int(r['units_7d'])}, "
                           f"units_30d {safe_int(r['units_30d'])}, "
                           f"ADS_7D {_fmt(safe_float(r.get('avg_daily_demand_7d') or r['units_7d'] / 7.0))}, "
                           f"ADS_30D {_fmt(safe_float(r['avg_daily_demand_30d']))}")})
    elif issue_type == "DATA_QUALITY_ISSUE":
        e.append({"source": "inventory",
                  "text": (f"Store {r['store_id']} - {r['product_name']}: negative closing stock on record "
                           f"(history {safe_int(r['history_days'])} days)")})
    return e


def _fmt(x: float) -> str:
    return fmt_num(x)


def product_menu() -> list[dict]:
    """Compact product list for search/dropdown."""
    rows = db.query_df("""
        SELECT product_id, SKU, product_name, category, brand
        FROM products ORDER BY product_name""")
    return rows


# ---------------------------------------------------------------------------
# Overview / sections (extended)
# ---------------------------------------------------------------------------

def kpi_cards(store_ids: list[str] | None = None) -> list[dict]:
    """Ten KPI cards for the Overview top row (spec 3.2)."""
    k = dashboard_kpis(store_ids)["kpis"]
    md_ = parse_date(max_date())
    d30 = (md_ - timedelta(days=30)).isoformat()
    sid = ", ".join("'" + s + "'" for s in store_ids) if store_ids else ""
    active_stores = db.scalar(
        f"SELECT COUNT(DISTINCT store_id) FROM daily_sales_summary "
        f"WHERE date >= '{d30}'{f' AND store_id IN ({sid})' if sid else ''}", default=0)
    products_count = db.scalar("SELECT COUNT(*) FROM products", default=0)
    low_stock = safe_int(k["stockout_critical"]) + safe_int(k["stockout_high"]) + safe_int(k["stocked_out"])
    stockout_risks = safe_int(k["stockout_critical"]) + safe_int(k["stockout_high"])

    def rev_text(v: Any) -> str:
        return f"{v:+.1f}%" if v else "0.0%"

    return [
        {"key": "total_sales_30d", "label": "Total Sales (30d)",
         "value": _num(k["revenue_30d"]), "format": "money",
         "sub": f"{rev_text(k['revenue_change_pct'])} vs prior 30d"},
        {"key": "sales_growth", "label": "Sales Growth", "value": k["revenue_change_pct"],
         "format": "percent", "sub": "last 30d vs prior 30d"},
        {"key": "inventory_value", "label": "Inventory Value", "value": k["inventory_value"],
         "format": "money", "sub": "cost value on shelf"},
        {"key": "units_sold_30d", "label": "Units Sold (30d)", "value": safe_int(k["units_30d"]),
         "format": "number", "sub": f"{rev_text(k['units_change_pct'])} vs prior 30d"},
        {"key": "active_stores", "label": "Active Stores", "value": safe_int(active_stores),
         "format": "number", "sub": "selling in last 30d"},
        {"key": "products", "label": "Products", "value": safe_int(products_count),
         "format": "number", "sub": "SKUs in assortment"},
        {"key": "low_stock", "label": "Low Stock", "value": low_stock,
         "format": "number", "sub": "critical + high + stocked-out"},
        {"key": "stockout_risks", "label": "Stock-out Risks", "value": stockout_risks,
         "format": "number", "sub": "critical / high pairs"},
        {"key": "overstock", "label": "Overstock Items", "value": safe_int(k["overstock_items"]),
         "format": "number", "sub": "cover exceeds 90 days"},
        {"key": "slow_moving", "label": "Slow-moving Items", "value": safe_int(k["slow_moving_items"]),
         "format": "number", "sub": "low-turnover pairs"},
    ]


_PERIODS = [
    ("today", 1), ("last_7_days", 7), ("last_30_days", 30),
    ("last_90_days", 90), ("last_365_days", 365),
]


def sales_summary(store_ids: list[str] | None = None) -> dict:
    """Revenue/units/margin by period with previous-period comparison."""
    md_ = parse_date(max_date())
    sid = ", ".join("'" + s + "'" for s in store_ids) if store_ids else ""
    sid_sql = f" AND store_id IN ({sid})" if sid else ""
    periods = []
    for key, days in _PERIODS:
        cur_start = (md_ - timedelta(days=days)).isoformat()
        prev_start = (md_ - timedelta(days=2 * days)).isoformat()
        cur = db.fetchone(f"""
            SELECT SUM(units_sold) units, SUM(revenue) revenue, SUM(gross_margin) margin
            FROM daily_sales_summary WHERE date > '{cur_start}'{sid_sql}""") or {}
        prev = db.fetchone(f"""
            SELECT SUM(units_sold) units, SUM(revenue) revenue, SUM(gross_margin) margin
            FROM daily_sales_summary WHERE date > '{prev_start}' AND date <= '{cur_start}'{sid_sql}""") or {}
        periods.append({
            "period": key, "days": days,
            "revenue": _num(cur.get("revenue")), "units": safe_int(cur.get("units")),
            "margin": _num(cur.get("margin")),
            "revenue_change_pct": _pct_change(cur.get("revenue"), prev.get("revenue")),
            "units_change_pct": _pct_change(cur.get("units"), prev.get("units")),
        })
    lifetime = db.fetchone(f"""
        SELECT SUM(units_sold) units, SUM(revenue) revenue, SUM(gross_margin) margin
        FROM daily_sales_summary WHERE 1=1{sid_sql}""") or {}
    return {
        "as_of_date": max_date(),
        "periods": periods,
        "lifetime": {
            "revenue": _num(lifetime.get("revenue")),
            "units": safe_int(lifetime.get("units")),
            "margin": _num(lifetime.get("margin")),
            "days": db.scalar(f"SELECT COUNT(DISTINCT date) FROM daily_sales_summary WHERE 1=1{sid_sql}", default=0),
        },
    }


def coverage_distribution(store_ids: list[str] | None = None) -> list[dict]:
    """Days-of-cover histogram: 0-1, 1-3, 3-7, 7-14, 14-30, 30+ days."""
    mid = ", ".join("'" + s + "'" for s in store_ids) if store_ids else ""
    mid_sql = f" AND m.store_id IN ({mid})" if mid else ""
    bins = [
        ("0 - 1 days", 0, 1), ("1 - 3 days", 1, 3), ("3 - 7 days", 3, 7),
        ("7 - 14 days", 7, 14), ("14 - 30 days", 14, 30), ("30+ days", 30, 1e18),
    ]
    total = db.scalar(f"SELECT COUNT(*) FROM metrics_snapshot m WHERE m.stock > 0{mid_sql}", default=1) or 1
    out = []
    for label, lo, hi in bins:
        n = db.scalar(f"SELECT COUNT(*) FROM metrics_snapshot m WHERE m.stock > 0 AND m.cover_days > {lo} AND m.cover_days <= {hi}{mid_sql}", default=0)
        out.append({"bucket": label, "count": safe_int(n),
                    "pct": round(safe_int(n) / total * 100, 1)})
    total_pct = round(sum(x["pct"] for x in out), 1)
    if total_pct != 100.0:
        largest = max(range(len(out)), key=lambda i: out[i]["count"])
        out[largest]["pct"] = round(out[largest]["pct"] + (100.0 - total_pct), 1)
    return out


def status_buckets(store_ids: list[str] | None = None) -> dict:
    """Donut-style inventory health segmentation across stocked pairs."""
    mid = ", ".join("'" + s + "'" for s in store_ids) if store_ids else ""
    mid_sql = f" AND m.store_id IN ({mid})" if mid else ""
    row = db.fetchone(f"""
        SELECT
          SUM(CASE WHEN m.stock <= 0 THEN 1 ELSE 0 END) out_of_stock,
          SUM(CASE WHEN m.risk = 'CRITICAL' THEN 1 ELSE 0 END) critical,
          SUM(CASE WHEN m.risk IN ('HIGH','MEDIUM') THEN 1 ELSE 0 END) low_stock,
          SUM(CASE WHEN m.overstock = 1 THEN 1 ELSE 0 END) overstock,
          SUM(CASE WHEN m.dead_stock = 1 THEN 1 ELSE 0 END) dead_stock,
          SUM(CASE WHEN m.history_days > 0 AND m.history_days < 21 THEN 1 ELSE 0 END) insufficient,
          COUNT(*) total
        FROM metrics_snapshot m WHERE 1=1{mid_sql}""") or {}

    def _g(key: str) -> int:
        return safe_int(row.get(key))

    total = _g("total")
    accounted = sum(_g(k) for k in
                    ("out_of_stock", "critical", "low_stock", "overstock", "dead_stock", "insufficient"))
    buckets = [
        {"status": "Healthy", "count": max(total - accounted, 0), "color": "#22c55e"},
        {"status": "Low Stock", "count": _g("low_stock"), "color": "#f59e0b"},
        {"status": "Critical", "count": _g("critical"), "color": "#ef4444"},
        {"status": "Overstock", "count": _g("overstock"), "color": "#8b5cf6"},
        {"status": "Out of Stock", "count": _g("out_of_stock"), "color": "#64748b"},
        {"status": "Dead Stock", "count": _g("dead_stock"), "color": "#e11d48"},
        {"status": "Insufficient History", "count": _g("insufficient"), "color": "#cbd5e1"},
    ]
    for b in buckets:
        b["pct"] = round(b["count"] / total * 100, 1) if total else 0.0
    return {"buckets": buckets, "total_pairs": total}


def _pair_list(where: str, order: str, store_ids: list[str] | None, limit: int) -> list[dict]:
    mid = ", ".join("'" + s + "'" for s in store_ids) if store_ids else ""
    mid_sql = f" AND m.store_id IN ({mid})" if mid else ""
    return db.query_df(f"""
        SELECT m.store_id, st.store_name, m.product_id, p.SKU, p.product_name,
               p.category, p.brand, p.unit_cost, p.selling_price,
               m.stock, m.units_30d, m.units_7d, m.avg_daily_demand_30d,
               m.cover_days, m.lead_time_days, m.reorder_point, m.risk,
               m.overstock_excess, m.overstock_value
        FROM metrics_snapshot m
        JOIN products p ON p.product_id = m.product_id
        JOIN stores st ON st.store_id = m.store_id
        WHERE m.stock >= 0 {where}{mid_sql}
        ORDER BY {order} LIMIT {limit}""")


def _reorder_qty(ads: float, lt: int, stock: int) -> int:
    target = ads * lt + max(ads * lt * THRESHOLDS["safety_stock_ratio"],
                            THRESHOLDS["safety_stock_floor"])
    return int(round(max(target - stock, 0)))


def low_stock_list(store_ids: list[str] | None = None, limit: int = 50) -> list[dict]:
    rows = _pair_list(
        "AND (m.risk IN ('CRITICAL','HIGH') OR m.stock <= 0)",
        "CASE WHEN m.stock <= 0 THEN 0 WHEN m.risk = 'CRITICAL' THEN 1 "
        "WHEN m.risk = 'HIGH' THEN 2 ELSE 3 END, m.cover_days ASC",
        store_ids, limit)
    out = []
    for r in rows:
        stock = safe_int(r["stock"])
        ads = safe_float(r["avg_daily_demand_30d"])
        lt = safe_int(r["lead_time_days"])
        level = "STOCKED_OUT" if stock <= 0 else r["risk"]
        qty = _reorder_qty(ads, lt, stock)
        out.append({
            "store_id": r["store_id"], "store_name": r["store_name"],
            "product_id": r["product_id"], "SKU": r["SKU"],
            "product_name": r["product_name"], "category": r["category"],
            "stock": stock, "avg_daily_demand_30d": round(ads, 2),
            "cover_days": round(safe_float(r["cover_days"]), 1),
            "lead_time_days": lt, "risk": level,
            "action": "Restock immediately" if level in ("STOCKED_OUT", "CRITICAL") else "Reorder soon",
            "action_qty": qty,
        })
    return out


def overstock_list(store_ids: list[str] | None = None, limit: int = 40) -> list[dict]:
    rows = _pair_list("AND m.overstock = 1 AND m.units_30d > 0", "m.overstock_value DESC", store_ids, limit)
    out = []
    for r in rows:
        out.append({
            "store_id": r["store_id"], "store_name": r["store_name"],
            "product_id": r["product_id"], "SKU": r["SKU"],
            "product_name": r["product_name"], "category": r["category"],
            "stock": safe_int(r["stock"]), "cover_days": round(safe_float(r["cover_days"]), 1),
            "units_30d": safe_int(r["units_30d"]),
            "excess_units": safe_int(r["overstock_excess"]),
            "excess_value": _num(r["overstock_value"]),
            "unit_cost": _num(r["unit_cost"]),
            "action": "Mark down or transfer to a store with lower cover",
        })
    return out


def slow_moving_list(store_ids: list[str] | None = None, limit: int = 40) -> list[dict]:
    rows = _pair_list("AND m.slow_moving = 1 AND m.units_30d > 0", "m.cover_days DESC", store_ids, limit)
    out = []
    for r in rows:
        out.append({
            "store_id": r["store_id"], "store_name": r["store_name"],
            "product_id": r["product_id"], "SKU": r["SKU"],
            "product_name": r["product_name"], "category": r["category"],
            "stock": safe_int(r["stock"]), "cover_days": round(safe_float(r["cover_days"]), 1),
            "units_30d": safe_int(r["units_30d"]),
            "avg_daily_demand_30d": round(safe_float(r["avg_daily_demand_30d"]), 2),
            "action": "Review assortment, run de-stocking promotion or transfer",
        })
    return out


def store_detail(store_id: str) -> dict | None:
    base = db.fetchone("SELECT * FROM stores WHERE store_id=?", (store_id,))
    if not base:
        return None
    comparison = {c["store_id"]: c for c in store_comparison()}
    c = comparison.get(store_id, {})
    inv_value = db.scalar(f"""
        SELECT SUM(m.stock * p.unit_cost) FROM metrics_snapshot m
        JOIN products p ON p.product_id = m.product_id
        WHERE m.store_id = '{store_id}' AND m.stock > 0""", default=0.0)
    overstock_value = db.scalar(f"""
        SELECT SUM(m.overstock_excess * p.unit_cost) FROM metrics_snapshot m
        JOIN products p ON p.product_id = m.product_id
        WHERE m.store_id = '{store_id}' AND m.overstock = 1""", default=0.0)
    counts = db.query_df(f"""
        SELECT risk, COUNT(*) c FROM metrics_snapshot WHERE store_id = '{store_id}' GROUP BY risk""")
    risk = {r["risk"]: r["c"] for r in counts}
    top = db.query_df(f"""
        SELECT m.product_id, p.product_name, p.category, p.brand,
               m.stock, m.units_30d, m.revenue_30d, m.margin_pct,
               m.avg_daily_demand_30d, m.cover_days, m.risk
        FROM metrics_snapshot m JOIN products p ON p.product_id = m.product_id
        WHERE m.store_id = '{store_id}' ORDER BY m.revenue_30d DESC LIMIT 8""")
    d30 = (parse_date(max_date()) - timedelta(days=30)).isoformat()
    trend_rows = db.query_df(f"""
        SELECT date, units_sold, revenue FROM daily_sales_summary
        WHERE store_id = '{store_id}' AND date >= '{d30}' ORDER BY date""")
    store = dict(base)
    store["region"] = base.get("region") or ""
    return {
        "store": store,
        "as_of_date": max_date(),
        "kpis": {
            "revenue_30d": _num(c.get("revenue_30d")),
            "units_30d": safe_int(c.get("units_30d")),
            "margin_30d": _num(c.get("margin_30d")),
            "margin_pct": round(safe_float(c.get("margin_pct")), 1),
            "revenue_change_pct": _num(c.get("revenue_change_pct")),
            "inventory_value": _num(inv_value),
            "overstock_value": _num(overstock_value),
            "low_stock": safe_int(risk.get("CRITICAL")) + safe_int(risk.get("HIGH")) + safe_int(risk.get("STOCKED_OUT")),
            "overstock_items": safe_int(db.scalar(
                f"SELECT COUNT(*) FROM metrics_snapshot WHERE store_id='{store_id}' AND overstock=1")),
        },
        "top_products": [{**t, "stock": safe_int(t["stock"]),
                          "units_30d": safe_int(t["units_30d"]),
                          "revenue_30d": _num(t["revenue_30d"])} for t in top],
        "attention": attention_items(scope="all", store_id=store_id, max_items=15),
        "trend": [{"date": r["date"], "units": safe_int(r["units_sold"]),
                   "revenue": _num(r["revenue"])} for r in trend_rows],
        "summary": sales_summary(store_ids=[store_id]),
    }


def product_list(q: str = "", category: str | None = None, brand: str | None = None,
                 store_ids: list[str] | None = None, sort: str = "revenue",
                 order: str = "desc", page: int = 1, page_size: int = 20) -> dict:
    """Paginated product-level view with search, filters and sorting."""
    mid = ", ".join("'" + s + "'" for s in store_ids) if store_ids else ""
    base = f"""
        SELECT p.product_id, p.SKU, p.product_name, p.category, p.brand,
               p.selling_price, p.unit_cost, p.lead_time_days,
               COUNT(DISTINCT m.store_id) active_stores,
               SUM(m.stock) stock_total,
               SUM(m.units_30d) units_30d, SUM(m.units_7d) units_7d,
               SUM(m.revenue_30d) revenue_30d, SUM(m.margin_30d) margin_30d,
               MAX(CASE WHEN m.risk IN ('CRITICAL','HIGH','STOCKED_OUT') THEN 1 ELSE 0 END) at_risk,
               MAX(CASE WHEN m.risk = 'CRITICAL' OR m.stock <= 0 THEN 1 ELSE 0 END) critical,
               COALESCE(AVG(NULLIF(m.margin_pct, 0)), 0) margin_pct_avg
        FROM metrics_snapshot m JOIN products p ON p.product_id = m.product_id
        WHERE 1=1
    """
    if mid:
        base += f" AND m.store_id IN ({mid})"
    agg = f"""{base} GROUP BY p.product_id"""
    rows = db.query_df(agg)

    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r["product_name"].lower()
                or ql in r["product_id"].lower() or ql in str(r["SKU"]).lower()
                or ql in (r.get("brand") or "").lower() or ql in r["category"].lower()]
    if category:
        rows = [r for r in rows if r["category"] == category]
    if brand:
        rows = [r for r in rows if r["brand"] == brand]

    order_map = {
        "revenue": ("revenue_30d", True), "units": ("units_30d", True),
        "stock": ("stock_total", True), "margin": ("margin_pct_avg", True),
        "name": ("product_name", False), "risk": ("at_risk", True),
    }
    key, is_num = order_map.get(sort, order_map["revenue"])
    rows.sort(key=lambda r: (r[key] if is_num else str(r[key]).lower()), reverse=(order == "desc"))

    total = len(rows)
    start = (page - 1) * page_size
    items = []
    for r in rows[start:start + page_size]:
        items.append({
            "product_id": r["product_id"], "SKU": r["SKU"],
            "product_name": r["product_name"], "category": r["category"],
            "brand": r["brand"], "selling_price": _num(r["selling_price"]),
            "unit_cost": _num(r["unit_cost"]),
            "active_stores": safe_int(r["active_stores"]),
            "stock_total": safe_int(r["stock_total"]),
            "units_30d": safe_int(r["units_30d"]),
            "revenue_30d": _num(r["revenue_30d"]),
            "margin_pct": round(safe_float(r["margin_pct_avg"]), 1),
            "at_risk": safe_int(r["at_risk"]),
            "critical": safe_int(r["critical"]),
        })
    return {
        "total": total, "page": page, "page_size": page_size,
        "filters": {"q": q, "category": category, "brand": brand},
        "items": items,
    }


def product_trend(product_id: str, store_id: str | None = None, days: int = 30) -> list[dict]:
    """Daily units/revenue (+ closing stock) for the product chart."""
    md_ = parse_date(max_date())
    start = (md_ - timedelta(days=days)).isoformat()
    sid = f" AND store_id = '{store_id}'" if store_id else ""
    sales = db.query_df(f"""
        SELECT date, SUM(units_sold) units, SUM(revenue) revenue, SUM(gross_margin) margin
        FROM sales WHERE product_id = '{product_id}' AND date >= '{start}'{sid}
        GROUP BY date ORDER BY date""")
    inv = db.query_df(f"""
        SELECT date, MAX(closing_stock) stock
        FROM inventory WHERE product_id = '{product_id}' AND date >= '{start}'{sid}
        GROUP BY date ORDER BY date""")
    inv_map = {r["date"]: safe_int(r["stock"]) for r in inv}
    day_map = {}
    for r in sales:
        day_map[r["date"]] = {"units": safe_int(r["units"]), "revenue": _num(r["revenue"])}
    out = []
    for i in range(days):
        d = (md_ - timedelta(days=days - 1 - i)).isoformat()
        entry = {"date": d, "units": 0, "revenue": 0.0, "stock": inv_map.get(d, 0)}
        if d in day_map:
            entry.update(day_map[d])
        out.append(entry)
    return out