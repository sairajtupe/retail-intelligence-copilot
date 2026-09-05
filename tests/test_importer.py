"""Tests for the CSV / XLSX / JSON import engine (detection, validation,
partial import into a throwaway in-memory DB, history)."""
import io
import sqlite3
import unittest

import pandas as pd

from src import importer

SALES_COLS = ["date", "store_id", "product_id", "quantity"]
PRODUCTS_COLS = ["product_id", "product_name", "category", "brand",
                 "unit_cost", "selling_price", "supplier_id"]
STORES_COLS = ["store_id", "store_name", "city", "region", "store_type"]

SCHEMA = """
CREATE TABLE stores (store_id TEXT PRIMARY KEY, store_name TEXT, city TEXT, region TEXT, store_type TEXT);
CREATE TABLE products (product_id TEXT PRIMARY KEY, SKU TEXT, product_name TEXT, category TEXT,
    brand TEXT, supplier_id TEXT, supplier TEXT, unit_cost REAL, selling_price REAL,
    lead_time_days INTEGER, reorder_point INTEGER, safety_stock INTEGER,
    shelf_life_days INTEGER, minimum_order_quantity INTEGER, target_stock_days INTEGER);
CREATE TABLE sales (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, store_id TEXT, product_id TEXT,
    units_sold INTEGER, unit_price REAL, discount REAL, revenue REAL, cost REAL, gross_margin REAL);
CREATE TABLE inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, store_id TEXT, product_id TEXT,
    opening_stock INTEGER, units_received INTEGER, units_sold INTEGER, adjustments INTEGER,
    closing_stock INTEGER, reorder_point INTEGER, safety_stock INTEGER);
CREATE TABLE daily_sales_summary (date TEXT, store_id TEXT, units_sold INTEGER,
    revenue REAL, cost REAL, gross_margin REAL, transactions INTEGER);
CREATE TABLE inventory_summary (date TEXT, store_id TEXT, items_tracked INTEGER,
    total_opening INTEGER, total_received INTEGER, total_sold INTEGER, total_closing INTEGER);
"""


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO stores VALUES ('S01','Store One','Pune','West','STANDARD')")
    conn.execute("INSERT INTO products (product_id, product_name, category, brand, unit_cost, selling_price)"
                 " VALUES ('P1','Demo Product','Fitness','DemoBrand',50,120)")
    conn.commit()
    return conn


def _csv(text: str) -> bytes:
    return io.StringIO(text.strip()).getvalue().encode()


class DetectionTest(unittest.TestCase):
    def test_detect_sales(self):
        df, _ = importer.read_file("x.csv", _csv("\n".join([
            ",".join(SALES_COLS), "2026-01-01,S01,P1,5"])))
        dtype, missing = importer.detect_type(df)
        self.assertEqual(dtype, "SALES")
        self.assertEqual(missing, [])

    def test_detect_stores_and_products(self):
        df, _ = importer.read_file("x.csv", _csv("\n".join([
            ",".join(STORES_COLS), "S02,Two,Mumbai,West,STANDARD"])))
        self.assertEqual(importer.detect_type(df)[0], "STORES")
        df2, _ = importer.read_file("x.csv", _csv("\n".join([
            ",".join(PRODUCTS_COLS), "P20,Foo,Fitness,Brand,10,20,SUP1"])))
        self.assertEqual(importer.detect_type(df2)[0], "PRODUCTS")

    def test_detect_unknown_columns(self):
        df = pd.DataFrame({"a": [1], "b": [2]})
        self.assertIsNone(importer.detect_type(df)[0])

    def test_read_json_and_invalid(self):
        data = [{"date": "2026-01-01", "store_id": "S01", "product_id": "P1", "quantity": 3}]
        df, err = importer.read_file("x.json", io.StringIO(
            '{"records": ' + __import__("json").dumps(data) + "}").read().encode())
        self.assertIsNone(err)
        self.assertEqual(len(df), 1)
        df2, err2 = importer.read_file("x.bin", b"\x00\x01")
        self.assertIsNone(df2)
        self.assertIsNotNone(err2)


