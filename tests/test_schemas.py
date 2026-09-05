import unittest

from pydantic import ValidationError

from src.schemas import CopilotRequest, AttentionRequest, ProductQuery


class TestSchemas(unittest.TestCase):
    def test_copilot_ok(self):
        r = CopilotRequest(query="stock out", max_items=5)
        self.assertEqual(r.max_items, 5)
        self.assertTrue(r.use_llm)

    def test_copilot_rejects_empty(self):
        with self.assertRaises(ValidationError):
            CopilotRequest(query="")

    def test_copilot_rejects_long(self):
        with self.assertRaises(ValidationError):
            CopilotRequest(query="x" * 501)

    def test_attention_scope_validation(self):
        with self.assertRaises(ValidationError):
            AttentionRequest(scope="bad")

    def test_product_query(self):
        self.assertEqual(ProductQuery().q, "")


if __name__ == "__main__":
    unittest.main()