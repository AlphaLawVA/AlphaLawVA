# test_evaluate_precedent_chunk_labels.py
"""
Description: 판례 질문-청크 평가의 블라인드 ID와 graded relevance 지표를
외부 모델이나 법률 데이터 없이 검증한다.
Author: choeminju
Date: 2026-11-12
Before:
    - 질문-청크 블라인드 평가 및 점수 계산 함수가 정의된 상태.
After:
    - 평가 ID 안정성과 청크 선택 벌점 계산이 회귀 테스트로 고정된다.
"""

import unittest

from ml.evaluation.precedents.evaluate_precedent_chunk_labels import (
    adjudication_id,
    compute_chunk_metrics,
)


class EvaluatePrecedentChunkLabelsTests(unittest.TestCase):
    def test_adjudication_id_is_stable_and_input_sensitive(self):
        first = adjudication_id("q1", "case1", "chunk1")

        self.assertEqual(first, adjudication_id("q1", "case1", "chunk1"))
        self.assertNotEqual(first, adjudication_id("q1", "case1", "chunk2"))
        self.assertEqual(len(first), 16)

    def test_chunk_metrics_compare_selected_chunks_with_oracle(self):
        metrics = compute_chunk_metrics(
            selected_relevances=[2, 3, 0, 1, 2],
            oracle_relevances=[3, 3, 2, 2, 1, 0],
        )

        self.assertAlmostEqual(metrics["precision@3"], 2 / 3, places=6)
        self.assertEqual(metrics["mrr@5"], 1.0)
        self.assertGreater(metrics["ndcg@5"], 0)
        self.assertLess(metrics["ndcg@5"], 1)


if __name__ == "__main__":
    unittest.main()
