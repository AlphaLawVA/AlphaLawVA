# test_finalize_statute_human_review.py
"""
Description: 사람 최종 검수 결정 반영과 승인 조건을 테스트한다.
Author: ooheunsu
Date: 2026-09-04
Before:
    - 법령 검색 평가셋 사람 최종 승인 모듈이 구현된 상태.
After:
    - 질문 수정, 점수 확정 및 승인 차단 조건이 자동 검증됨.
"""

import unittest

from ml.evaluation.finalize_statute_human_review import apply_human_decisions


def case() -> dict:
    return {
        "query_id": "q1",
        "question": "기존 질문",
        "judgments": [
            {"chunk_id": "a", "relevance": 3, "reason": "핵심"},
            {"chunk_id": "b", "relevance": 1, "reason": "참고"},
        ],
        "review": {"status": "draft"},
    }


def decisions() -> dict:
    return {
        "review_status": "completed",
        "reviewer": "reviewer",
        "reviewed_at": "2026-09-04",
        "review_scope": {
            "question_count": 11,
            "positive_judgment_count": 38,
            "changed_binary_label_count": 42,
        },
        "question_resolutions": [
            {"query_id": "q1", "final_question": "수정 질문", "reason": "수정"}
        ],
        "judgment_resolutions": [
            {
                "query_id": "q1",
                "chunk_id": "b",
                "final_relevance": 2,
                "reason": "보조 근거",
            }
        ],
    }


class FinalizeStatuteHumanReviewTests(unittest.TestCase):
    def test_applies_resolutions_and_approves_case(self):
        approved, summary = apply_human_decisions([case()], decisions())

        self.assertEqual(approved[0]["question"], "수정 질문")
        self.assertEqual(approved[0]["judgments"][1]["relevance"], 2)
        self.assertEqual(approved[0]["review"]["status"], "approved")
        self.assertEqual(summary["positive_count"], 2)
        self.assertEqual(len(summary["changed_scores"]), 1)

    def test_rejects_incomplete_review_scope(self):
        review = decisions()
        review["review_scope"]["question_count"] = 10

        with self.assertRaisesRegex(ValueError, "검수 범위"):
            apply_human_decisions([case()], review)

    def test_rejects_question_without_core_evidence(self):
        item = case()
        item["judgments"][0]["relevance"] = 2

        with self.assertRaisesRegex(ValueError, "핵심 근거"):
            apply_human_decisions([item], decisions())


if __name__ == "__main__":
    unittest.main()
