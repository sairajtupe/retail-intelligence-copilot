#!/usr/bin/env python3
"""Synthetic Indian retail dataset generator (tiny: 3 stores, 11 products).

Deterministic (seed fixed). Produces CSVs + retail.db with metrics_snapshot so
the app starts with zero manual setup. The dataset is intentionally small
(~660 sales rows over 2 calendar months) while still exercising every demo
scenario end-to-end:

  * P-DEMO-001 @ S01  -> CRITICAL stock-out risk, engine recommends "order 91 units"
  * P-DEMO-002 @ S01  -> HIGH stock-out risk (healthy fast mover)
  * P-DEMO-003 @ S02  -> natural demand SPIKE (Portable Charger 20K)
  * P-DEMO-004 @ S03  -> LOW risk healthy pair
  * P-DEMO-005 @ S01  -> demand DROP (winter scarf, seasonal)
  * P-DEMO-006 @ S02  -> OVERSTOCK / slow-moving (LED Desk Lamp)
  * P-DEMO-007 @ S03  -> STOCKED_OUT (Silicone Baking Mat)
  * P-DEMO-008/009/010 -> LOW risk healthy pairs
  * P-DEMO-011 @ S02  -> promotion-driven SPIKE ("Member Exclusive")
"""
from __future__ import annotations
import os, sys, sqlite3, random
from pathlib import Path
from datetime import date, timedelta

import numpy as np
import pandas as pd

SEED = 42
np.random.seed(SEED)
random.seed(SEED)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

START_DATE = date(2026, 7, 1)
NUM_DAYS = 62
ALL_DATES = [START_DATE + timedelta(days=d) for d in range(NUM_DAYS)]

# (store_id, store_name, city, region, store_type)
STORES = [
    ("S01", "Downtown Pune Market", "Pune", "Maharashtra", "Supermarket"),
    ("S02", "Phoenix Mall Store", "Pune", "Maharashtra", "Hypermarket"),
    ("S03", "Hometown Grocers", "Nashik", "Maharashtra", "Convenience"),
]

# (product_id, SKU, product_name, category, brand, unit_cost, selling_price,
#  supplier, lead_time_days, shelf_life_days, minimum_order_quantity,
#  target_stock_days)
PRODUCTS = [
    ("P-DEMO-001", "BEV-COLA-001", "Cola Max 300ml", "Beverages", "MaxCola",
     12.00, 40.00, "BeverageDistro Co", 8, 365, 50, 30),
    ("P-DEMO-002", "SNK-EBAR-001", "EnergyBar Protein Pack", "Snacks", "NutriBar",
     25.00, 75.00, "FreshFoods Supply", 5, 180, 30, 21),
    ("P-DEMO-003", "ELEC-CHRG-001", "Portable Charger 20K", "Electronics Accessories", "ChargeMax",
     260.00, 649.00, "PacificSupply Inc", 12, 365, 15, 30),
    ("P-DEMO-004", "FIT-SHAKE-001", "Protein Shake Vanilla", "Fitness", "FitLife",
     140.00, 350.00, "FreshFoods Supply", 7, 120, 30, 30),
    ("P-DEMO-005", "SEA-SCARF-001", "Winter Scarf Premium", "Seasonal", "WinterWarm",
     120.00, 299.00, "PremiumGoods Ltd", 10, 365, 25, 30),
    ("P-DEMO-006", "HOU-LAMP-001", "LED Desk Lamp Pro", "Household", "BrightHome",
     420.00, 999.00, "PremiumGoods Ltd", 14, 365, 10, 30),
    ("P-DEMO-007", "KIT-MAT-001", "Silicone Baking Mat", "Kitchen", "ChefMate",
     110.00, 249.00, "AllGoods Wholesale", 8, 365, 20, 30),
    ("P-DEMO-008", "ELEC-HUB-001", "USB-C Hub Adapter", "Electronics Accessories", "CablePro",
     220.00, 549.00, "QuickServe Logistics", 10, 365, 20, 30),
    ("P-DEMO-009", "BEA-CRM-001", "Organic Face Cream 50ml", "Beauty", "GlowNaturals",
     96.00, 399.00, "PrimeGoods Corp", 7, 270, 20, 30),
    ("P-DEMO-010", "ELEC-EBUD-001", "Bluetooth Earbuds Mini", "Electronics Accessories", "TechGear",
     240.00, 599.00, "EasternImports", 14, 365, 15, 30),
    ("P-DEMO-011", "BEV-TEA-001", "Green Tea Matcha 500ml", "Beverages", "ZestTea",
     30.00, 99.00, "BeverageDistro Co", 7, 180, 30, 30),
]

