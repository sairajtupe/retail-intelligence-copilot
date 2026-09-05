import unittest

import src.retrieval as retrieval


class TestRetrieval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        retrieval._load()

    def test_chunks_loaded(self):
        self.assertGreaterEqual(retrieval.chunk_count(), 80)

    def test_doc_sources_span_policies(self):
        docs = retrieval.context("inventory safety stock policy", top_k=6)
        self.assertGreaterEqual(len(docs), 3)
        joined = " ".join(str(d) for d in docs)
        self.assertIn("inventory_policy", joined)

    def test_requires_policy_query(self):
        docs = retrieval.context("What needs attention today?", top_k=4)
        self.assertGreaterEqual(len(docs), 1)

    def test_context_is_deterministic(self):
        a = retrieval.context("stockout cover lead time", top_k=5)
        b = retrieval.context("stockout cover lead time", top_k=5)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()