import unittest

import src.analytics as analytics


class TestAnalytics(unittest.TestCase):
    def test_dashboard_kpis(self):
        k = analytics.dashboard_kpis()
        self.assertGreater(k["kpis"]["revenue_30d"], 0)
        self.assertGreater(k["kpis"]["units_30d"], 0)
        self.assertGreater(k["kpis"]["inventory_value"], 0)
        self.assertGreater(k["kpis"]["product_store_pairs"], 0)

    def test_trend(self):
        t = analytics.trend(30)
        self.assertEqual(len(t), 30)
        self.assertGreater(t[-1]["revenue"], 0)

    def test_categories(self):
        c = analytics.category_performance()
        self.assertGreater(len(c), 5)
        total_share = round(sum(x["share_pct"] for x in c), 1)
        self.assertAlmostEqual(total_share, 100.0, delta=0.5)

    def test_store_comparison(self):
        s = analytics.store_comparison()
        self.assertEqual(len(s), 3)
        self.assertEqual(sorted(x["store_id"] for x in s),
                         ["S01", "S02", "S03"])

    def test_attention_demo_scenarios(self):
        items = analytics.attention_items(scope="all", max_items=4000)
        by = {}
        for it in items:
            by[(it["store_id"], it["product_id"], it["issue_type"])] = it
        # verified demo scenarios on the tiny dataset
        self.assertIn(("S01", "P-DEMO-001", "STOCKOUT_RISK"), by)
        self.assertIn(("S01", "P-DEMO-002", "STOCKOUT_RISK"), by)
        self.assertIn(("S02", "P-DEMO-006", "OVERSTOCK"), by)
        self.assertIn(("S02", "P-DEMO-003", "SALES_SPIKE"), by)
        self.assertIn(("S01", "P-DEMO-005", "SALES_DROP"), by)
        for it in items:
            self.assertTrue(it["issue_id"])
            self.assertIn(it["priority"], ("CRITICAL", "HIGH", "MEDIUM", "LOW"))
            self.assertIn("metrics", it)

    def test_attention_filters(self):
        stock = analytics.attention_items(scope="stockout", max_items=50)
        self.assertTrue(all(i["issue_type"] == "STOCKOUT_RISK" for i in stock))
        s1 = analytics.attention_items(scope="all", store_id="S01", max_items=100)
        self.assertTrue(all(i["store_id"] == "S01" for i in s1))

    def test_evidence_present(self):
        items = analytics.attention_items(scope="stockout", max_items=10)
        self.assertTrue(all(i.get("evidence") for i in items))

    def test_inventory_health(self):
        h = analytics.inventory_health()
        self.assertGreater(h["total_inventory_value"], 0)
        self.assertEqual(sum(b["pct"] for b in h["coverage_buckets"]), 100.0)

    def test_top_products(self):
        top = analytics.top_products(30, 10)
        self.assertEqual(len(top), 10)
        revs = [t["revenue"] for t in top]
        self.assertEqual(revs, sorted(revs, reverse=True))


if __name__ == "__main__":
    unittest.main()