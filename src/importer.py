"""Data import engine: CSV / XLSX / JSON -> validated -> partial import.

Flow (all local, no external services):
  1. read the uploaded file,
  2. auto-detect the data type from the actual columns,
  3. preview + validate every row (required columns, types, dates, numeric
     fields, duplicate ids, unknown product/store, negative quantities,
     missing values),
  4. import the VALID rows into the real SQLite database and skip the invalid
     ones (partial import) with a per-reason breakdown,
  5. rebuild analytics (daily summary + metrics snapshot) so every dashboard
     surface picks the new data up.

Validation is deterministic; nothing here ever touches the network.
"""
from __future__ import annotations
import io
import json
import math
import sqlite3
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

import src.database as db
import src.data_loader as data_loader
from src.config import log
from src.utils import parse_date, safe_float, safe_int

# ---------------------------------------------------------------------------
# Canonical schemas / column aliases
# ---------------------------------------------------------------------------

TYPE_LABEL = {
    "SALES": "Sales",
    "INVENTORY": "Inventory",
    "PRODUCTS": "Products",
    "STORES": "Stores",
}

REQUIRED_COLUMNS = {
    "SALES": ["store_id", "product_id", "units_sold"],
    "INVENTORY": ["store_id", "product_id", "closing_stock"],
    "PRODUCTS": ["product_id", "product_name"],
    "STORES": ["store_id", "store_name"],
}

ALIASES: dict[str, dict[str, list[str]]] = {
    "SALES": {
        "store_id": ["store_id", "store", "store_code", "outlet_id", "branch_id"],
        "product_id": ["product_id", "product", "product_code", "item_id"],
        "units_sold": ["units_sold", "quantity", "units", "qty", "sold", "quantity_sold"],
        "unit_price": ["unit_price", "price", "unit_rate", "price_per_unit"],
        "discount": ["discount", "discount_pct", "discount_percent"],
        "revenue": ["revenue", "sales_amount", "amount", "total", "gross_revenue", "net_sales"],
        "cost": ["cost", "cost_amount", "cogs"],
        "gross_margin": ["gross_margin", "margin", "gross_profit", "profit"],
        "date": ["date", "sale_date", "transaction_date", "invoice_date", "order_date", "sale_date_time"],
    },
    "INVENTORY": {
        "store_id": ["store_id", "store", "store_code", "outlet_id", "branch_id"],
        "product_id": ["product_id", "product", "product_code", "item_id"],
        "opening_stock": ["opening_stock", "beginning_stock", "opening"],
        "units_received": ["units_received", "received", "inbound", "receipts"],
        "units_sold": ["units_sold", "quantity_sold", "sold"],
        "adjustments": ["adjustments", "adjustment", "stock_adjustments"],
        "closing_stock": ["closing_stock", "stock_quantity", "stock_on_hand", "balance", "stock", "closing"],
        "reorder_point": ["reorder_point", "reorder_level"],
        "safety_stock": ["safety_stock", "safety_level"],
        "date": ["date", "inventory_date", "report_date", "snapshot_date"],
    },
    "PRODUCTS": {
        "product_id": ["product_id", "product", "product_code", "item_id", "sku_id"],
        "SKU": ["SKU", "sku", "sku_id", "barcode"],
        "product_name": ["product_name", "name", "title", "description", "item_name"],
        "category": ["category", "department", "product_category"],
        "brand": ["brand", "manufacturer", "vendor_name"],
        "supplier_id": ["supplier_id", "supplier", "vendor"],
        "unit_cost": ["unit_cost", "cost", "purchase_price", "base_cost"],
        "selling_price": ["selling_price", "price", "retail_price", "price_inr", "unit_price"],
        "lead_time_days": ["lead_time_days", "lead_time", "supply_lead_time"],
        "reorder_point": ["reorder_point", "reorder_level"],
        "safety_stock": ["safety_stock", "safety_level"],
        "shelf_life_days": ["shelf_life_days", "shelf_life"],
        "minimum_order_quantity": ["minimum_order_quantity", "moq", "min_order_qty"],
        "target_stock_days": ["target_stock_days", "target_days", "cover_target_days"],
    },
    "STORES": {
        "store_id": ["store_id", "store_code", "code", "outlet_id"],
        "store_name": ["store_name", "name", "store"],
        "city": ["city", "town"],
        "state": ["state", "province"],
        "region": ["region", "zone", "cluster", "area"],
        "store_type": ["store_type", "type", "format", "channel"],
    },
}

