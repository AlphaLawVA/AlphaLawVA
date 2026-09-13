# test_review_statute_retrieval_calibrated.py
"""
Description: 파일럿 경계 기준을 반영한 보정 AI 검수 준비를 검증한다.
Author: ooheunsu
Date: 2026-09-04
Before:
    - 보정 검수 대상 생성과 응답 검증 함수가 정의된 상태.

After:
    - 대상 범위, 판정 형식과 체크포인트 동작이 검증됨.
"""

import json
import unittest

from ml.evaluation.review_statute_retrieval_calibrated import (
    build_review_input,
    select_review_cases,
    select_target_cases,
)


class ReviewStatuteRetrievalCalibratedTests(unittest.TestCase):
    def test_selects_borderline_non_high_and_seed_disagreement(self):
        dataset = [
            {
                "query_id": "statute_retrieval_v01_q012",
                "required_concepts": ["요건"],
                "judgments": [
                    {"chunk_id": "chunk-3", "relevance": 3},
                ],
            }
        ]
        candidates = [
            {
                "candidate_id": f"C0{number}",
                "chunk_id": f"chunk-{number}",
                "law_name": "법령",
                "article_label": "제1조",
                "article_title": "제목",
                "retrieval_text": "본문",
            }
            for number in range(1, 5)
        ]
        blind = [
            {
                "query_id": "statute_retrieval_v01_q012",
                "question": "질문",
                "purpose": "목적",
                "category": "direct_fact",
                "critical": False,
                "candidates": candidates,
            }
        ]
        first = [
            {
                "query_id": "statute_retrieval_v01_q012",
                "assessments": [
                    {
                        "candidate_id": "C01",
                        "relevance": 0,
                        "confidence": "high",
                    },
                    {
                        "candidate_id": "C02",
                        "relevance": 1,
                        "confidence": "high",
                    },
                    {
                        "candidate_id": "C03",
                        "relevance": 1,
                        "confidence": "high",
                    },
                    {
                        "candidate_id": "C04",
                        "relevance": 0,
                        "confidence": "medium",
                    },
                ],
            }
        ]

        result = select_review_cases(dataset, blind, first, 12, 12)

        self.assertEqual(
            [item["candidate_id"] for item in result[0]["candidates"]],
            ["C02", "C03", "C04"],
        )

    def test_review_input_does_not_reveal_prior_labels(self):
        case = {
            "query_id": "statute_retrieval_v01_q012",
            "question": "질문",
            "purpose": "목적",
            "required_concepts": ["요건"],
            "candidates": [
                {
                    "candidate_id": "C01",
                    "chunk_id": "chunk-1",
                    "law_name": "법령",
                    "article_label": "제1조",
                    "article_title": "제목",
                    "retrieval_text": "본문",
                    "relevance": None,
                    "reason": "",
                }
            ],
        }

        payload = json.loads(build_review_input(case).split("\n", 1)[1])

        self.assertNotIn("chunk_id", payload["candidates"][0])
        self.assertNotIn("relevance", payload["candidates"][0])
        self.assertNotIn("confidence", payload["candidates"][0])

    def test_select_target_cases_hides_review_metadata(self):
        dataset = [
            {
                "query_id": "statute_retrieval_v01_q012",
                "question": "질문",
                "purpose": "목적",
                "category": "direct_fact",
                "critical": False,
                "required_concepts": ["요건"],
            }
        ]
        targets = [
            {
                "query_id": "statute_retrieval_v01_q012",
                "candidate_id": "C01",
                "chunk_id": "chunk-1",
                "law_name": "법령",
                "article_label": "제1조",
                "article_title": "제목",
                "retrieval_text": "본문",
                "seed_relevance": 3,
                "ai_relevance": 1,
                "selection_reasons": ["seed_ai_threshold_disagreement"],
            }
        ]

        result = select_target_cases(dataset, targets, 12, 50)

        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["candidates"]), 1)
        candidate = result[0]["candidates"][0]
        self.assertNotIn("seed_relevance", candidate)
        self.assertNotIn("ai_relevance", candidate)
        self.assertNotIn("selection_reasons", candidate)


if __name__ == "__main__":
    unittest.main()
