import unittest

from src.schemas import CopilotRequest
import src.copilot as copilot


def ask(q: str, store_id: str | None = None, use_llm: bool = False) -> dict:
    return copilot.answer(CopilotRequest(query=q, store_id=store_id, use_llm=use_llm))


class TestCopilot(unittest.TestCase):
    def test_attention(self):
        r = ask("What needs attention today?")
        self.assertEqual(r["status"], "answered")
        self.assertTrue(r["answer"])
        self.assertFalse(r["llm_used"])

    def test_stockout_reports_demo_values(self):
        r = ask("Which products are running out of stock?", store_id="S01")
        self.assertEqual(r["intent"], "STOCKOUT")
        self.assertEqual(r["status"], "answered")
        self.assertIn("Cola Max 300ml @ S01", r["answer"])
        self.assertIn("order 91 units", r["answer"])
        self.assertIn("CRITICAL", r["answer"])

    def test_product_performance(self):
        r = ask("Tell me about P-DEMO-001")
        self.assertEqual(r["intent"], "PRODUCT_PERFORMANCE")
        self.assertIn("Cola Max", r["answer"])
        self.assertTrue(r["key_metrics"])
        self.assertTrue(r["evidence"])

    def test_unknown_product(self):
        r = ask("How is Apple doing?")
        self.assertEqual(r["intent"], "UNKNOWN_PRODUCT")
        self.assertTrue(r["needs_clarification"])
        self.assertEqual(r["status"], "insufficient_data")

    def test_greeting(self):
        r = ask("hi")
        self.assertIn("Retail Intelligence", r["answer"])

    def test_unanswerable(self):
        r = ask("Weather tomorrow?")
        self.assertEqual(r["status"], "insufficient_data")
        self.assertEqual(r["answer"], "I don't have enough data to answer reliably.")

    def test_overstock(self):
        r = ask("What are the overstock situations?")
        self.assertEqual(r["intent"], "OVERSTOCK")
        self.assertGreater(len(r["key_metrics"]), 0)
        self.assertIn("LED Desk Lamp", r["answer"])

    def test_forecast(self):
        r = ask("Forecast next week")
        self.assertEqual(r["intent"], "FORECAST")
        self.assertIn("units", r["answer"])

    def test_sales_spike(self):
        r = ask("Why did sales spike?")
        self.assertEqual(r["intent"], "SALES_SPIKE")
        self.assertIn("Portable Charger", r["answer"])

    def test_leaderboard(self):
        r = ask("What is the best margin product?")
        self.assertEqual(r["intent"], "LEADERBOARD")
        self.assertIn("margin", r["answer"].lower())

    def test_recommendations_attached(self):
        r = ask("What needs attention today?")
        self.assertIsInstance(r["recommendations"], list)

    def test_policy_citations_present(self):
        r = ask("Which products are running out of stock?")
        self.assertIsInstance(r["policy_citations"], list)
        self.assertTrue(r["policy_citations"])

    def test_data_used_reported(self):
        r = ask("What needs attention today?")
        self.assertEqual(r["data_used"]["index"], True)
        self.assertTrue(r["data_used"]["tables"])

    def test_market_removed(self):
        # The Market feature is gone: the question is answered from local data
        # via the GENERAL fallback, never a simulated MARKET intent.
        r = ask("What is the market situation?")
        self.assertNotEqual(r["intent"], "MARKET")
        self.assertEqual(r["status"], "answered")
        self.assertNotIn("Retail demand index", [m["label"] for m in r["key_metrics"]])
        self.assertNotIn("DEMO/SIMULATED", r["answer"])


if __name__ == "__main__":
    unittest.main()