import unittest

from src.config import THRESHOLDS, BASE_DIR, DATA_DIR, INDEX_DIR, PORT


class TestConfig(unittest.TestCase):
    def test_thresholds_present(self):
        for k in ("min_history_days", "safety_stock_ratio", "safety_stock_floor"):
            self.assertIn(k, THRESHOLDS)
        self.assertGreaterEqual(THRESHOLDS["min_history_days"], 21)

    def test_paths(self):
        self.assertTrue((BASE_DIR / "app.py").exists())
        self.assertTrue(DATA_DIR.is_dir())
        self.assertTrue(INDEX_DIR.is_dir())

    def test_port(self):
        self.assertEqual(PORT, 8000)


if __name__ == "__main__":
    unittest.main()