class ValidateTest(unittest.TestCase):
    def test_sales_ready(self):
        conn = _make_conn()
        df, _ = importer.read_file("x.csv", _csv("\n".join([
            ",".join(SALES_COLS), "2026-01-01,S01,P1,5", "2026-01-02,S01,P1,7"])))
        dtype, _ = importer.detect_type(df)
        report = importer.validate(df, dtype, importer._mapping_for(df, dtype), conn)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["valid_count"], 2)
        self.assertEqual(report["records"], 2)
        self.assertEqual(len(report["preview"]), 2)

    def test_sales_errors_for_unknown_ids(self):
        conn = _make_conn()
        df, _ = importer.read_file("x.csv", _csv("\n".join([
            ",".join(SALES_COLS), "2026-01-01,XX,P1,5", "2026-01-02,S01,P99,7"])))
        dtype, _ = importer.detect_type(df)
        report = importer.validate(df, dtype, importer._mapping_for(df, dtype), conn)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["valid_count"], 0)
        self.assertIn("Unknown store ID: XX", report["error_counts"])
        self.assertIn("Unknown product ID: P99", report["error_counts"])


class PartialImportTest(unittest.TestCase):
    def test_partial_sales_import(self):
        conn = _make_conn()
        df, _ = importer.read_file("x.csv", _csv("\n".join([
            ",".join(SALES_COLS), "2026-01-01,S01,P1,5", "2026-01-02,XX,P1,7",
            "2026-01-03,S01,P1,9"])))
        dtype, _ = importer.detect_type(df)
        report = importer.validate(df, dtype, importer._mapping_for(df, dtype), conn)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["valid_count"], 2)
        summary = importer.perform_import(report, conn, refresh=False)
        self.assertEqual(summary["status"], "success")
        self.assertEqual(summary["imported"], 2)
        self.assertEqual(summary["skipped"], 1)
        count = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        self.assertEqual(count, 2)
        # summaries rebuilt (one row per distinct date: 2026-01-01 and 2026-01-03)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM daily_sales_summary").fetchone()[0], 2)

    def test_products_import_creates_row(self):
        conn = _make_conn()
        df, _ = importer.read_file("x.csv", _csv("\n".join([
            ",".join(PRODUCTS_COLS), "P20,Foo,Fitness,Brand,10,20,SUP1"])))
        dtype, _ = importer.detect_type(df)
        report = importer.validate(df, dtype, importer._mapping_for(df, dtype), conn)
        self.assertEqual(report["status"], "ready")
        summary = importer.perform_import(report, conn, refresh=False)
        self.assertEqual(summary["imported"], 1)
        row = conn.execute("SELECT product_id FROM products WHERE product_id='P20'").fetchone()
        self.assertIsNotNone(row)

    def test_stores_import_creates_row(self):
        conn = _make_conn()
        df, _ = importer.read_file("x.csv", _csv("\n".join([
            ",".join(STORES_COLS), "S02,Two,Mumbai,West,STANDARD"])))
        dtype, _ = importer.detect_type(df)
        report = importer.validate(df, dtype, importer._mapping_for(df, dtype), conn)
        summary = importer.perform_import(report, conn, refresh=False)
        self.assertEqual(summary["imported"], 1)
        self.assertIsNotNone(conn.execute(
            "SELECT store_id FROM stores WHERE store_id='S02'").fetchone())

    def test_invalid_full_reject(self):
        conn = _make_conn()
        df, _ = importer.read_file("x.csv", _csv("\n".join([
            ",".join(SALES_COLS), "2026-01-01,XX,P99,5"])))
        dtype, _ = importer.detect_type(df)
        report = importer.validate(df, dtype, importer._mapping_for(df, dtype), conn)
        summary = importer.perform_import(report, conn, refresh=False)
        self.assertEqual(summary["status"], "failed")


class SampleFilesTest(unittest.TestCase):
    def test_build_sample_sales_is_valid(self):
        rows = importer.build_sample("SALES")
        self.assertEqual(len(rows), 12)
        csv_txt = "\n".join([",".join(rows[0].keys())] +
                            [",".join(str(v) for v in r.values()) for r in rows])
        df, _ = importer.read_file("sample.csv", csv_txt.encode())
        dtype, missing = importer.detect_type(df)
        self.assertEqual(dtype, "SALES")
        self.assertEqual(missing, [])

    def test_importable_types(self):
        self.assertEqual(importer.IMPORTABLE_TYPES,
                         frozenset(("SALES", "INVENTORY", "PRODUCTS", "STORES")))


if __name__ == "__main__":
    unittest.main()