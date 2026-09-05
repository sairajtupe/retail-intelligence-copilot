import unittest

import src.recommendation_engine as re

PRIORITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}


class TestRecommendations(unittest.TestCase):
    def test_build_for_valid(self):
        recs = re.build_for(max_items=100)
        self.assertGreater(len(recs), 0)
        for r in recs:
            for k in ("issue_type", "priority", "store_id", "product_id",
                      "product_name", "reason", "recommended_action", "evidence"):
                self.assertIn(k, r, r.get("product_id", ""))
            self.assertTrue(r["evidence"])
            self.assertTrue(r["policy_citations"])

    def test_priorities_confined(self):
        for r in re.build_for(max_items=100):
            self.assertIn(r["priority"], PRIORITIES)

    def test_suppressions(self):
        sup = re.suppression_reasons()
        for r in sup:
            self.assertIn("history_days", r)
            self.assertIn("data_quality", r)
            self.assertTrue(r["history_days"] < 21 or r["data_quality"])

    def test_no_suppressed_pairs_in_lists(self):
        sup_ids = {(r["store_id"], r["product_id"]) for r in re.suppression_reasons()}
        for r in re.build_for(max_items=100):
            if r["issue_type"] != "DATA_QUALITY_ISSUE":
                self.assertNotIn((r["store_id"], r["product_id"]), sup_ids)
        # Every pair in the tiny dataset has >= 21 days of history and no
        # data-quality flags, so nothing should be suppressed.
        self.assertEqual(sup_ids, set())


if __name__ == "__main__":
    unittest.main()