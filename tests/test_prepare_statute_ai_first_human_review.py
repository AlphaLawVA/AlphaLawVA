# test_prepare_statute_ai_first_human_review.py
"""
Description: 1차 AI 판정에서 사람 검수 대상을 선별하는 규칙을 검증한다.
Author: ooheunsu
Date: 2026-09-04
Before:
    - 시드 라벨과 블라인드 AI 판정의 선별 함수가 정의된 상태.

After:
    - 충돌, 저확신, 신규 양성과 통제표본 선별이 검증됨.
"""

import csv
import tempfile
import unittest
from pathlib import Path

from ml.evaluation.prepare_statute_ai_first_human_review import (
    build_candidate_rows,
    select_human_targets,
    write_csv,
)


def dataset():
    return [
        {
            "query_id": "q1",
            "judgments": [
                {"chunk_id": "chunk-1", "relevance": 3, "reason": "seed"},
                {"chunk_id": "chunk-2", "relevance": 1, "reason": "seed"},
            ],
        }
    ]


def blind_pool():
    candidates = []
    for index in range(1, 5):
        candidates.append(
            {
                "candidate_id": f"C{index:02d}",
                "chunk_id": f"chunk-{index}",
                "law_name": "법령",
                "article_label": f"제{index}조",
                "article_title": "제목",
                "retrieval_text": "본문",
            }
        )
    return [
        {
            "query_id": "q1",
            "question": "질문",
            "purpose": "목적",
            "category": "direct_fact",
            "critical": False,
            "candidates": candidates,
        }
    ]


def ai_review():
    scores = ((1, "high"), (2, "high"), (3, "high"), (0, "low"))
    return [
        {
            "query_id": "q1",
            "assessments": [
                {
                    "candidate_id": f"C{index:02d}",
                    "chunk_id": f"chunk-{index}",
                    "relevance": relevance,
                    "confidence": confidence,
                    "reason": "AI 근거",
                    "evidence_excerpt": "본문",
                }
                for index, (relevance, confidence) in enumerate(scores, start=1)
            ],
        }
    ]


class PrepareStatuteAiFirstHumanReviewTests(unittest.TestCase):
    def test_selects_disagreements_new_positive_and_low_confidence(self):
        rows = build_candidate_rows(dataset(), blind_pool(), ai_review())

        selected = select_human_targets(rows, control_rate=0)
        by_id = {row["candidate_id"]: row for row in selected}

        self.assertEqual(set(by_id), {"C01", "C02", "C03", "C04"})
        self.assertIn("seed_ai_threshold_disagreement", by_id["C01"]["selection_reasons"])
        self.assertIn("seed_ai_threshold_disagreement", by_id["C02"]["selection_reasons"])
        self.assertIn("unseeded_ai_positive", by_id["C03"]["selection_reasons"])
        self.assertIn("ai_low_confidence", by_id["C04"]["selection_reasons"])

    def test_high_confidence_negative_control_is_deterministic(self):
        review = ai_review()
        for assessment in review[0]["assessments"]:
            assessment["relevance"] = 0
            assessment["confidence"] = "high"
        rows = build_candidate_rows([], blind_pool(), review)

        first = select_human_targets(rows, control_rate=0.5, seed="test")
        second = select_human_targets(rows, control_rate=0.5, seed="test")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        self.assertTrue(
            all(
                "high_confidence_negative_control" in row["selection_reasons"]
                for row in first
            )
        )

    def test_rejects_ai_chunk_mismatch(self):
        review = ai_review()
        review[0]["assessments"][0]["chunk_id"] = "wrong"

        with self.assertRaisesRegex(ValueError, "chunk_id가 다릅니다"):
            build_candidate_rows(dataset(), blind_pool(), review)

    def test_human_csv_hides_seed_and_ai_labels(self):
        rows = build_candidate_rows(dataset(), blind_pool(), ai_review())
        selected = select_human_targets(rows, control_rate=0)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "review.csv"
            write_csv(path, selected)
            with path.open(encoding="utf-8-sig", newline="") as file:
                result = list(csv.DictReader(file))

        self.assertTrue(result)
        self.assertNotIn("seed_relevance", result[0])
        self.assertNotIn("ai_relevance", result[0])
        self.assertNotIn("selection_reasons", result[0])
        self.assertIn("human_relevance", result[0])


if __name__ == "__main__":
    unittest.main()