# Pair schedule: (product_id, store_id, initial_stock, daily_pattern).
PAIRS = [
    # Jul: 12/day. Aug 1-29: 10/day. Aug 30-31: 5/day -> final stock 13.
    ("P-DEMO-001", "S01", 685,
     [12 if d < 31 else (10 if d < 60 else 5) for d in range(NUM_DAYS)]),
    ("P-DEMO-002", "S01", 273, [4] * NUM_DAYS),
    # Steady 1/day then 6/day for the last 7 days -> natural SPIKE (+500%).
    ("P-DEMO-003", "S02", 137, [1] * 55 + [6] * 7),
    ("P-DEMO-004", "S03", 246, [3] * NUM_DAYS),
    # Strong 5/day until Aug 24 then 1/day -> demand DROP.
    ("P-DEMO-005", "S01", 342, [5] * 55 + [1] * 7),
    # Sparse slow-mover -> OVERSTOCK. Sells Jul 1-15 and Aug 5-31.
    ("P-DEMO-006", "S02", 342,
     [1 if (d < 15 or d >= 35) else 0 for d in range(NUM_DAYS)]),
    # Sells for 61 days then stops -> STOCKED_OUT on the final day.
    ("P-DEMO-007", "S03", 305, [5] * 61 + [0]),
    ("P-DEMO-008", "S02", 80, [1] * NUM_DAYS),
    ("P-DEMO-009", "S01", 164, [2] * NUM_DAYS),
    ("P-DEMO-010", "S03", 184, [2] * NUM_DAYS),
    # Steady 2/day then 8/day for the last 7 days, matched by "Member Exclusive".
    ("P-DEMO-011", "S02", 236, [2] * 55 + [8] * 7),
]

PROMOTIONS = [
    # Promotion-driven spike window (last 2 days) behind P-DEMO-011 @ S02.
    ("PRM-0001", "Member Exclusive", "P-DEMO-011", "S02", 60, 61, 0.15, 3.0, "DISCOUNT"),
    ("PRM-0002", "Weekend Blast", "P-DEMO-003", "S02", 9, 11, 0.15, 2.0, "DISCOUNT"),
    ("PRM-0003", "Store Anniversary", "P-DEMO-001", "S01", 19, 24, 0.10, 2.2, "SEASONAL"),
]

# (PO_id, store_id, product_id, order_day, expected_delivery_day, received_day, qty, status)
PURCHASE_ORDERS = [
    ("PO-00001", "S01", "P-DEMO-001", 58, 65, "", 300, "pending"),
    ("PO-00002", "S01", "P-DEMO-002", 50, 55, 55, 120, "received"),
    ("PO-00003", "S03", "P-DEMO-007", 57, 65, "", 350, "pending"),
    ("PO-00004", "S02", "P-DEMO-003", 55, 67, "", 80, "pending"),
    ("PO-00005", "S01", "P-DEMO-005", 40, 50, 50, 100, "received"),
]


def generate_stores_df() -> pd.DataFrame:
    return pd.DataFrame(STORES, columns=["store_id", "store_name", "city", "region", "store_type"])


