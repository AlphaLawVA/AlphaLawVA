# test_compare_precedent_bm25_hybrid.py
"""
Description: 판례 BM25 색인, Weighted RRF와 골드 지표 계산을
외부 모델이나 ChromaDB 없이 검증한다.
Author: choeminju
Date: 2026-11-13
Before:
    - 판례 Dense·BM25 하이브리드 비교 함수가 정의된 상태.
After:
    - BM25 판례 묶기, 가중 순위 융합과 Recall 계산이 회귀 테스트로 고정됨.
"""

import unittest

from ml.evaluation.precedents.compare_precedent_bm25_hybrid import (
    CorpusChunk,
    PrecedentBm25Index,
    RankedPrecedent,
    evaluate_ranking,
    weighted_rrf,
)


def whitespace_tokens(text):
    return tuple(text.casefold().split())


class ComparePrecedentBm25HybridTests(unittest.TestCase):
    def test_bm25_groups_chunks_by_precedent_and_keeps_best_chunk(self):
        index = PrecedentBm25Index(
            [
                CorpusChunk("a1", "A", "보증금 반환"),
                CorpusChunk("a2", "A", "임대차 종료"),
                CorpusChunk("b1", "B", "보증금 보증금 반환"),
                CorpusChunk("c1", "C", "매매 계약"),
            ],
            tokenizer=whitespace_tokens,
        )

        results = index.search("보증금 반환", top_k=2)

        self.assertEqual([result.precedent_id for result in results], ["B", "A"])
        self.assertEqual(results[0].best_chunk_id, "b1")
        self.assertGreater(results[0].score, results[1].score)

    def test_weighted_rrf_changes_order_with_source_weight(self):
        dense = [RankedPrecedent("A", 1, 0.9, "a")]
        bm25 = [RankedPrecedent("C", 1, 9.0, "c")]

        dense_heavy = weighted_rrf(
            dense,
            bm25,
            dense_weight=0.9,
            bm25_weight=0.1,
            top_k=3,
        )
        bm25_heavy = weighted_rrf(
            dense,
            bm25,
            dense_weight=0.1,
            bm25_weight=0.9,
            top_k=2,
        )

        self.assertEqual(dense_heavy[0].precedent_id, "A")
        self.assertEqual(bm25_heavy[0].precedent_id, "C")

    def test_evaluate_ranking_uses_graded_gold_at_top_k(self):
        ranking = [
            RankedPrecedent("A", 1, 1.0, "a"),
            RankedPrecedent("B", 2, 0.9, "b"),
            RankedPrecedent("X", 3, 0.8, "x"),
        ]

        metrics = evaluate_ranking(
            ranking,
            {"A": 3, "B": 2, "C": 2},
            top_k=3,
        )

        self.assertAlmostEqual(metrics["recall@3"], 2 / 3, places=6)
        self.assertEqual(metrics["mrr@3"], 1.0)
        self.assertGreater(metrics["ndcg@3"], 0.0)


if __name__ == "__main__":
    unittest.main()
