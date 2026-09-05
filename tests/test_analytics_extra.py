"""Tests for the extended analytics endpoints (KPI cards, lists, details)."""
import unittest

from src import analytics


class ExtendedAnalyticsTest(unittest.TestCase):
    def test_kpi_cards_ten(self):
        cards = analytics.kpi_cards()
        self.assertEqual(len(cards), 10)
        keys = {c["key"] for c in cards}
        self.assertIn("total_sales_30d", keys)
        self.assertIn("overstock", keys)
        for c in cards:
            self.assertIn(c["format"], ("money", "percent", "number"))

    def test_kpi_cards_store_filtered(self):
        cards = analytics.kpi_cards(store_ids=["S01"])
        self.assertEqual(len(cards), 10)

    def test_sales_summary_periods(self):
        s = analytics.sales_summary()
        labels = [p["period"] for p in s["periods"]]
        self.assertEqual(labels, ["today", "last_7_days", "last_30_days",
                                  "last_90_days", "last_365_days"])
        for p in s["periods"]:
            self.assertGreaterEqual(p["revenue"], 0)
            self.assertGreaterEqual(p["units"], 0)
        self.assertGreater(s["lifetime"]["revenue"], 0)
        self.assertGreaterEqual(s["lifetime"]["days"], 1)

    def test_sales_summary_single_store(self):
        s = analytics.sales_summary(store_ids=["S01"])
        self.assertGreater(s["periods"][1]["units"], 0)

    def test_coverage_distribution_rounds_to_100(self):
        dist = analytics.coverage_distribution()
        total = sum(d["count"] for d in dist)
        self.assertGreater(total, 0)
        self.assertAlmostEqual(sum(d["pct"] for d in dist), 100.0, places=1)
        self.assertEqual(len(dist), 6)

    def test_status_buckets_partition(self):
        res = analytics.status_buckets()
        self.assertEqual(sum(b["count"] for b in res["buckets"]), res["total_pairs"])
        self.assertGreater(res["total_pairs"], 0)
        statuses = {b["status"] for b in res["buckets"]}
        self.assertIn("Healthy", statuses)
        self.assertIn("Out of Stock", statuses)

    def test_low_stock_list(self):
        items = analytics.low_stock_list(limit=50)
        self.assertTrue(items)
        first = items[0]
        for key in ("store_id", "product_id", "SKU", "product_name", "stock",
                    "cover_days", "risk", "action", "action_qty"):
            self.assertIn(key, first)
        stocked_out = [i for i in items if i["risk"] == "STOCKED_OUT"]
        self.assertTrue(stocked_out)

    def test_overstock_list(self):
        items = analytics.overstock_list(limit=20)
        self.assertTrue(items)
        self.assertIn("excess_units", items[0])
        self.assertIn("excess_value", items[0])

    def test_slow_moving_list(self):
        items = analytics.slow_moving_list(limit=20)
        self.assertTrue(items)
        self.assertTrue(all(i["units_30d"] > 0 for i in items))

    def test_store_detail(self):
        detail = analytics.store_detail("S01")
        self.assertIsNotNone(detail)
        self.assertEqual(detail["store"]["store_id"], "S01")
        for key in ("kpis", "top_products", "attention", "trend", "summary"):
            self.assertIn(key, detail)
        self.assertTrue(detail["top_products"])
        self.assertTrue(detail["trend"])

    def test_store_detail_unknown(self):
        self.assertIsNone(analytics.store_detail("NOPE"))

    def test_product_list_search(self):
        res = analytics.product_list(q="demo")
        self.assertGreater(res["total"], 0)
        for item in res["items"]:
            self.assertTrue("demo" in item["product_name"].lower()
                            or "demo" in item["product_id"].lower())

    def test_product_list_pagination_and_sort(self):
        page1 = analytics.product_list(sort="revenue", order="desc", page=1, page_size=5)
        page2 = analytics.product_list(sort="revenue", order="desc", page=2, page_size=5)
        self.assertEqual(len(page1["items"]), 5)
        self.assertEqual(len(page2["items"]), 5)
        self.assertGreaterEqual(page1["total"], 10)
        revenues = [i["revenue_30d"] for i in page1["items"]]
        self.assertEqual(revenues, sorted(revenues, reverse=True))
        self.assertNotEqual(page1["items"][0]["product_id"], page2["items"][0]["product_id"])

    def test_product_list_store_filtered(self):
        all_res = analytics.product_list(page_size=5)
        s01 = analytics.product_list(store_ids=["S01"], page_size=5)
        self.assertGreaterEqual(all_res["total"], s01["total"])

    def test_product_trend(self):
        rows = analytics.product_trend("P-DEMO-001", "S01", days=30)
        self.assertEqual(len(rows), 30)
        self.assertTrue(all(r["units"] >= 0 for r in rows))
        self.assertTrue(all(r["stock"] >= 0 for r in rows))


if __name__ == "__main__":
    unittest.main()