def generate_products_df() -> tuple[pd.DataFrame, dict]:
    suppliers = {}
    rows = []
    for pid, sku, name, cat, brand, cost, price, supplier, lead, shelf, moq, tdays in PRODUCTS:
        suppliers.setdefault(supplier, 0)
        rows.append({
            "product_id": pid, "SKU": sku, "product_name": name, "category": cat,
            "brand": brand, "unit_cost": cost, "selling_price": price, "supplier": supplier,
            "lead_time_days": lead, "shelf_life_days": shelf,
            "minimum_order_quantity": moq, "target_stock_days": tdays,
        })
    return pd.DataFrame(rows), {"demo_ids": {p[0] for p in PRODUCTS}}


def generate_suppliers(products_df: pd.DataFrame) -> pd.DataFrame:
    names = sorted(products_df["supplier"].unique())
    rows = []
    for i, n in enumerate(names):
        leads = products_df.loc[products_df["supplier"] == n, "lead_time_days"]
        rows.append({
            "supplier_id": f"S{i + 1:03d}",
            "supplier_name": n,
            "region": "Maharashtra",
            "avg_lead_time_days": int(leads.median()) if len(leads) else 7,
            "on_time_rate": 0.95,
            "avg_min_order_value": 1000,
        })
    return pd.DataFrame(rows, columns=["supplier_id", "supplier_name", "region",
                                       "avg_lead_time_days", "on_time_rate", "avg_min_order_value"])


def enrich_products(products_df: pd.DataFrame, suppliers_df: pd.DataFrame) -> pd.DataFrame:
    sup_map = dict(zip(suppliers_df["supplier_name"], suppliers_df["supplier_id"]))
    products_df["supplier_id"] = products_df["supplier"].map(sup_map)
    products_df["reorder_point"] = (products_df["lead_time_days"] * 1.3).round().astype(int)
    products_df["safety_stock"] = (products_df["lead_time_days"] * 0.3).round().astype(int)
    return products_df


def generate_promotions() -> pd.DataFrame:
    rows = []
    for pid, name, product, store, sday, eday, disc, lift, ptype in PROMOTIONS:
        rows.append({
            "promo_id": pid, "promotion_name": name, "product_id": product, "store_id": store,
            "start_day": sday, "end_day": eday,
            "start_date": ALL_DATES[sday].isoformat(), "end_date": ALL_DATES[eday].isoformat(),
            "discount_pct": disc, "demand_lift_x": lift, "promotion_type": ptype,
        })
    return pd.DataFrame(rows)


