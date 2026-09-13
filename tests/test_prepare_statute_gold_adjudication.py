# test_prepare_statute_gold_adjudication.py
"""
Description: 법령 판정 병합과 근거 재검토 입력 검증 규칙을 테스트한다.
Author: ooheunsu
Date: 2026-09-02
Before:
    - 법령 골드셋 준비 모듈이 구현된 상태.

After:
    - 다수결 병합과 재검토 제약의 회귀 오류를 자동 검출.
"""

import unittest

from ml.evaluation.prepare_statute_gold_adjudication import (
    QuestionDecision,
    exact_majority,
    merge_reviews,
    tokenize,
    validate_decisions,
)


def blind_pool():
    return [
        {
            "query_id": "q1",
            "question": "질문",
            "purpose": "목적",
            "category": "direct_fact",
            "critical": True,
            "candidates": [
                {
                    "candidate_id": "C01",
                    "chunk_id": "law:000001:article:0001",
                    "law_name": "법",
                    "article_label": "제1조",
                    "article_title": "제목",
                    "retrieval_text": "본문",
                },
                {
                    "candidate_id": "C02",
                    "chunk_id": "law:000001:article:0002",
                    "law_name": "법",
                    "article_label": "제2조",
                    "article_title": "제목",
                    "retrieval_text": "본문",
                },
            ],
        }
    ]


class PrepareStatuteGoldAdjudicationTests(unittest.TestCase):
    def test_exact_majority_requires_two_matching_scores(self):
        self.assertEqual(exact_majority([3, 3, 1]), 3)
        self.assertIsNone(exact_majority([3, 2, 1]))
        self.assertIsNone(exact_majority([3, 2]))

    def test_merge_uses_binary_majority_and_flags_exact_grade_conflict(self):
        human = [
            {"query_id": "q1", "candidate_id": "C01", "relevance": 3},
            {"query_id": "q1", "candidate_id": "C02", "relevance": 0},
        ]
        ai = [
            {
                "query_id": "q1",
                "assessments": [
                    {"candidate_id": "C01", "relevance": 0},
                    {"candidate_id": "C02", "relevance": 1},
                ],
            }
        ]
        team = [
            {"query_id": "q1", "candidate_id": "C01", "relevance": 2}
        ]
        selection = [
            {
                "query_id": "q1",
                "candidate_id": "C01",
                "selection_reasons": ["positive_threshold_disagreement"],
            }
        ]

        result = merge_reviews(blind_pool(), human, ai, team, selection)

        self.assertTrue(result[0]["provisional_binary_relevant"])
        self.assertIsNone(result[0]["provisional_grade"])
        self.assertIn("no_exact_grade_majority", result[0]["adjudication_reasons"])
        self.assertFalse(result[1]["provisional_binary_relevant"])
        self.assertIsNone(result[1]["provisional_grade"])

    def test_tokenize_keeps_korean_legal_terms(self):
        self.assertEqual(tokenize("주택의 인도, 주민등록!"), ["주택의", "인도", "주민등록"])

    def test_validate_decisions_rejects_score_outside_majority_class(self):
        target = {
            "query_id": "q1",
            "candidates": [
                {
                    "candidate_id": "C01",
                    "allowed_relevance": [2, 3],
                }
            ],
        }
        decision = QuestionDecision.model_validate(
            {
                "query_id": "q1",
                "decisions": [
                    {
                        "candidate_id": "C01",
                        "independent_relevance": 1,
                        "relevance": 1,
                        "majority_class_assessment": "unsupported",
                        "confidence": "high",
                        "reason": "이유",
                        "evidence_excerpt": "근거",
                    }
                ],
            }
        )

        with self.assertRaisesRegex(ValueError, "허용 범위 밖"):
            validate_decisions(target, decision)


if __name__ == "__main__":
    unittest.main()
