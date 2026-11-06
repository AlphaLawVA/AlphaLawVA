# test_rag_eval_retrieval.py
"""
Description: 검색 평가 지표와 설정 비교 도구를 가짜 검색기로 검증한다. 모델이나 DB 없이 실행된다.
Author: ooheunsu
Date: 2026-11-06
Before:
    - ml.evaluation.rag_eval.retrieval_metrics와 retrieval_eval이 존재.
After:
    - 지표 계산, 순위 합성, 서비스 방식 필터, 결과 저장이 오프라인에서 검증됨.
"""

import json
import tempfile
import unittest
from pathlib import Path

from ml.evaluation.rag_eval import retrieval_eval as ev
from ml.evaluation.rag_eval.retrieval_metrics import (
    evaluate_rankings,
    paired_bootstrap_difference,
    query_metrics_at_k,
)


class FakeSource:
    def __init__(self):
        self.ids = [f"c{i}" for i in range(1, 11)]

    def dense(self, queries, depth):
        # c1이 가장 유사하지만 질문1 정답은 c3, 질문2 정답은 c1
        return [[(c, 0.9 - 0.05 * i) for i, c in enumerate(self.ids)] for _ in queries]

    def bm25(self, query, depth, tokenizer="word"):
        order = ["c3", "c2", "c1"] if query == "q1" else ["c1", "c4"]
        return [(c, 10.0 - i) for i, c in enumerate(order)]

    def corpus_ids(self):
        return list(self.ids)


CASES = [
    {"query_id": "a", "question": "q1", "judgments": [
        {"chunk_id": "c3", "relevance": 3}, {"chunk_id": "c1", "relevance": 1}]},
    {"query_id": "b", "question": "q2", "judgments": [
        {"chunk_id": "c1", "relevance": 2}, {"chunk_id": "c9", "relevance": 2}]},
]


class MetricsTest(unittest.TestCase):
    def test_query_metrics(self):
        m = query_metrics_at_k(["x", "c3", "y"], {"c3": 3, "c1": 1}, 3)
        self.assertAlmostEqual(m["recall"], 1.0)
        self.assertAlmostEqual(m["mrr"], 0.5)
        self.assertAlmostEqual(m["context_precision"], 0.5)
        self.assertAlmostEqual(m["precision"], 1 / 3)

    def test_requires_positive_label(self):
        with self.assertRaises(ValueError):
            query_metrics_at_k(["a"], {"a": 1}, 3)

    def test_mismatched_queries(self):
        with self.assertRaises(ValueError):
            evaluate_rankings({"a": ["x"]}, {"b": {"x": 3}})

    def test_bootstrap_zero_diff(self):
        r = paired_bootstrap_difference({"a": 1.0, "b": 0.0}, {"a": 1.0, "b": 0.0})
        self.assertEqual(r["mean_diff"], 0.0)
        self.assertEqual((r["ci_low"], r["ci_high"]), (0.0, 0.0))


class ConfigTest(unittest.TestCase):
    def test_rankers(self):
        c = ev.QueryCandidates(
            dense=[("a", 0.9), ("b", 0.8)], bm25=[("b", 5.0), ("c", 1.0)], bm25_ngram=[("c", 3.0)]
        )
        self.assertEqual(ev.rank_dense(c), ["a", "b"])
        self.assertEqual(ev.rank_rrf(c)[0], "b")
        self.assertEqual(ev.rank_weighted(c, 1.0)[0], "b")
        self.assertEqual(ev.rank_weighted(c, 0.0)[0], "a")
        self.assertEqual(ev.rank_bm25_ngram(c), ["c"])
        self.assertEqual(ev.bigram_tokens("전입신고를 A"), ("전입", "입신", "신고", "고를", "a"))

    def test_min_similarity_and_expansion(self):
        c = ev.QueryCandidates(dense=[("a", 0.5), ("b", 0.2)], bm25=[], bm25_ngram=[])
        self.assertEqual(ev.dense_with_min_similarity(c, top_k=3, min_similarity=0.35), ["a"])
        self.assertEqual(ev.expand_with_neighbors(["c2"], ["c1", "c2", "c3"], 1), ["c2", "c1", "c3"])

    def test_end_to_end_and_outputs(self):
        results = ev.run_retrieval_eval(CASES, FakeSource())
        self.assertEqual(results["query_count"], 2)
        dense = results["methods"]["dense"]["macro"]["3"]
        self.assertAlmostEqual(dense["recall"], (1.0 + 0.5) / 2)
        self.assertIn("dense_k3_min0.35", results["service_view"])
        config = {"run_id": "t", "dataset": "d", "dataset_sha256": "x",
                  "depth": 50, "embedding_model": "fake"}
        with tempfile.TemporaryDirectory() as tmp:
            ev.write_outputs(Path(tmp), config, results)
            self.assertIn("방식별 비교", (Path(tmp) / "summary.md").read_text(encoding="utf-8"))
            self.assertEqual(json.loads((Path(tmp) / "results.json").read_text(encoding="utf-8"))["query_count"], 2)


if __name__ == "__main__":
    unittest.main()
