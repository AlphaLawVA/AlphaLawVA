# test_prepare_statute_final_review.py
"""
Description: 법령 검색 평가셋 최종 사람 검수 패키지의 추출과 검증을 테스트한다.
Author: ooheunsu
Date: 2026-09-03
Before:
    - 최종 사람 검수 패키지 생성 모듈이 구현된 상태.
After:
    - 정답, 라벨 변경 및 질문별 핵심 근거 검사가 자동 검증됨.
"""

import unittest

from ml.evaluation.prepare_statute_final_review import build_review_package


def make_case(score_a: int, score_b: int) -> dict:
    return {
        "query_id": "statute_retrieval_v01_q001",
        "question": "질문",
        "purpose": "목적",
        "category": "direct_fact",
        "legal_domain": "housing_lease",
        "difficulty": "easy",
        "critical": True,
        "required_concepts": ["요건"],
        "judgments": [
            {"chunk_id": "a", "relevance": score_a, "reason": "근거 A"},
            {"chunk_id": "b", "relevance": score_b, "reason": "근거 B"},
        ],
    }


def chunks() -> dict:
    return {
        chunk_id: {
            "retrieval_text": f"본문 {chunk_id}",
            "metadata": {
                "law_name": "법",
                "article_label": "제1조",
                "article_title": "제목",
            },
        }
        for chunk_id in ("a", "b")
    }


class PrepareStatuteFinalReviewTests(unittest.TestCase):
    def test_extracts_positive_and_changed_binary_labels(self):
        majority = [make_case(3, 2)]
        evidence = [make_case(3, 1)]

        package = build_review_package(majority, evidence, chunks())

        self.assertEqual(package["summary"]["question_count"], 1)
        self.assertEqual(package["summary"]["positive_count"], 1)
        self.assertEqual(package["summary"]["changed_binary_label_count"], 1)
        self.assertEqual(package["positive_judgments"][0]["chunk_id"], "a")
        self.assertEqual(package["changed_binary_labels"][0]["chunk_id"], "b")

    def test_marks_question_without_core_for_review(self):
        cases = [make_case(2, 1)]

        package = build_review_package(cases, cases, chunks())

        self.assertTrue(package["questions"][0]["needs_core_review"])
        self.assertEqual(package["summary"]["question_without_core_count"], 1)

    def test_rejects_question_without_positive_chunk(self):
        cases = [make_case(1, 0)]

        with self.assertRaisesRegex(ValueError, "정답 청크가 없는 질문"):
            build_review_package(cases, cases, chunks())


if __name__ == "__main__":
    unittest.main()