def generate_sales_and_inventory(products_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    prod_map = products_df.set_index("product_id")
    sales_rows, inv_rows, po_rows = [], [], []

    for pid, sid, initial, daily in PAIRS:
        prod = prod_map.loc[pid]
        price, cost, lead = prod["selling_price"], prod["unit_cost"], prod["lead_time_days"]
        total_sold = sum(daily)
        reorder_pt = int(lead * total_sold / NUM_DAYS)
        safety = max(5, int(lead * total_sold / NUM_DAYS * 0.3))

        stock = initial
        for d, u in enumerate(daily):
            dt = ALL_DATES[d]
            opening = stock
            if u > 0:
                disc = round(random.uniform(0.05, 0.20), 2) if random.random() < 0.05 else 0.0
                rev = round(u * price * (1 - disc), 2)
                cst = round(u * cost, 2)
                sales_rows.append({
                    "date": dt.isoformat(), "store_id": sid, "product_id": pid,
                    "units_sold": u, "unit_price": price, "discount": disc,
                    "revenue": rev, "cost": cst, "gross_margin": round(rev - cst, 2),
                })
            closing = opening - u
            inv_rows.append({
                "date": dt.isoformat(), "store_id": sid, "product_id": pid,
                "opening_stock": max(opening, 0), "units_received": 0,
                "units_sold": u, "adjustments": 0,
                "closing_stock": max(closing, 0), "reorder_point": reorder_pt,
                "safety_stock": safety,
            })
            stock = max(closing, 0)

        for po_id, psid, ppid, order_day, exp_day, recv_day, qty, status in PURCHASE_ORDERS:
            if (psid, ppid) == (sid, pid):
                po_rows.append({
                    "PO_id": po_id, "store_id": sid, "product_id": pid,
                    "order_date": (START_DATE + timedelta(days=order_day)).isoformat(),
                    "expected_delivery_date": (START_DATE + timedelta(days=exp_day)).isoformat(),
                    "received_date": (START_DATE + timedelta(days=recv_day)).isoformat() if recv_day else "",
                    "quantity": qty, "status": status, "supplier": prod["supplier"],
                })

    return (pd.DataFrame(sales_rows), pd.DataFrame(inv_rows), pd.DataFrame(po_rows))


def write_csvs(stores_df, products_df, suppliers_df, promotions_df, sales_df, inv_df, po_df):
    out_cols_prod = ["product_id", "SKU", "product_name", "category", "brand", "supplier_id",
                     "supplier", "unit_cost", "selling_price", "lead_time_days",
                     "reorder_point", "safety_stock", "shelf_life_days",
                     "minimum_order_quantity", "target_stock_days"]
    products_df[out_cols_prod].to_csv(DATA_DIR / "products.csv", index=False)
    stores_df.to_csv(DATA_DIR / "stores.csv", index=False)
    suppliers_df.to_csv(DATA_DIR / "suppliers.csv", index=False)
    promotions_df.to_csv(DATA_DIR / "promotions.csv", index=False)
    sales_df.to_csv(DATA_DIR / "sales.csv", index=False)
    inv_df.to_csv(DATA_DIR / "inventory.csv", index=False)
    po_df.to_csv(DATA_DIR / "purchase_orders.csv", index=False)


def build_database(stores_df, products_df, suppliers_df, promotions_df,
                   sales_df, inv_df, po_df) -> Path:
    db_path = DATA_DIR / "retail.db"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE stores (store_id TEXT PRIMARY KEY, store_name TEXT, city TEXT, region TEXT, store_type TEXT);
    CREATE TABLE products (product_id TEXT PRIMARY KEY, SKU TEXT, product_name TEXT, category TEXT,
        brand TEXT, supplier_id TEXT, supplier TEXT, unit_cost REAL, selling_price REAL,
        lead_time_days INTEGER, reorder_point INTEGER, safety_stock INTEGER,
        shelf_life_days INTEGER, minimum_order_quantity INTEGER, target_stock_days INTEGER);
    CREATE TABLE suppliers (supplier_id TEXT PRIMARY KEY, supplier_name TEXT, region TEXT,
        avg_lead_time_days INTEGER, on_time_rate REAL, avg_min_order_value REAL);
    CREATE TABLE sales (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, store_id TEXT, product_id TEXT,
        units_sold INTEGER, unit_price REAL, discount REAL, revenue REAL, cost REAL, gross_margin REAL);
    CREATE TABLE inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, store_id TEXT, product_id TEXT,
        opening_stock INTEGER, units_received INTEGER, units_sold INTEGER, adjustments INTEGER,
        closing_stock INTEGER, reorder_point INTEGER, safety_stock INTEGER);
    CREATE TABLE purchase_orders (PO_id TEXT PRIMARY KEY, store_id TEXT, product_id TEXT,
        order_date TEXT, expected_delivery_date TEXT, received_date TEXT, quantity INTEGER,
        status TEXT, supplier TEXT);
    CREATE TABLE promotions (promo_id TEXT PRIMARY KEY, promotion_name TEXT, product_id TEXT,
        store_id TEXT, start_date TEXT, end_date TEXT, discount_pct REAL,
        demand_lift_x REAL, promotion_type TEXT);
    CREATE TABLE daily_sales_summary (date TEXT, store_id TEXT, units_sold INTEGER,
        revenue REAL, cost REAL, gross_margin REAL, transactions INTEGER,
        PRIMARY KEY(date, store_id));
    CREATE TABLE inventory_summary (date TEXT, store_id TEXT, items_tracked INTEGER,
        total_opening INTEGER, total_received INTEGER, total_sold INTEGER, total_closing INTEGER,
        PRIMARY KEY(date, store_id));

    CREATE INDEX idx_sales_date ON sales(date);
    CREATE INDEX idx_sales_store ON sales(store_id);
    CREATE INDEX idx_sales_product ON sales(product_id);
    CREATE INDEX idx_sales_store_product ON sales(store_id, product_id);
    CREATE INDEX idx_sales_date_product ON sales(date, product_id);
    CREATE INDEX idx_inv_date ON inventory(date);
    CREATE INDEX idx_inv_store ON inventory(store_id);
    CREATE INDEX idx_inv_product ON inventory(product_id);
    CREATE INDEX idx_inv_store_product ON inventory(store_id, product_id);
    CREATE INDEX idx_inv_date_store_product ON inventory(date, store_id, product_id);
    CREATE INDEX idx_po_store ON purchase_orders(store_id);
    CREATE INDEX idx_po_product ON purchase_orders(product_id);
    CREATE INDEX idx_po_status ON purchase_orders(status);
    CREATE INDEX idx_dss_date ON daily_sales_summary(date);
    CREATE INDEX idx_is_date ON inventory_summary(date);
    CREATE INDEX idx_promo_prod ON promotions(product_id);
    CREATE INDEX idx_promo_store ON promotions(store_id);
    """)

    out_cols_prod = ["product_id", "SKU", "product_name", "category", "brand", "supplier_id",
                     "supplier", "unit_cost", "selling_price", "lead_time_days",
                     "reorder_point", "safety_stock", "shelf_life_days",
                     "minimum_order_quantity", "target_stock_days"]
    stores_df.to_sql("stores", conn, if_exists="append", index=False)
    products_df[out_cols_prod].to_sql("products", conn, if_exists="append", index=False)
    suppliers_df.to_sql("suppliers", conn, if_exists="append", index=False)
    sales_df.to_sql("sales", conn, if_exists="append", index=False)
    inv_df.to_sql("inventory", conn, if_exists="append", index=False)
    po_df.to_sql("purchase_orders", conn, if_exists="append", index=False)
    prom_cols = ["promo_id", "promotion_name", "product_id", "store_id",
                 "start_date", "end_date", "discount_pct", "demand_lift_x", "promotion_type"]
    promotions_df[prom_cols].to_sql("promotions", conn, if_exists="append", index=False)

    conn.execute("""
        INSERT OR REPLACE INTO daily_sales_summary
        (date, store_id, units_sold, revenue, cost, gross_margin, transactions)
        SELECT date, store_id, SUM(units_sold), SUM(revenue), SUM(cost),
               SUM(gross_margin), COUNT(*) FROM sales GROUP BY date, store_id""")
    conn.execute("""
        INSERT OR REPLACE INTO inventory_summary
        (date, store_id, items_tracked, total_opening, total_received, total_sold, total_closing)
        SELECT date, store_id, COUNT(*), SUM(opening_stock), SUM(units_received),
               SUM(units_sold), SUM(closing_stock) FROM inventory GROUP BY date, store_id""")

    conn.commit()
    conn.close()
    return db_path


def compute_metrics(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    max_date = pd.read_sql("SELECT MAX(date) as d FROM sales", conn).iloc[0]["d"]
    max_date_dt = date.fromisoformat(max_date)

    d30 = (max_date_dt - timedelta(days=30)).isoformat()
    d7 = (max_date_dt - timedelta(days=7)).isoformat()
    d90 = (max_date_dt - timedelta(days=90)).isoformat()
    eps = 1e-9

    sales_30 = pd.read_sql(f"""
        SELECT store_id, product_id, SUM(units_sold) as units_30d, SUM(revenue) as revenue_30d,
               SUM(cost) as cost_30d, SUM(gross_margin) as margin_30d
        FROM sales WHERE date >= '{d30}' GROUP BY store_id, product_id""", conn)
    sales_7 = pd.read_sql(f"""
        SELECT store_id, product_id, SUM(units_sold) as units_7d
        FROM sales WHERE date >= '{d7}' GROUP BY store_id, product_id""", conn)
    sales_last = pd.read_sql(f"""
        SELECT store_id, product_id, MAX(date) as last_sale_date
        FROM sales GROUP BY store_id, product_id""", conn)
    sales_90 = pd.read_sql(f"""
        SELECT store_id, product_id, COUNT(DISTINCT date) as history_days
        FROM sales WHERE date >= '{d90}' GROUP BY store_id, product_id""", conn)
    neg_recent = pd.read_sql(f"""
        SELECT store_id, product_id, MIN(closing_stock) as min_close
        FROM inventory WHERE date >= '{d30}' GROUP BY store_id, product_id""", conn)
    latest_inv = pd.read_sql(f"""
        SELECT store_id, product_id, closing_stock as stock
        FROM inventory i
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
        cd, lt = row["cover_days"], row["lead_time_days"]
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
        lt = max(row["lead_time_days"], 1)
        cd = max(row["cover_days"], 0.1)
        base = 1.0 - min(cd / (lt * 3), 1.0)
        if row["demand_trend_pct"] > 20:
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
        trend = row["demand_trend_pct"]
        if trend > 100:
            return "SPIKE"
        if trend < -40:
            return "DROP"
        return "NORMAL"
    metrics["anomaly"] = metrics.apply(calc_anomaly, axis=1)

    metrics["transfer_flag"] = 0
    for pid in metrics["product_id"].unique():
        sub = metrics[metrics["product_id"] == pid]
        excess = sub[sub["cover_days"] > sub["target_stock_days"] * 2]
        deficit = sub[sub["cover_days"] < sub["lead_time_days"]]
        if len(excess) > 0 and len(deficit) > 0:
            metrics.loc[metrics["product_id"] == pid, "transfer_flag"] = 1

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
    pd.read_sql(f"""
        SELECT store_id, SUM(revenue) as revenue_total, SUM(units_sold) as units_total,
               SUM(gross_margin) as margin_total
        FROM sales WHERE date >= '{d30}' GROUP BY store_id""", conn) \
        .to_sql("store_summary", conn, index=False)

    conn.commit()
    conn.close()


