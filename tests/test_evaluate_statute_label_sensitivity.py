# test_evaluate_statute_label_sensitivity.py
"""
Description: 법령 검색 지표와 라벨 민감도 비교 계산을 테스트한다.
Author: ooheunsu
Date: 2026-09-02
Before:
    - 라벨 민감도 평가 모듈이 구현된 상태.

After:
    - 지표 공식과 독립 라벨 대체 및 모델 순위 비교를 자동 검증.
"""

import math
import unittest

from ml.evaluation.evaluate_statute_label_sensitivity import (
    build_label_policies,
    dcg,
    evaluate,
    query_metrics,
)


class EvaluateStatuteLabelSensitivityTests(unittest.TestCase):
    def test_dcg_uses_exponential_gain(self):
        expected = 7 + 3 / math.log2(3) + 1 / math.log2(4)
        self.assertAlmostEqual(dcg([3, 2, 1]), expected)

    def test_query_metrics_uses_relevance_two_as_positive(self):
        result = query_metrics(
            ["noise", "answer_a", "topic", "answer_b", *[f"n{i}" for i in range(6)]],
            {"answer_a": 3, "answer_b": 2, "topic": 1},
        )

        self.assertEqual(result["recall_at_5"], 1.0)
        self.assertEqual(result["recall_at_10"], 1.0)
        self.assertEqual(result["mrr_at_10"], 0.5)
        self.assertEqual(result["precision_at_10"], 0.2)
        self.assertEqual(result["hit_at_10"], 1.0)

    def test_independent_policy_overrides_only_adjudicated_chunks(self):
        cases = [
            {
                "query_id": "q1",
                "judgments": [
                    {"chunk_id": "a", "relevance": 2},
                    {"chunk_id": "b", "relevance": 3},
                ],
            }
        ]
        adjudication = [
            {
                "query_id": "q1",
                "decisions": [
                    {"chunk_id": "a", "independent_relevance": 1}
                ],
            }
        ]

        policies = build_label_policies(cases, adjudication)

        self.assertEqual(policies["majority"]["q1"], {"a": 2, "b": 3})
        self.assertEqual(policies["independent"]["q1"], {"a": 1, "b": 3})

    def test_evaluate_reports_model_order_change(self):
        cases = [
            {
                "query_id": "q1",
                "critical": False,
                "judgments": [
                    {"chunk_id": "a", "relevance": 2},
                    {"chunk_id": "b", "relevance": 1},
                ],
            }
        ]
        adjudication = [
            {
                "query_id": "q1",
                "decisions": [
                    {"chunk_id": "a", "independent_relevance": 1},
                    {"chunk_id": "b", "independent_relevance": 2},
                ],
            }
        ]
        filler = [
            {"rank": rank, "chunk_id": f"n{rank}"}
            for rank in range(2, 11)
        ]
        rankings = {
            "model_a": {"q1": [{"rank": 1, "chunk_id": "a"}, *filler]},
            "model_b": {"q1": [{"rank": 1, "chunk_id": "b"}, *filler]},
        }

        result = evaluate(cases, adjudication, rankings)

        self.assertEqual(result["comparison"]["majority_order"][0], "model_a")
        self.assertEqual(result["comparison"]["independent_order"][0], "model_b")
        self.assertTrue(result["comparison"]["model_order_changed"])


if __name__ == "__main__":
    unittest.main()