_ALL_CANON = {
    "SALES": ["date", "store_id", "product_id", "units_sold", "unit_price",
              "discount", "revenue", "cost", "gross_margin"],
    "INVENTORY": ["date", "store_id", "product_id", "opening_stock",
                  "units_received", "units_sold", "adjustments", "closing_stock",
                  "reorder_point", "safety_stock"],
    "PRODUCTS": ["product_id", "SKU", "product_name", "category", "brand",
                 "supplier_id", "unit_cost", "selling_price", "lead_time_days",
                 "reorder_point", "safety_stock", "shelf_life_days",
                 "minimum_order_quantity", "target_stock_days"],
    "STORES": ["store_id", "store_name", "city", "region", "store_type"],
}

_PRODUCT_COL_COST = "unit_cost"
_PRODUCT_COL_PRICE = "selling_price"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def read_file(filename: str, content: bytes) -> tuple[pd.DataFrame | None, str | None]:
    """Parse an uploaded CSV / XLSX / JSON file into a DataFrame."""
    name = (filename or "").lower()
    try:
        if name.endswith(".csv") or name.endswith(".txt"):
            df = pd.read_csv(io.BytesIO(content))
        elif name.endswith(".xlsx") or name.endswith(".xls"):
            engine = "openpyxl" if name.endswith(".xlsx") else None
            df = pd.read_excel(io.BytesIO(content), engine=engine)
        elif name.endswith(".json"):
            data = json.loads(content.decode("utf-8-sig"))
            if isinstance(data, dict):
                for key in ("records", "rows", "data", "items"):
                    if key in data and isinstance(data[key], list):
                        data = data[key]
                        break
                else:
                    data = [data]
            df = pd.DataFrame(data)
        else:
            return None, "Unsupported file type. Please upload CSV, XLSX or JSON."
        df = df.dropna(how="all")
        # Guarantee unique, usable column labels.
        cols, seen = [], set()
        for i, c in enumerate(df.columns):
            base = (str(c).strip() if c is not None and str(c).strip() else f"column_{i}")
            u = base
            n = 1
            while u in seen:
                u = f"{base}_{n}"
                n += 1
            seen.add(u)
            cols.append(u)
        df.columns = cols
        return df, None
    except Exception as exc:  # noqa: BLE001
        log.warning("import read failed for %s: %s", filename, exc)
        return None, f"Could not parse file: {exc}"


# ---------------------------------------------------------------------------
# Detection + column mapping
# ---------------------------------------------------------------------------

def _norm_cols(df: pd.DataFrame) -> list[str]:
    return [str(c).strip().lower() for c in df.columns]


def _hits(cols: list[str], aliases: list[str]) -> int:
    want = {str(a).strip().lower() for a in aliases}
    return sum(1 for c in cols if c in want)


def detect_type(df: pd.DataFrame) -> tuple[str | None, list[str]]:
    """Return (detected_type, missing_required_columns)."""
    cols = _norm_cols(df)
    if not cols:
        return None, ["no_columns"]

    scores: dict[str, int] = {}
    for t, aliases in ALIASES.items():
        score = 0
        for target, names in aliases.items():
            if target == "SKU":
                continue
            score += _hits(cols, names)
        scores[t] = score

    best = max(scores, key=lambda t: scores[t])
    if scores[best] <= 0:
        return None, [f"no_recognisable_columns({len(cols)})"]

    missing = [c for c in REQUIRED_COLUMNS[best] if _hits(cols, ALIASES[best][c]) == 0]
    return best, missing


