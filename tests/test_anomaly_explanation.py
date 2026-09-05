import unittest

import src.anomaly_detection as ad
import src.analytics as analytics
import src.anomaly_explanation as ax
from src.gemini_client import GeminiUnavailable


def _finding(store_id, product_id, signal=None, limit=300):
    for a in ad.detect_anomalies(limit=limit):
        if a["store_id"] == store_id and a["product_id"] == product_id \
                and (signal is None or a["signal"] == signal):
            return a
    return None


class TestAnomalyExplanation(unittest.TestCase):
    def test_spike_explanation_grounded_and_nonblank(self):
        a = _finding("S02", "P-DEMO-003", "SPIKE")
        self.assertIsNotNone(a)
        res = ax.explain_anomaly(a, use_llm=False)
        self.assertEqual(res["source"], "deterministic")
        self.assertFalse(res["llm_used"])
        self.assertTrue(res["explanation"].strip())
        self.assertIn("Portable Charger", res["explanation"])
        self.assertIn("natural demand signal", res["explanation"])
        self.assertGreater(res["grounded"]["current_stock"], 0)
        self.assertEqual(res["grounded"]["signal"], "SPIKE")

    def test_drop_explanation_nonblank(self):
        a = _finding("S01", "P-DEMO-005", "DROP")
        self.assertIsNotNone(a)
        res = ax.explain_anomaly(a, use_llm=False)
        self.assertTrue(res["explanation"].strip())
        self.assertIn("dropped", res["explanation"])

    def test_promo_explanation_attributes_to_promotion(self):
        a = _finding("S02", "P-DEMO-011", "SPIKE")
        self.assertIsNotNone(a)
        res = ax.explain_anomaly(a, use_llm=False)
        self.assertIn("Member Exclusive", res["explanation"])
        self.assertIn("promotion", res["explanation"].lower())
        self.assertTrue(res["grounded"]["promo_overlap"])

    def test_gemini_path_used_when_available(self):
        a = _finding("S02", "P-DEMO-003", "SPIKE")
        old_flag, old_fn = ax.GEMINI_AVAILABLE, ax.generate_json
        try:
            ax.GEMINI_AVAILABLE = True
            ax.generate_json = lambda *args, **kwargs: {"explanation": "Grounded Gemini explanation."}
            res = ax.explain_anomaly(a, use_llm=True)
            self.assertEqual(res["source"], "gemini")
            self.assertTrue(res["llm_used"])
            self.assertEqual(res["explanation"], "Grounded Gemini explanation.")
        finally:
            ax.GEMINI_AVAILABLE, ax.generate_json = old_flag, old_fn

    def test_gemini_unavailable_falls_back(self):
        a = _finding("S02", "P-DEMO-003", "SPIKE")
        old_flag, old_fn = ax.GEMINI_AVAILABLE, ax.generate_json
        try:
            ax.GEMINI_AVAILABLE = True
            ax.generate_json = lambda *a, **k: (_ for _ in ()).throw(
                GeminiUnavailable("boom"))
            res = ax.explain_anomaly(a, use_llm=True)
            self.assertEqual(res["source"], "deterministic")
            self.assertFalse(res["llm_used"])
            self.assertTrue(res["explanation"].strip())
        finally:
            ax.GEMINI_AVAILABLE, ax.generate_json = old_flag, old_fn

    def test_gemini_empty_response_falls_back(self):
        a = _finding("S02", "P-DEMO-003", "SPIKE")
        old_flag, old_fn = ax.GEMINI_AVAILABLE, ax.generate_json
        try:
            ax.GEMINI_AVAILABLE = True
            ax.generate_json = lambda *a, **k: {}
            res = ax.explain_anomaly(a, use_llm=True)
            self.assertEqual(res["source"], "deterministic")
            self.assertTrue(res["explanation"].strip())
        finally:
            ax.GEMINI_AVAILABLE, ax.generate_json = old_flag, old_fn

    def test_missing_data_never_blank(self):
        res = ax.explain_anomaly(
            ax.build_anomaly("S99", "P-DEMO-999", "SPIKE"), use_llm=False)
        self.assertTrue(res["explanation"].strip())

    def test_enrich_attention_items(self):
        anomalies = ad.detect_anomalies(limit=200)
        items = analytics.attention_items(scope="all", store_id="S02", max_items=50)
        items = ax.enrich_items(items, anomalies)
        spikes = [i for i in items if i.get("issue_type") == "SALES_SPIKE"]
        drops = [i for i in items if i.get("issue_type") == "SALES_DROP"]
        self.assertTrue(spikes)
        for i in spikes + drops:
            self.assertTrue(i.get("explanation"))
            self.assertIn(i["explanation_source"], ("deterministic", "gemini"))

    def test_enrich_raw_findings(self):
        anomalies = ad.detect_anomalies(limit=20)
        items = ax.enrich_items(anomalies, anomalies)
        self.assertEqual(len(items), len(anomalies))
        for i in items:
            self.assertTrue(i.get("explanation"))

    def test_find_anomaly(self):
        a = ax.find_anomaly("S02", "P-DEMO-003", "SPIKE")
        self.assertIsNotNone(a)


if __name__ == "__main__":
    unittest.main()