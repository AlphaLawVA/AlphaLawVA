# test_compare_precedent_rerankers.py
"""
Description: 판례 리랭커 비교기의 리뷰 풀 중복 제거와 순위 품질 지표를
외부 모델 호출 없이 검증한다.
Author: choeminju
Date: 2026-10-18
Before:
    - Dense 및 리랭커 순위 비교 함수와 1~3점 평가 규칙이 정의된 상태.
After:
    - 공정한 후보 통합과 nDCG, Precision, MRR 계산이 자동 검증된다.
"""

import unittest
from dataclasses import replace

from ml.evaluation.precedents.compare_precedent_rerankers import (
    build_review_pool,
    deduplicate_case_results,
    ranking_metrics,
)
from ml.rag.precedent_retriever import PrecedentSearchResult


def ranking_row(method: str, rank: int, chunk_id: str, text: str) -> dict:
    return {
        "query_id": "q01",
        "scenario_id": "s01",
        "variant": "A",
        "query": "보증금을 받지 못했어요.",
        "method": method,
        "rank": rank,
        "candidate_key": "q01:100",
        "precedent_id": "100",
        "case_number": "2026다100",
        "case_name": "보증금반환",
        "court_name": "대법원",
        "decision_date": "2026-01-01",
        "ranking_score": 0.9,
        "best_chunk_id": chunk_id,
        "best_chunk_type": "이유",
        "best_chunk_text": text,
    }


class ComparePrecedentRerankersTests(unittest.TestCase):
    def test_candidate_cases_are_deduplicated_by_case_number(self):
        base = PrecedentSearchResult(
            rank=1,
            precedent_id="100",
            distance=0.1,
            similarity=0.9,
            text="",
            case_no="2024다321973",
            case_no_list=None,
            case_name="보증금반환",
            court_name="대법원",
            decision_date="2026-01-01",
            decision_year=2026,
            case_type="민사",
            judgment_type="판결",
            source_path=None,
            matched_chunks=[],
            metadata={},
        )
        duplicate = replace(base, rank=2, precedent_id="101")
        other = replace(
            base,
            rank=3,
            precedent_id="102",
            case_no="2025다100",
        )

        results = deduplicate_case_results([base, duplicate, other], limit=2)

        self.assertEqual([result.precedent_id for result in results], ["100", "102"])

    def test_review_pool_deduplicates_case_and_preserves_unique_evidence(self):
        rankings = [
            ranking_row("dense", 1, "chunk-a", "첫 번째 근거"),
            ranking_row("bge", 1, "chunk-a", "첫 번째 근거"),
            ranking_row("gte", 1, "chunk-b", "두 번째 근거"),
        ]

        pool = build_review_pool(rankings, review_top_k=1)

        self.assertEqual(len(pool), 1)
        self.assertIn("첫 번째 근거", pool[0]["evidence_texts"])
        self.assertIn("두 번째 근거", pool[0]["evidence_texts"])

    def test_ranking_metrics_use_pooled_ideal_scores(self):
        metrics = ranking_metrics(
            [2, 3, 1, 1, 1],
            ideal_candidates=[3, 3, 2, 1, 1, 1],
        )

        self.assertAlmostEqual(metrics["precision@3"], 2 / 3)
        self.assertEqual(metrics["mrr@5"], 0.5)
        self.assertAlmostEqual(metrics["mean_top3_score"], 2.0)
        self.assertGreater(metrics["ndcg@5"], 0)
        self.assertLess(metrics["ndcg@5"], 1)


if __name__ == "__main__":
    unittest.main()