def _mapping_for(df: pd.DataFrame, dtype: str) -> dict[str, str]:
    """canonical column -> matched file column name (or col index when blank)."""
    cols = _norm_cols(df)
    mapping: dict[str, str] = {}
    for target, names in ALIASES[dtype].items():
        for i, c in enumerate(cols):
            if c in {str(a).lower() for a in names}:
                mapping[target] = str(df.columns[i])
                break
    return mapping


# ---------------------------------------------------------------------------
# Row validation
# ---------------------------------------------------------------------------

class _Row:
    __slots__ = ("row", "key", "valid", "errors", "warnings")

    def __init__(self) -> None:
        self.row: dict[str, Any] = {}
        self.key: tuple | None = None
        self.valid = True
        self.errors: list[str] = []
        self.warnings: list[str] = []


def _as_date(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return (v.date() if isinstance(v, datetime) else v).isoformat()
    s = str(v).strip()
    if not s:
        return None
    if isinstance(v, (int, float)) and pd.notna(v):
        # Excel serial date fallback
        try:
            return (datetime(1899, 12, 30) + timedelta(days=float(v))).date().isoformat()
        except Exception:  # noqa: BLE001
            pass
    d = parse_date(s)
    return d.isoformat() if d else None


def _to_number(v: Any) -> float | None:
    if v is None:
        return None
    if pd.isna(v):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("₹", "").replace("$", "").strip())
    except Exception:  # noqa: BLE001
        return None


def _pfx(dp: Any) -> str:
    return str(dp).strip()


def _lookup_map(conn: sqlite3.Connection, table: str, col: str) -> set[str]:
    rows = conn.execute(f"SELECT DISTINCT {col} FROM [{table}]").fetchall()
    return {str(r[0]) for r in rows if r[0] is not None}


def _valid_builders(conn: sqlite3.Connection):
    """Preload product/store lookup maps once per import."""
    products = _lookup_map(conn, "products", "product_id")
    stores = _lookup_map(conn, "stores", "store_id")
    try:
        prod_price = {r[0]: r[1] for r in
                      conn.execute(f"SELECT product_id, {_PRODUCT_COL_PRICE} FROM products").fetchall()}
        prod_cost = {r[0]: r[1] for r in
                     conn.execute(f"SELECT product_id, {_PRODUCT_COL_COST} FROM products").fetchall()}
    except Exception:  # noqa: BLE001
        prod_price, prod_cost = {}, {}
    return products, stores, prod_price, prod_cost


