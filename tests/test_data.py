import unittest

import src.database as db
import src.data_loader as data_loader


class TestDataBootstrap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data_loader.ensure_database()

    def test_tables_exist(self):
        for t in ("stores", "products", "suppliers", "sales", "inventory",
                  "purchase_orders", "promotions", "metrics_snapshot",
                  "daily_sales_summary"):
            self.assertGreater(db.table_row_count(t), 0, t)

    def test_ensure_database_idempotent(self):
        before = {t: db.table_row_count(t) for t in ("sales", "metrics_snapshot")}
        data_loader.ensure_database()
        after = {t: db.table_row_count(t) for t in ("sales", "metrics_snapshot")}
        self.assertEqual(before, after)

    def test_sales_integrity(self):
        neg = db.scalar("SELECT COUNT(*) FROM sales WHERE units_sold < 0 OR revenue < 0")
        self.assertEqual(neg, 0)
        # Tiny deterministic demo network (3 stores, 11 products).
        self.assertLess(db.table_row_count("sales"), 5_000)

    def test_no_negative_stock_in_snapshot(self):
        neg = db.scalar("SELECT COUNT(*) FROM metrics_snapshot WHERE stock < 0")
        self.assertEqual(neg, 0)

    def test_stocked_out_demo_exists(self):
        # P-DEMO-007 @ S03 is the designed stock-out edge case (stock hits 0).
        row = db.fetchone("""
            SELECT stock, risk FROM metrics_snapshot
            WHERE store_id = 'S03' AND product_id = 'P-DEMO-007'""")
        self.assertIsNotNone(row)
        self.assertEqual(row["stock"], 0)
        self.assertEqual(row["risk"], "STOCKED_OUT")

    def test_product_pairs(self):
        pairs = data_loader.product_pairs()
        self.assertGreater(len(pairs), 0)
        r = pairs[0]
        for k in ("store_id", "product_id", "product_name", "stock", "risk",
                  "cover_days", "history_days", "avg_daily_demand_30d"):
            self.assertIn(k, r)

    def test_promotions_target_selling_pairs(self):
        bad = db.scalar("""
            SELECT COUNT(*) FROM promotions p
            WHERE NOT EXISTS (
                SELECT 1 FROM sales s
                WHERE s.product_id = p.product_id AND s.store_id = p.store_id)""")
        self.assertEqual(bad, 0)


if __name__ == "__main__":
    unittest.main()