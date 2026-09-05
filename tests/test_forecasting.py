import unittest

import src.forecasting as forecasting


class TestForecasting(unittest.TestCase):
    def test_forecast_dashboard(self):
        f = forecasting.forecast_dashboard(14)
        self.assertEqual(f["method"], "weighted_moving_average")
        self.assertEqual(len(f["forecast_units"]), 14)
        for r in f["forecast_units"]:
            self.assertIn("date", r)
            self.assertIn("units", r)
            self.assertIn("revenue", r)
            self.assertGreater(r["units"], 0)
        self.assertGreater(f["base_units"], 0)

    def test_forecast_daily_units(self):
        f = forecasting.forecast_daily_units("P-DEMO-001", "S01", horizon_days=7)
        self.assertEqual(f["horizon_days"], 7)
        self.assertEqual(len(f["forecast_units"]), 7)
        self.assertTrue(all(u >= 0 for u in
                            [x["forecast_units"] for x in f["forecast_units"]]))


if __name__ == "__main__":
    unittest.main()