def validate(df: pd.DataFrame, dtype: str, mapping: dict[str, str],
             conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Validate every row and build the import report."""
    conn = conn or db.get_conn()
    products, stores, prod_price, prod_cost = _valid_builders(conn)
    cols = _norm_cols(df)

    def val_of(row: pd.Series, target: str):
        fcol = mapping.get(target)
        if fcol is None:
            return None
        v = row[fcol]
        return v if pd.notna(v) else None

    rows: list[_Row] = []
    seen: set[tuple | None] = set()
    error_counts: dict[str, int] = {}
    warning_counts: dict[str, int] = {}
    for _, r in df.iterrows():
        out = _Row()
        key_parts: list[Any] = []

        if dtype == "SALES":
            d = _as_date(val_of(r, "date"))
            sid = _pfx(val_of(r, "store_id")) if val_of(r, "store_id") is not None else ""
            pid = _pfx(val_of(r, "product_id")) if val_of(r, "product_id") is not None else ""
            units = _to_number(val_of(r, "units_sold"))
            key_parts = [d, sid, pid, units]
            if not d:
                out.errors.append("Invalid or missing date")
            if not sid:
                out.errors.append("Missing required field: store_id")
            elif sid not in stores and dtype != "STORES":
                out.errors.append(f"Unknown store ID: {sid}")
            if not pid:
                out.errors.append("Missing required field: product_id")
            elif pid not in products:
                out.errors.append(f"Unknown product ID: {pid}")
            if units is None:
                out.errors.append("Invalid numeric field: units_sold")
            elif units < 0:
                out.errors.append("Negative quantity")
            unit_price = _to_number(val_of(r, "unit_price"))
            revenue = _to_number(val_of(r, "revenue"))
            cost = _to_number(val_of(r, "cost"))
            margin = _to_number(val_of(r, "gross_margin"))
            discount = _to_number(val_of(r, "discount")) or 0.0
            if unit_price is None and revenue is None and pid in prod_price:
                unit_price = safe_float(prod_price[pid])
            if unit_price is None and revenue is None and units:
                out.warnings.append("Missing optional field: unit_price (could not infer)")
            out.row = {
                "date": d, "store_id": sid, "product_id": pid,
                "units_sold": int(round(units)) if units is not None else 0,
                "unit_price": unit_price, "discount": discount,
                "revenue": revenue, "cost": cost, "gross_margin": margin,
            }

        elif dtype == "INVENTORY":
            d = _as_date(val_of(r, "date"))
            sid = _pfx(val_of(r, "store_id")) if val_of(r, "store_id") is not None else ""
            pid = _pfx(val_of(r, "product_id")) if val_of(r, "product_id") is not None else ""
            closing = _to_number(val_of(r, "closing_stock"))
            key_parts = [d, sid, pid, closing]
            if not d:
                out.errors.append("Invalid or missing date")
            if not sid:
                out.errors.append("Missing required field: store_id")
            elif sid not in stores:
                out.errors.append(f"Unknown store ID: {sid}")
            if not pid:
                out.errors.append("Missing required field: product_id")
            elif pid not in products:
                out.errors.append(f"Unknown product ID: {pid}")
            if closing is None:
                out.errors.append("Invalid numeric field: stock_quantity")
            elif closing < 0:
                out.warnings.append("Negative closing stock (data-quality flag)")
            out.row = {
                "date": d, "store_id": sid, "product_id": pid,
                "opening_stock": safe_int(_to_number(val_of(r, "opening_stock"))),
                "units_received": safe_int(_to_number(val_of(r, "units_received"))),
                "units_sold": safe_int(_to_number(val_of(r, "units_sold"))),
                "adjustments": safe_int(_to_number(val_of(r, "adjustments"))),
                "closing_stock": int(round(closing)) if closing is not None else 0,
                "reorder_point": safe_int(_to_number(val_of(r, "reorder_point"))),
                "safety_stock": safe_int(_to_number(val_of(r, "safety_stock"))),
            }

        elif dtype == "PRODUCTS":
            pid = _pfx(val_of(r, "product_id")) if val_of(r, "product_id") is not None else ""
            name = _pfx(val_of(r, "product_name"))
            key_parts = [pid]
            if not pid:
                out.errors.append("Missing required field: product_id")
            elif pid in products:
                out.errors.append("Duplicate product ID (already in catalog)")
            if not name:
                out.errors.append("Missing required field: product_name")
            for col, label in ((_PRODUCT_COL_COST, "unit_cost"), (_PRODUCT_COL_PRICE, "selling_price"),
                               ("lead_time_days", "lead_time_days"), ("reorder_point", "reorder_point"),
                               ("safety_stock", "safety_stock"), ("shelf_life_days", "shelf_life_days"),
                               ("minimum_order_quantity", "moq"), ("target_stock_days", "target_stock_days")):
                v = _to_number(val_of(r, col))
                if v is not None and v < 0:
                    out.errors.append(f"Negative numeric field: {label}")
            out.row = {
                "product_id": pid, "SKU": _pfx(val_of(r, "SKU")),
                "product_name": name, "category": _pfx(val_of(r, "category")),
                "brand": _pfx(val_of(r, "brand")),
                "supplier_id": _pfx(val_of(r, "supplier_id")),
                "unit_cost": _to_number(val_of(r, _PRODUCT_COL_COST)) or 0.0,
                "selling_price": _to_number(val_of(r, _PRODUCT_COL_PRICE)) or 0.0,
                "lead_time_days": safe_int(_to_number(val_of(r, "lead_time_days"))),
                "reorder_point": safe_int(_to_number(val_of(r, "reorder_point"))),
                "safety_stock": safe_int(_to_number(val_of(r, "safety_stock"))),
                "shelf_life_days": safe_int(_to_number(val_of(r, "shelf_life_days"))),
                "minimum_order_quantity": safe_int(_to_number(val_of(r, "minimum_order_quantity"))),
                "target_stock_days": safe_int(_to_number(val_of(r, "target_stock_days"))),
            }

        elif dtype == "STORES":
            sid = _pfx(val_of(r, "store_id")) if val_of(r, "store_id") is not None else ""
            name = _pfx(val_of(r, "store_name"))
            key_parts = [sid]
            if not sid:
                out.errors.append("Missing required field: store_id")
            elif sid in stores:
                out.errors.append("Duplicate store ID (already exists)")
            if not name:
                out.errors.append("Missing required field: store_name")
            state = _pfx(val_of(r, "state"))
            region = _pfx(val_of(r, "region")) or state
            out.row = {
                "store_id": sid, "store_name": name,
                "city": _pfx(val_of(r, "city")) or "",
                "region": region,
                "store_type": _pfx(val_of(r, "store_type")) or "STANDARD",
            }

        # duplicate detection (whole-row key)
        key = tuple(key_parts)
        if key in seen and key_parts and all(k not in (None, "") for k in key_parts):
            out.errors.append("Duplicate record")
        if key_parts:
            seen.add(key)

        out.valid = not out.errors
        for err in out.errors:
            error_counts[err] = error_counts.get(err, 0) + 1
        for w in out.warnings:
            warning_counts[w] = warning_counts.get(w, 0) + 1
        rows.append(out)

    valid_rows = [r for r in rows if r.valid]
    preview_rows = [dict(r.row) for r in rows[:10]]
    for row in preview_rows:
        if "date" in row and row["date"] is None:
            row["date"] = ""

    status = "ready"
    message = ""
    if not rows:
        status = "empty"
        message = "The file contains no data rows."
    elif not error_counts:
        status = "ready"
    elif len(valid_rows) == 0:
        status = "invalid"
        message = "No valid rows found. The file will be rejected."
    else:
        status = "partial"

    return {
        "filename": None,
        "type": TYPE_LABEL.get(dtype, dtype),
        "dtype": dtype,
        "records": len(rows),
        "columns": cols,
        "preview": preview_rows,
        "valid_count": len(valid_rows),
        "error_counts": error_counts,
        "warning_counts": warning_counts,
        "status": status,
        "message": message,
        "_valid_rows": [r.row for r in valid_rows],
    }


# ---------------------------------------------------------------------------
# Import (partial) into the real database
# ---------------------------------------------------------------------------

def _insert_sales(conn: sqlite3.Connection, rows: list[dict], prod_price, prod_cost) -> None:
    out = []
    for r in rows:
        units = r["units_sold"]
        price = r["unit_price"]
        if price is None and r["product_id"] in prod_price:
            price = safe_float(prod_price[r["product_id"]])
        revenue = r["revenue"]
        if revenue is None and price:
            revenue = units * price * (1 - safe_float(r["discount"]))
        cost = r["cost"]
        if cost is None and r["product_id"] in prod_cost:
            cost = units * safe_float(prod_cost[r["product_id"]])
        margin = r["gross_margin"]
        if margin is None and revenue is not None and cost is not None:
            margin = revenue - cost
        out.append((r["date"], r["store_id"], r["product_id"], units,
                    price or 0.0, safe_float(r["discount"]),
                    revenue or 0.0, cost or 0.0, margin if margin is not None else 0.0))
    conn.executemany(
        "INSERT INTO sales (date, store_id, product_id, units_sold, unit_price,"
        " discount, revenue, cost, gross_margin) VALUES (?,?,?,?,?,?,?,?,?)", out)


def _insert_inventory(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        "INSERT INTO inventory (date, store_id, product_id, opening_stock,"
        " units_received, units_sold, adjustments, closing_stock, reorder_point,"
        " safety_stock) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(r["date"], r["store_id"], r["product_id"], r["opening_stock"],
          r["units_received"], r["units_sold"], r["adjustments"], r["closing_stock"],
          r["reorder_point"], r["safety_stock"]) for r in rows])


_PRODUCT_INSERT_COLS = ["product_id", "SKU", "product_name", "category", "brand",
                        "supplier_id", "unit_cost", "selling_price", "lead_time_days",
                        "reorder_point", "safety_stock", "shelf_life_days",
                        "minimum_order_quantity", "target_stock_days"]


def _insert_products(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO products (" + ", ".join(_PRODUCT_INSERT_COLS) + ")"
        " VALUES (" + ",".join("?" * len(_PRODUCT_INSERT_COLS)) + ")",
        [tuple(r.get(c, "" if c != "product_name" else r.get("product_name"))
               for c in _PRODUCT_INSERT_COLS) for r in rows])


def _insert_stores(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO stores (store_id, store_name, city, region, store_type)"
        " VALUES (?,?,?,?,?)",
        [(r["store_id"], r["store_name"], r["city"], r["region"], r["store_type"]) for r in rows])


def _rebuild_summaries(conn: sqlite3.Connection) -> None:
    conn.execute("""DELETE FROM daily_sales_summary""")
    conn.execute("""INSERT INTO daily_sales_summary
        (date, store_id, units_sold, revenue, cost, gross_margin, transactions)
        SELECT date, store_id, SUM(units_sold), SUM(revenue), SUM(cost),
               SUM(gross_margin), COUNT(*) FROM sales GROUP BY date, store_id""")
    conn.execute("""DELETE FROM inventory_summary""")
    conn.execute("""INSERT INTO inventory_summary
        (date, store_id, items_tracked, total_opening, total_received, total_sold, total_closing)
        SELECT date, store_id, COUNT(*), SUM(opening_stock), SUM(units_received),
               SUM(units_sold), SUM(closing_stock) FROM inventory GROUP BY date, store_id""")
    conn.commit()


def perform_import(report: dict[str, Any], conn: sqlite3.Connection | None = None,
                   refresh: bool = True) -> dict[str, Any]:
    """Run the full import pipeline for an already-validated report."""
    conn = conn or db.get_conn()
    dtype = report["dtype"]
    filename = report.get("filename") or "upload"
    summary = {
        "filename": filename,
        "type": report["type"],
        "records": report["records"],
        "imported": 0,
        "skipped": report["records"] - report["valid_count"],
        "reasons": [{"reason": k, "count": v} for k, v in report["error_counts"].items()],
        "warnings": [{"reason": k, "count": v} for k, v in report["warning_counts"].items()],
        "status": "success",
    }

    if report["status"] == "invalid":
        summary["status"] = "failed"
        summary["message"] = report["message"] or "No valid rows to import."
        _record_history(conn, summary)
        return summary
    if report["status"] == "empty":
        summary["status"] = "failed"
        summary["message"] = "The file contains no data rows."
        _record_history(conn, summary)
        return summary

    products, stores, prod_price, prod_cost = _valid_builders(conn)
    valid_rows = report.get("_valid_rows", []) if report.get("_valid_rows") else []

    try:
        if dtype == "SALES":
            _insert_sales(conn, valid_rows, prod_price, prod_cost)
        elif dtype == "INVENTORY":
            _insert_inventory(conn, valid_rows)
        elif dtype == "PRODUCTS":
            _insert_products(conn, valid_rows)
        elif dtype == "STORES":
            _insert_stores(conn, valid_rows)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        log.error("import failed: %s", exc)
        summary["status"] = "failed"
        summary["message"] = f"Database error during import: {exc}"
        _record_history(conn, summary)
        return summary

    summary["imported"] = len(valid_rows)
    _rebuild_summaries(conn)
    if refresh:
        try:
            data_loader.rebuild_metrics(conn)
        except Exception as exc:  # noqa: BLE001
            log.warning("post-import refresh incomplete: %s", exc)
    _record_history(conn, summary)
    return summary


def _record_history(conn: sqlite3.Connection, summary: dict) -> None:
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS import_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT, file_type TEXT, records INTEGER, imported INTEGER,
                skipped INTEGER, errors TEXT, status TEXT, created_at TEXT
            )""")
        conn.execute(
            "INSERT INTO import_history (filename, file_type, records, imported,"
            " skipped, errors, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (summary.get("filename"), summary.get("type"),
             summary.get("records", 0), summary.get("imported", 0),
             summary.get("skipped", 0),
             json.dumps(summary.get("reasons", []), default=str),
             summary.get("status", "failed"),
             datetime.now().isoformat(timespec="seconds")))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not record import history: %s", exc)


