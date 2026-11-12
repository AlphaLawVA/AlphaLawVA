# test_compare_precedent_rerankers_v2.py
"""
Description: 판례 리랭커 v2의 20문항 층화 구성, 사건번호 정규화와 골드 기반
순위 지표 계산을 외부 모델 다운로드 없이 검증한다.
Author: choeminju
Date: 2026-11-12
Before:
    - 판례 리랭커 v2 질문 부분집합과 평가 함수가 정의된 상태.
After:
    - 질문 구성과 핵심 지표가 회귀 테스트로 고정된다.
"""

import unittest
from collections import Counter

from ml.evaluation.precedents.compare_precedent_rerankers_v2 import (
    DEFAULT_QUERIES_CSV,
    compute_metrics,
    load_queries,
    normalized_case_key,
)


class ComparePrecedentRerankersV2Tests(unittest.TestCase):
    def test_subset_has_expected_stratified_composition(self):
        queries = load_queries(DEFAULT_QUERIES_CSV)

        self.assertEqual(len(queries), 20)
        self.assertEqual(
            Counter(row["query_specificity"] for row in queries),
            {"specific_life": 10, "legal_mixed": 10},
        )
        type_numbers = {row["dispute_type"].split(".", 1)[0] for row in queries}
        self.assertEqual(type_numbers, {str(number) for number in range(1, 17)})

    def test_case_key_normalizes_duplicate_case_numbers(self):
        first = normalized_case_key("2024다 321973", "613093")
        duplicate = normalized_case_key("2024다321973", "615961")

        self.assertEqual(first, duplicate)
        self.assertEqual(normalized_case_key("", "100"), "precedent:100")

    def test_metrics_use_graded_gold_relevance(self):
        ranked = [
            {"case_key": "a"},
            {"case_key": "x"},
            {"case_key": "b"},
            {"case_key": "c"},
            {"case_key": "y"},
        ]
        metrics = compute_metrics(ranked, {"a": 3, "b": 2, "c": 2})

        self.assertAlmostEqual(metrics["precision@3"], 2 / 3, places=6)
        self.assertEqual(metrics["mrr@5"], 1.0)
        self.assertAlmostEqual(metrics["mean_top3_score"], 5 / 3, places=6)
        self.assertEqual(metrics["recall@5"], 1.0)
        self.assertEqual(metrics["non_gold_top5_count"], 2)
        self.assertGreater(metrics["ndcg@5"], 0)
        self.assertLess(metrics["ndcg@5"], 1)


if __name__ == "__main__":
    unittest.main()
