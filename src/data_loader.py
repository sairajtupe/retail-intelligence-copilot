"""Data loading and defensive bootstrap.

If data/retail.db is missing this module rebuilds it: from the committed CSVs
when they exist, otherwise by generating the full 20-store dataset in-process,
so the application always starts with zero manual setup.
"""
from __future__ import annotations
import sqlite3
import numpy as np
import pandas as pd
from datetime import date, timedelta
from src.config import DATA_DIR, DB_PATH, log
import src.database as db


def _csv(name: str) -> str:
    return str(DATA_DIR / name)


def _read_csv(name: str):
    return pd.read_csv(_csv(name))


def ensure_raw_tables(conn: sqlite3.Connection) -> None:
    """Create base tables from committed CSVs if they are absent."""
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS stores (store_id TEXT PRIMARY KEY, store_name TEXT, city TEXT, region TEXT, store_type TEXT);
    CREATE TABLE IF NOT EXISTS products (product_id TEXT PRIMARY KEY, SKU TEXT, product_name TEXT, category TEXT,
        brand TEXT, supplier_id TEXT, supplier TEXT, unit_cost REAL, selling_price REAL,
        lead_time_days INTEGER, reorder_point INTEGER, safety_stock INTEGER,
        shelf_life_days INTEGER, minimum_order_quantity INTEGER, target_stock_days INTEGER);
    CREATE TABLE IF NOT EXISTS suppliers (supplier_id TEXT PRIMARY KEY, supplier_name TEXT, region TEXT,
        avg_lead_time_days INTEGER, on_time_rate REAL, avg_min_order_value REAL);
    CREATE TABLE IF NOT EXISTS sales (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, store_id TEXT, product_id TEXT,
        units_sold INTEGER, unit_price REAL, discount REAL, revenue REAL, cost REAL, gross_margin REAL);
    CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, store_id TEXT, product_id TEXT,
        opening_stock INTEGER, units_received INTEGER, units_sold INTEGER, adjustments INTEGER,
        closing_stock INTEGER, reorder_point INTEGER, safety_stock INTEGER);
    CREATE TABLE IF NOT EXISTS purchase_orders (PO_id TEXT PRIMARY KEY, store_id TEXT, product_id TEXT,
        order_date TEXT, expected_delivery_date TEXT, received_date TEXT, quantity INTEGER,
        status TEXT, supplier TEXT);
    CREATE TABLE IF NOT EXISTS promotions (promo_id TEXT PRIMARY KEY, promotion_name TEXT, product_id TEXT,
        store_id TEXT, start_date TEXT, end_date TEXT, discount_pct REAL,
        demand_lift_x REAL, promotion_type TEXT);
    CREATE TABLE IF NOT EXISTS daily_sales_summary (date TEXT, store_id TEXT, units_sold INTEGER,
        revenue REAL, cost REAL, gross_margin REAL, transactions INTEGER,
        PRIMARY KEY(date, store_id));
    CREATE TABLE IF NOT EXISTS inventory_summary (date TEXT, store_id TEXT, items_tracked INTEGER,
        total_opening INTEGER, total_received INTEGER, total_sold INTEGER, total_closing INTEGER,
        PRIMARY KEY(date, store_id));
    CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(date);
    CREATE INDEX IF NOT EXISTS idx_sales_store ON sales(store_id);
    CREATE INDEX IF NOT EXISTS idx_sales_product ON sales(product_id);
    CREATE INDEX IF NOT EXISTS idx_sales_store_product ON sales(store_id, product_id);
    CREATE INDEX IF NOT EXISTS idx_sales_date_product ON sales(date, product_id);
    CREATE INDEX IF NOT EXISTS idx_inv_date ON inventory(date);
    CREATE INDEX IF NOT EXISTS idx_inv_store ON inventory(store_id);
    CREATE INDEX IF NOT EXISTS idx_inv_product ON inventory(product_id);
    CREATE INDEX IF NOT EXISTS idx_inv_store_product ON inventory(store_id, product_id);
    CREATE INDEX IF NOT EXISTS idx_inv_date_store_product ON inventory(date, store_id, product_id);
    CREATE INDEX IF NOT EXISTS idx_po_store ON purchase_orders(store_id);
    CREATE INDEX IF NOT EXISTS idx_po_product ON purchase_orders(product_id);
    CREATE INDEX IF NOT EXISTS idx_po_status ON purchase_orders(status);
    CREATE INDEX IF NOT EXISTS idx_dss_date ON daily_sales_summary(date);
    CREATE INDEX IF NOT EXISTS idx_is_date ON inventory_summary(date);
    CREATE INDEX IF NOT EXISTS idx_promo_prod ON promotions(product_id);
    CREATE INDEX IF NOT EXISTS idx_promo_store ON promotions(store_id);
    """)
    conn.commit()


def rebuild_metrics(conn: sqlite3.Connection) -> None:
    """Recompute metrics_snapshot + store_summary (mirror of scripts/generate_data.py)."""
    import math

    max_date = pd.read_sql("SELECT MAX(date) as d FROM sales", conn).iloc[0]["d"]
    max_date_dt = date.fromisoformat(max_date)
    d30 = (max_date_dt - timedelta(days=30)).isoformat()
    d7 = (max_date_dt - timedelta(days=7)).isoformat()
    d90 = (max_date_dt - timedelta(days=90)).isoformat()
    eps = 1e-9

    sales_30 = pd.read_sql(f"SELECT store_id, product_id, SUM(units_sold) units_30d, SUM(revenue) revenue_30d, SUM(cost) cost_30d, SUM(gross_margin) margin_30d FROM sales WHERE date >= '{d30}' GROUP BY store_id, product_id", conn)
    sales_7 = pd.read_sql(f"SELECT store_id, product_id, SUM(units_sold) units_7d FROM sales WHERE date >= '{d7}' GROUP BY store_id, product_id", conn)
    sales_last = pd.read_sql("SELECT store_id, product_id, MAX(date) last_sale_date FROM sales GROUP BY store_id, product_id", conn)
    sales_90 = pd.read_sql(f"SELECT store_id, product_id, COUNT(DISTINCT date) history_days FROM sales WHERE date >= '{d90}' GROUP BY store_id, product_id", conn)
    neg_recent = pd.read_sql(f"SELECT store_id, product_id, MIN(closing_stock) min_close FROM inventory WHERE date >= '{d30}' GROUP BY store_id, product_id", conn)
    latest_inv = pd.read_sql("""
        SELECT store_id, product_id, closing_stock stock FROM inventory i
        WHERE date = (SELECT MAX(date) FROM inventory WHERE store_id = i.store_id AND product_id = i.product_id)""", conn)
    products = pd.read_sql("SELECT * FROM products", conn)

    metrics = latest_inv.merge(sales_30, on=["store_id", "product_id"], how="left")
    metrics = metrics.merge(sales_7, on=["store_id", "product_id"], how="left")
    metrics = metrics.merge(sales_last, on=["store_id", "product_id"], how="left")
    metrics = metrics.merge(sales_90, on=["store_id", "product_id"], how="left")
    metrics = metrics.merge(neg_recent, on=["store_id", "product_id"], how="left")
    metrics = metrics.merge(products[["product_id", "product_name", "category", "brand",
                                      "unit_cost", "selling_price", "lead_time_days",
                                      "target_stock_days", "minimum_order_quantity"]],
                            on="product_id", how="left")
    for col in ["units_30d", "revenue_30d", "cost_30d", "margin_30d", "units_7d", "stock",
                "history_days", "min_close"]:
        metrics[col] = metrics[col].fillna(0)

    metrics["avg_daily_demand_30d"] = metrics["units_30d"] / 30.0
    metrics["avg_daily_demand_7d"] = metrics["units_7d"] / 7.0
    metrics["demand_trend_pct"] = ((metrics["avg_daily_demand_7d"] - metrics["avg_daily_demand_30d"])
                                   / (metrics["avg_daily_demand_30d"] + eps) * 100).round(1)
    metrics["margin_pct"] = (metrics["margin_30d"] / (metrics["revenue_30d"] + eps) * 100).round(1)
    metrics["cover_days"] = (metrics["stock"] / (metrics["avg_daily_demand_30d"] + eps)).round(1)
    metrics["reorder_point"] = (metrics["avg_daily_demand_30d"] * metrics["lead_time_days"]
                                + metrics["avg_daily_demand_30d"] * metrics["lead_time_days"] * 0.3).astype(int)

    def calc_risk(row):
        if row["history_days"] > 0 and row["history_days"] < 21:
            return "INSUFFICIENT_HISTORY"
        if row["stock"] <= 0:
            return "STOCKED_OUT"
        cd, lt = float(row["cover_days"]), float(row["lead_time_days"])
        if cd <= lt:
            return "CRITICAL"
        if cd <= lt + 3:
            return "HIGH"
        if cd <= lt * 1.5:
            return "MEDIUM"
        return "LOW"

    def calc_risk_score(row):
        if row["risk"] in ("INSUFFICIENT_HISTORY", "STOCKED_OUT"):
            return 1.0 if row["risk"] == "STOCKED_OUT" else None
        lt = max(float(row["lead_time_days"]), 1.0)
        cd = max(float(row["cover_days"]), 0.1)
        base = 1.0 - min(cd / (lt * 3), 1.0)
        if float(row["demand_trend_pct"]) > 20:
            base = min(base + 0.15, 1.0)
        return round(base, 2)

    metrics["risk"] = metrics.apply(calc_risk, axis=1)
    metrics["risk_score"] = metrics.apply(calc_risk_score, axis=1)
    metrics["overstock"] = ((metrics["cover_days"] > 90) & (metrics["units_30d"] > 0)
                            & (metrics["units_30d"] < metrics["stock"] * 0.1)).astype(int)
    metrics["overstock_excess"] = np.where(metrics["overstock"] == 1,
        (metrics["stock"] - metrics["target_stock_days"] * metrics["avg_daily_demand_30d"]).clip(lower=0).astype(int), 0)
    metrics["overstock_value"] = (metrics["overstock_excess"] * metrics["unit_cost"]).round(2)
    metrics["slow_moving"] = ((metrics["units_30d"] > 0) & (metrics["units_30d"] < metrics["stock"] * 0.1)
                              & (metrics["cover_days"] > 90)).astype(int)
    metrics["dead_stock"] = ((metrics["units_30d"] == 0) & (metrics["stock"] > metrics["minimum_order_quantity"])).astype(int)

    def days_since(row):
        if pd.isna(row["last_sale_date"]):
            return 999
        return (max_date_dt - date.fromisoformat(row["last_sale_date"])).days
    metrics["days_since_last_sale"] = metrics.apply(days_since, axis=1)

    def calc_anomaly(row):
        if row["history_days"] > 0 and row["history_days"] < 21:
            return "INSUFFICIENT_HISTORY"
        if row["units_7d"] == 0 and row["units_30d"] == 0:
            return "NORMAL"
        if row["days_since_last_sale"] > 30:
            return "NORMAL"
        trend = float(row["demand_trend_pct"])
        if trend > 100:
            return "SPIKE"
        if trend < -40:
            return "DROP"
        return "NORMAL"
    metrics["anomaly"] = metrics.apply(calc_anomaly, axis=1)

    def calc_transfer(row):
        if row["history_days"] > 0 and row["history_days"] < 21:
            return 0
        return 1 if (row["cover_days"] < 4 and row["stock"] < 10) or row["overstock"] == 1 else 0
    metrics["transfer_flag"] = metrics.apply(calc_transfer, axis=1)

    metrics["data_quality"] = np.select(
        [metrics["min_close"] < 0,
         (metrics["dead_stock"] == 1) & (metrics["days_since_last_sale"] > 60),
         (metrics["history_days"] > 0) & (metrics["history_days"] < 21)],
        ["NEGATIVE_STOCK", "DEAD_STOCK", "INSUFFICIENT_HISTORY"], default="")

    out_cols = ["store_id", "product_id", "product_name", "category", "brand",
                "stock", "avg_daily_demand_7d", "avg_daily_demand_30d",
                "units_7d", "units_30d", "revenue_30d", "cost_30d", "margin_30d", "margin_pct",
                "demand_trend_pct", "cover_days", "reorder_point", "lead_time_days",
                "target_stock_days", "minimum_order_quantity",
                "days_since_last_sale", "history_days", "risk", "risk_score",
                "overstock", "overstock_excess", "overstock_value",
                "slow_moving", "dead_stock", "anomaly", "transfer_flag", "data_quality"]
    conn.execute("DROP TABLE IF EXISTS metrics_snapshot")
    metrics[out_cols].to_sql("metrics_snapshot", conn, index=False)

    conn.execute("DROP TABLE IF EXISTS store_summary")
    pd.read_sql(f"SELECT store_id, SUM(revenue) revenue_total, SUM(units_sold) units_total, SUM(gross_margin) margin_total FROM sales WHERE date >= '{d30}' GROUP BY store_id", conn) \
        .to_sql("store_summary", conn, index=False)
    conn.commit()


def ensure_database() -> None:
    """Ensure the database exists with all required tables and data.

    Priority order:
      1. If data/retail.db exists and is populated, use it as-is.
      2. If the CSVs exist, rebuild the database from them.
      3. Otherwise generate the full 20-store dataset in-process via
         src.generate_dataset.build_dataset() (CSVs + retail.db + metrics).
    """
    if not DB_PATH.exists():
        log.warning("retail.db not found")
        required = ("products", "stores", "suppliers", "sales", "inventory",
                    "purchase_orders", "promotions")
        if all((DATA_DIR / f"{name}.csv").exists() for name in required):
            log.info("Rebuilding database from committed CSVs")
            _rebuild_from_csvs()
        else:
            log.info("No committed CSVs found - generating 20-store dataset in-process")
            from src.generate_dataset import build_dataset
            build_dataset()
        return

    conn = sqlite3.connect(str(DB_PATH))
    try:
        have_metrics = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='metrics_snapshot'"
        ).fetchone()[0]
        if have_metrics:
            return
        _rebuild_in_place(conn)
    finally:
        conn.close()


def _rebuild_from_csvs() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        ensure_raw_tables(conn)
        cur = conn.cursor()
        if cur.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            prod = _read_csv("products.csv")
            prod_cols = ["product_id", "SKU", "product_name", "category", "brand", "supplier_id",
                         "supplier", "unit_cost", "selling_price", "lead_time_days",
                         "reorder_point", "safety_stock", "shelf_life_days",
                         "minimum_order_quantity", "target_stock_days"]
            prod[prod_cols].to_sql("products", conn, if_exists="append", index=False)
            _read_csv("stores.csv").to_sql("stores", conn, if_exists="append", index=False)
            _read_csv("suppliers.csv").to_sql("suppliers", conn, if_exists="append", index=False)
            _read_csv("sales.csv").to_sql("sales", conn, if_exists="append", index=False)
            _read_csv("inventory.csv").to_sql("inventory", conn, if_exists="append", index=False)
            _read_csv("purchase_orders.csv").to_sql("purchase_orders", conn, if_exists="append", index=False)
            _read_csv("promotions.csv").to_sql("promotions", conn, if_exists="append", index=False)
            conn.execute("""INSERT OR REPLACE INTO daily_sales_summary
                (date, store_id, units_sold, revenue, cost, gross_margin, transactions)
                SELECT date, store_id, SUM(units_sold), SUM(revenue), SUM(cost),
                       SUM(gross_margin), COUNT(*) FROM sales GROUP BY date, store_id""")
            conn.execute("""INSERT OR REPLACE INTO inventory_summary
                (date, store_id, items_tracked, total_opening, total_received, total_sold, total_closing)
                SELECT date, store_id, COUNT(*), SUM(opening_stock), SUM(units_received),
                       SUM(units_sold), SUM(closing_stock) FROM inventory GROUP BY date, store_id""")
            conn.commit()
        rebuild_metrics(conn)
    finally:
        conn.close()
    log.info("Database rebuilt from CSVs")


def _rebuild_in_place(conn: sqlite3.Connection) -> None:
    ensure_raw_tables(conn)
    cur = conn.cursor()
    if cur.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        raise RuntimeError("Database exists but contains no products; delete data/ to regenerate")
    rebuild_metrics(conn)
    log.info("Metrics rebuilt in-place")


def product_pairs() -> list[dict]:
    """All product-store pairs with metrics, used to drive the dashboard."""
    return db.fetchall("""
        SELECT m.store_id, m.product_id, p.product_name, p.category, p.brand,
               p.unit_cost, p.selling_price, m.stock, m.avg_daily_demand_30d,
               m.avg_daily_demand_7d, m.units_30d, m.units_7d, m.cover_days,
               m.risk, m.risk_score, m.anomaly, m.overstock, m.slow_moving,
               m.dead_stock, m.reorder_point, m.lead_time_days, m.transfer_flag,
               m.data_quality, m.history_days, m.margin_pct
        FROM metrics_snapshot m
        JOIN products p ON p.product_id = m.product_id""")