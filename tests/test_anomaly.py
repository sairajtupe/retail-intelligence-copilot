import unittest

import src.anomaly_detection as ad


class TestAnomalyDetection(unittest.TestCase):
    def test_detect_anomalies_shape(self):
        an = ad.detect_anomalies(limit=50)
        self.assertGreater(len(an), 0)
        for r in an:
            self.assertIn("signal", r)
            self.assertIn("store_id", r)
            self.assertIn("product_id", r)
            self.assertIn("change_pct", r)
            self.assertIn("verdict", r)
            self.assertEqual(r["anomaly_type"], r["signal"])

    def test_spike_present(self):
        an = ad.detect_anomalies(limit=200)
        self.assertIn("SPIKE", [r["signal"] for r in an])

    def test_natural_spike_kept(self):
        an = ad.detect_anomalies(limit=200)
        c = next(r for r in an
                 if r["store_id"] == "S02" and r["product_id"] == "P-DEMO-003")
        self.assertEqual(c["signal"], "SPIKE")
        self.assertGreater(c["change_pct"], 100)
        self.assertEqual(c["verdict"], "natural_signal")

    def test_promo_spike_attributed_to_promotion(self):
        an = ad.detect_anomalies(limit=200)
        hits = [r for r in an if r["store_id"] == "S02"
                and r["product_id"] == "P-DEMO-011"]
        self.assertTrue(hits)
        hit = hits[0]
        self.assertEqual(hit["signal"], "SPIKE")
        self.assertEqual(hit["verdict"], "promotion_driven")
        self.assertEqual(hit["promo_name"], "Member Exclusive")
        self.assertTrue(hit["promo_overlap"])

    def test_pair_anomalies(self):
        an = ad.pair_anomalies("S01", "P-DEMO-001")
        self.assertIn("signal", an)
        self.assertIn("change_pct", an)
        self.assertIn("verdict", an)


if __name__ == "__main__":
    unittest.main()