"""Deterministic demand forecasting.

Simple, transparent methods. The advisor layer consumes these raw forecasts;
the LLM never produces them.
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Any

import src.database as db
from src.utils import safe_float, safe_int, parse_date


def forecast_daily_units(product_id: str, store_id: str | None = None,
                         horizon_days: int = 14) -> dict[str, Any]:
    """Naive forecast based on trailing demand with linear trend guard.

    Uses a 7-day weighted moving average and a bounded linear drift from the
    trailing 14 days. Falls back to the 30-day mean when history is thin.
    """
    days = 30
    start = (parse_date(db.max_date()) - timedelta(days=days)).isoformat()
    params: list = []
    where = "date >= ?"
    params.append(start)
    if product_id and store_id:
        where += " AND product_id = ? AND store_id = ?"
        params.extend([product_id, store_id])
    elif product_id:
        where += " AND product_id = ?"
        params.append(product_id)
    elif store_id:
        where += " AND store_id = ?"
        params.append(store_id)

    rows = db.query_df(f"""
        SELECT date, SUM(units_sold) units
        FROM sales WHERE {where}
        GROUP BY date ORDER BY date""", tuple(params))
    if not rows:
        return {"horizon_days": horizon_days, "forecast_units": [],
                "method": "insufficient_history", "note": "No history available"}

    daily: dict[str, float] = {}
    for r in rows:
        d = parse_date(r["date"])
        if d is None:
            continue
        daily[d] = safe_float(r["units"]) + daily.get(d, 0.0)

    dates = sorted(daily)
    if len(dates) < 7:
        return {"horizon_days": horizon_days, "forecast_units": [],
                "method": "insufficient_history", "note": "Fewer than 7 days of history"}

    series = [daily[d] for d in dates]
    recent7 = sum(series[-7:]) / 7.0
    prior7 = sum(series[-14:-7]) / 7.0 if len(series) >= 14 else recent7
    horizon = prior7 if prior7 > 0 else recent7
    drift = 0.0
    if prior7 > 0:
        drift = max(-0.15, min(0.15, (recent7 - prior7) / prior7 / 7.0))

    last_day = dates[-1]
    out = []
    for i in range(1, horizon_days + 1):
        d = last_day + timedelta(days=i)
        est = horizon * (1 + drift * i)
        out.append({"date": d.isoformat(), "forecast_units": round(max(est, 0), 1)})

    return {"horizon_days": horizon_days, "forecast_units": out,
            "method": "weighted_moving_average",
            "base_rate": round(recent7, 2),
            "trend_adj": round(drift * 100, 1),
            "recent_demand_30d": round(sum(series) / len(series), 2)}


def forecast_dashboard(horizon_days: int = 14) -> dict[str, Any]:
    """Forecast for the whole network (used by the Forecast widget)."""
    md = parse_date(db.max_date())
    start = (md - timedelta(days=30)).isoformat()
    rows = db.query_df(f"""
        SELECT date, SUM(units_sold) units, SUM(revenue) revenue
        FROM daily_sales_summary WHERE date >= '{start}'
        GROUP BY date ORDER BY date""")
    daily: dict[str, tuple] = {}
    for r in rows:
        daily[r["date"]] = (safe_float(r["units"]), safe_float(r["revenue"]))
    dates = sorted(daily)
    if not dates or len(dates) < 7:
        return {"forecast_units": [], "method": "insufficient_history"}

    units = [daily[d][0] for d in dates]
    revenue = [daily[d][1] for d in dates]
    ru = sum(units[-7:]) / 7.0
    rv = sum(revenue[-7:]) / 7.0
    output = []
    for i in range(1, horizon_days + 1):
        d = (parse_date(dates[-1]) + timedelta(days=i)).isoformat()
        output.append({"date": d, "units": round(max(ru, 0), 1),
                       "revenue": round(max(rv, 0), 2)})
    return {"forecast_units": output, "method": "weighted_moving_average",
            "base_units": round(ru, 1), "base_revenue": round(rv, 2)}