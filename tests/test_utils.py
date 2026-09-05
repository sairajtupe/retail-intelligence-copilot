import unittest
from datetime import date

from src.utils import (parse_date, fmt_num, fmt_money, steady_id, overlaps,
                       month_start, add_months, text_hash, safe_int, safe_float)


class TestUtils(unittest.TestCase):
    def test_parse_date(self):
        self.assertIsNone(parse_date(""))
        self.assertEqual(parse_date("2026-08-31").isoformat(), "2026-08-31")

    def test_fmt(self):
        self.assertEqual(fmt_num(1234), "1,234")
        self.assertEqual(fmt_money(1234.5), "₹1,234")
        self.assertEqual(fmt_money(1235), "₹1,235")
        self.assertEqual(fmt_money(12.5), "₹12.50")
        self.assertEqual(fmt_money(123456), "₹1.2L")
        self.assertEqual(fmt_money(250000), "₹2.5L")
        self.assertEqual(fmt_money(15000000), "₹1.50Cr")
        self.assertEqual(fmt_money(123456789), "₹12.35Cr")
        self.assertEqual(fmt_num(0.123), "0.12")

    def test_steady_id(self):
        a = steady_id("S01", "P-DEMO-001", "STOCKOUT_RISK")
        b = steady_id("S01", "P-DEMO-001", "STOCKOUT_RISK")
        c = steady_id("S01", "P-DEMO-001", "OVERSTOCK")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(len(a), 12)

    def test_overlaps(self):
        self.assertTrue(overlaps(date(2026, 8, 1), date(2026, 8, 5),
                                 date(2026, 8, 3), date(2026, 8, 8)))
        self.assertFalse(overlaps(date(2026, 8, 1), date(2026, 8, 5),
                                  date(2026, 8, 6), date(2026, 8, 8)))

    def test_months(self):
        self.assertEqual(month_start(date(2026, 8, 31)).isoformat(), "2026-08-01")
        self.assertEqual(add_months(date(2026, 1, 31), 1).isoformat(), "2026-02-28")

    def test_text_hash_deterministic(self):
        h1 = text_hash("hello world")
        h2 = text_hash("hello world")
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 256)

    def test_safe_int_float(self):
        self.assertEqual(safe_int("12"), 12)
        self.assertEqual(safe_float(None), 0.0)
        self.assertEqual(safe_float("x"), 0.0)


if __name__ == "__main__":
    unittest.main()