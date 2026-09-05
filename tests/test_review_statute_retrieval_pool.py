"""Tests for blind AI review validation and cross-review sampling."""

import unittest
from unittest.mock import patch

from ml.evaluation.review_statute_retrieval_pool import (
    CandidateAssessment,
    QuestionAssessment,
    compare_reviews,
    review_case_with_validation_retry,
    select_team_review,
    validate_assessment,
    validate_blind_pool,
)


def blind_case(critical=False):
    return {
        "query_id": "q1",
        "question": "질문",
        "purpose": "목적",
        "category": "direct_fact",
        "critical": critical,
        "candidates": [
            {
                "candidate_id": "C01",
                "chunk_id": "chunk-1",
                "law_name": "법령",
                "article_label": "제1조",
                "article_title": "목적",
                "retrieval_text": "본문",
                "relevance": None,
                "reason": "",
            },
            {
                "candidate_id": "C02",
                "chunk_id": "chunk-2",
                "law_name": "법령",
                "article_label": "제2조",
                "article_title": "정의",
                "retrieval_text": "본문2",
                "relevance": None,
                "reason": "",
            },
        ],
    }


def assessment(candidate_id, relevance, confidence="high"):
    return CandidateAssessment(
        candidate_id=candidate_id,
        relevance=relevance,
        confidence=confidence,
        reason="근거",
        evidence_excerpt="본문",
    )


class ReviewStatuteRetrievalPoolTests(unittest.TestCase):
    def test_blind_pool_rejects_existing_labels(self):
        case = blind_case()
        case["candidates"][0]["relevance"] = 3
        with self.assertRaisesRegex(ValueError, "기존 관련도"):
            validate_blind_pool([case])

    def test_assessment_requires_all_candidates_without_extras(self):
        case = blind_case()
        result = QuestionAssessment(
            query_id="q1",
            assessments=[assessment("C01", 3)],
        )
        with self.assertRaisesRegex(ValueError, "후보 불일치"):
            validate_assessment(case, result)

    def test_comparison_tracks_exact_and_binary_agreement(self):
        cases = [blind_case()]
        human = [
            {"query_id": "q1", "candidate_id": "C01", "relevance": 2},
            {"query_id": "q1", "candidate_id": "C02", "relevance": 0},
        ]
        ai = [
            QuestionAssessment(
                query_id="q1",
                assessments=[
                    assessment("C01", 3),
                    assessment("C02", 2),
                ],
            ).model_dump()
        ]

        result = compare_reviews(cases, human, ai)

        self.assertFalse(result[0]["exact_agreement"])
        self.assertTrue(result[0]["binary_agreement"])
        self.assertFalse(result[1]["binary_agreement"])

    def test_selection_includes_threshold_disagreements_and_controls(self):
        comparisons = [
            {
                "query_id": "q1",
                "candidate_id": "C01",
                "critical": True,
                "human_relevance": 3,
                "ai_relevance": 1,
                "ai_confidence": "high",
                "exact_agreement": False,
                "binary_agreement": False,
            },
            {
                "query_id": "q1",
                "candidate_id": "C02",
                "critical": True,
                "human_relevance": 0,
                "ai_relevance": 0,
                "ai_confidence": "low",
                "exact_agreement": True,
                "binary_agreement": True,
            },
            {
                "query_id": "q1",
                "candidate_id": "C03",
                "critical": True,
                "human_relevance": 2,
                "ai_relevance": 2,
                "ai_confidence": "high",
                "exact_agreement": True,
                "binary_agreement": True,
            },
        ]

        selected = select_team_review(comparisons, agreement_rate=0, seed="test")
        by_id = {row["candidate_id"]: row for row in selected}

        self.assertIn("C01", by_id)
        self.assertIn("C02", by_id)
        self.assertIn("C03", by_id)
        self.assertIn(
            "critical_query_control_sample",
            by_id["C03"]["selection_reasons"],
        )

    def test_validation_retry_retries_value_error(self):
        with patch(
            "ml.evaluation.review_statute_retrieval_pool.review_case",
            side_effect=[
                ValueError("invalid structure"),
                ({"query_id": "q1"}, {"total_tokens": 1}),
            ],
        ) as mocked_review:
            result = review_case_with_validation_retry(
                object(),
                {"query_id": "q1"},
                "model",
                attempts=2,
            )

        self.assertEqual(mocked_review.call_count, 2)
        self.assertEqual(result[0]["query_id"], "q1")


if __name__ == "__main__":
    unittest.main()