def build_dataset() -> dict:
    print("Step 1: Generating stores (3 stores)...")
    stores_df = generate_stores_df()

    print("Step 2: Generating products (11 products)...")
    products_df, demo_info = generate_products_df()

    print("Step 2b: Generating suppliers and promotions...")
    suppliers_df = generate_suppliers(products_df)
    products_df = enrich_products(products_df, suppliers_df)
    promotions_df = generate_promotions()

    print("Step 3: Generating sales + inventory (deterministic pairs)...")
    sales_df, inv_df, po_df = generate_sales_and_inventory(products_df)

    print("Step 5: Writing CSVs...")
    write_csvs(stores_df, products_df, suppliers_df, promotions_df, sales_df, inv_df, po_df)

    print("Step 6: Building SQLite database...")
    db_path = build_database(stores_df, products_df, suppliers_df, promotions_df,
                             sales_df, inv_df, po_df)

    print("Step 7: Computing metrics snapshot...")
    compute_metrics(db_path)

    stats = {
        "stores": len(stores_df),
        "products": len(products_df),
        "suppliers": len(suppliers_df),
        "promotions": len(promotions_df),
        "sales": len(sales_df),
        "inventory": len(inv_df),
        "purchase_orders": len(po_df),
    }
    print("DONE!", stats)
    return stats


if __name__ == "__main__":
    build_dataset()