def history_list(limit: int = 30, conn: sqlite3.Connection | None = None) -> list[dict]:
    conn = conn or db.get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS import_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT, file_type TEXT, records INTEGER, imported INTEGER,
            skipped INTEGER, errors TEXT, status TEXT, created_at TEXT
        )""")
    rows = conn.execute(
        "SELECT * FROM import_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["errors"] = json.loads(d.get("errors") or "[]")
        except Exception:  # noqa: BLE001
            d["errors"] = []
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Sample files for testing imports (deterministic, real ids only)
# ---------------------------------------------------------------------------

def build_sample(dtype: str, n: int = 12) -> list[dict]:
    """Build a clean sample file referencing real products/stores so users can
    test the import flow without producing validation errors."""
    price_map = {r[0]: r[1] for r in db.get_conn().execute("SELECT product_id, selling_price FROM products").fetchall()}
    cost_map = {r[0]: r[1] for r in db.get_conn().execute("SELECT product_id, unit_cost FROM products").fetchall()}
    products = [r["product_id"] for r in db.query_df("SELECT product_id FROM products LIMIT 8")]
    stores = [r["store_id"] for r in db.query_df("SELECT store_id FROM stores LIMIT 6")]
    md = parse_date(db.max_date("sales", "date")) or date.today()
    if dtype == "SALES":
        rows = []
        for i in range(n):
            weekday = (md - timedelta(days=i % 30))
            pid = products[i % len(products)]
            sid = stores[i % len(stores)]
            qty = 3 + (i * 7) % 18
            price = safe_float(price_map.get(pid, 100))
            rows.append({
                "sale_id": f"DEMO-S{i+1:03d}", "date": weekday.isoformat(),
                "store_id": sid, "product_id": pid, "quantity": qty,
                "unit_price": price, "revenue": round(qty * price, 2),
            })
        return rows
    if dtype == "INVENTORY":
        rows = []
        for i in range(n):
            pid = products[i % len(products)]
            sid = stores[i % len(stores)]
            stock = 40 + (i * 13) % 200
            rows.append({
                "date": (md - timedelta(days=i % 7)).isoformat(),
                "store_id": sid, "product_id": pid,
                "stock_quantity": stock, "reserved_quantity": 2 + i % 5,
            })
        return rows
    if dtype == "PRODUCTS":
        return [{
            "product_id": f"P-IMP-{i+1:03d}", "product_name": f"Demo Import Product {i+1}",
            "category": "Imported", "brand": "DemoBrand",
            "unit_cost": round(40 + i * 5.5, 2), "selling_price": round(120 + i * 12, 2),
            "supplier_id": "SUP-DEMO", "lead_time_days": 7,
        } for i in range(n)]
    # STORES
    return [{
        "store_id": f"S-IMP-{i+1:02d}", "store_name": f"Demo Store {i+1}",
        "city": "Pune", "state": "Maharashtra", "region": "West",
        "store_type": "STANDARD",
    } for i in range(min(n, 5))]


IMPORTABLE_EXTENSIONS = (".csv", ".xlsx", ".json")
IMPORTABLE_TYPES = frozenset(("SALES", "INVENTORY", "PRODUCTS", "STORES"))