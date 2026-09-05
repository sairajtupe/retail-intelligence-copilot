import unittest

from src.config import THRESHOLDS
import src.inventory_engine as inv


class TestInventoryEngine(unittest.TestCase):
    def test_stockout_demo_scenarios(self):
        risks = inv.stockout_risk(limit=200)
        by = {(r["store_id"], r["product_id"]): r for r in risks}
        self.assertEqual(by[("S01", "P-DEMO-001")]["risk"], "CRITICAL")
        self.assertEqual(by[("S01", "P-DEMO-002")]["risk"], "HIGH")
        self.assertEqual(by[("S03", "P-DEMO-007")]["risk"], "STOCKED_OUT")
        self.assertGreater(by[("S01", "P-DEMO-001")]["recommended_order_qty"], 0)
        for r in risks:
            self.assertTrue(r["evidence"])

    def test_reorder_math_consistent(self):
        sug = inv.reorder_suggestions(limit=50)
        for r in sug:
            ads = r["avg_daily_demand_30d"]
            lt = r["lead_time_days"]
            target = ads * lt + max(ads * lt * THRESHOLDS["safety_stock_ratio"],
                                    THRESHOLDS["safety_stock_floor"])
            expected = int(round(max(target - r["current_stock"], 0)))
            self.assertEqual(r["suggested_quantity"], expected)
            self.assertGreater(r["target_level"], 0)

    def test_transfers_shape(self):
        tr = inv.transfer_opportunities(limit=50)
        for r in tr:
            self.assertIn("from_store", r)
            self.assertIn("to_store", r)
            self.assertGreaterEqual(r["recommended_units"], 0)
            self.assertGreaterEqual(r["transfer_value"], 0)


if __name__ == "__main__":
    unittest.main()