# test_prepare_statute_calibrated_human_review.py
"""
Description: 1차·보정 AI 판정 병합과 사람 집중 검수 선별을 검증한다.
Author: ooheunsu
Date: 2026-09-05
Before:
    - 두 AI 판정과 시드 근거의 병합 규칙이 정의된 상태.

After:
    - 충돌·저확신·품질 표본의 선별과 잠정 점수 적용이 검증됨.
"""

import unittest

from ml.evaluation.prepare_statute_calibrated_human_review import (
    merge_reviews,
    select_human_review,
)


def target(
    candidate: str,
    first: int,
    seed: int | None = None,
    confidence: str = "high",
    critical: bool = False,
) -> dict:
    return {
        "query_id": "statute_retrieval_v01_q012",
        "question": "질문",
        "purpose": "목적",
        "category": "direct_fact",
        "critical": critical,
        "candidate_id": candidate,
        "chunk_id": f"chunk-{candidate}",
        "law_name": "법령",
        "article_label": "제1조",
        "article_title": "제목",
        "retrieval_text": "본문",
        "seed_relevance": seed,
        "seed_reason": "",
        "ai_relevance": first,
        "ai_confidence": confidence,
        "ai_reason": "",
        "ai_evidence_excerpt": "",
        "selection_reasons": [],
        "human_relevance": None,
        "human_reason": "",
    }


def calibrated(items: list[tuple[str, int, str]]) -> list[dict]:
    return [
        {
            "query_id": "statute_retrieval_v01_q012",
            "assessments": [
                {
                    "candidate_id": candidate,
                    "chunk_id": f"chunk-{candidate}",
                    "relevance": score,
                    "confidence": confidence,
                    "reason": "근거",
                    "evidence_excerpt": "원문",
                }
                for candidate, score, confidence in items
            ],
        }
    ]


class PrepareCalibratedHumanReviewTests(unittest.TestCase):
    def test_merge_marks_threshold_seed_and_low_confidence(self):
        targets = [
            target("C01", 1),
            target("C02", 1, seed=3),
            target("C03", 0, confidence="low"),
            target("C04", 2),
        ]
        second = calibrated(
            [
                ("C01", 2, "high"),
                ("C02", 1, "high"),
                ("C03", 0, "high"),
                ("C04", 3, "high"),
            ]
        )

        rows = merge_reviews(targets, second)

        self.assertEqual(
            rows[0]["human_review_reasons"],
            ["first_second_threshold_disagreement"],
        )
        self.assertEqual(
            rows[1]["human_review_reasons"],
            ["seed_threshold_conflict_with_ai_consensus"],
        )
        self.assertEqual(
            rows[2]["human_review_reasons"],
            ["ai_low_confidence"],
        )
        self.assertEqual(rows[3]["human_review_tier"], "auto")
        self.assertEqual(rows[3]["provisional_relevance"], 3)

    def test_selection_adds_deterministic_controls(self):
        targets = [target(f"C{i:02d}", 0, critical=i % 2 == 0) for i in range(1, 21)]
        second = calibrated(
            [(row["candidate_id"], 0, "high") for row in targets]
        )
        merged = merge_reviews(targets, second)

        first, first_summary = select_human_review(merged, 0.10)
        second_run, second_summary = select_human_review(merged, 0.10)

        self.assertEqual(len(first), 2)
        self.assertTrue(
            all(row["human_review_tier"] == "control" for row in first)
        )
        self.assertEqual(
            [row["chunk_id"] for row in first],
            [row["chunk_id"] for row in second_run],
        )
        self.assertEqual(first_summary, second_summary)


if __name__ == "__main__":
    unittest.main()
