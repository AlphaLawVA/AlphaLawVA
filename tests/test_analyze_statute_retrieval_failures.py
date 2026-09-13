# test_analyze_statute_retrieval_failures.py
"""
Description: 법령 검색 실패의 공통·모델별·순위 지연 분류를 테스트한다.
Author: ooheunsu
Date: 2026-09-02
Before:
    - 법령 검색 실패 분석 모듈이 구현된 상태.

After:
    - 실패 원인 분류와 라벨 민감도 집계의 회귀 오류를 자동 검출.
"""

import unittest

from ml.evaluation.analyze_statute_retrieval_failures import (
    analyze,
    analyze_policy,
)


def ranking(*chunk_ids):
    return [
        {"rank": rank, "chunk_id": chunk_id}
        for rank, chunk_id in enumerate(chunk_ids, start=1)
    ]


class AnalyzeStatuteRetrievalFailuresTests(unittest.TestCase):
    def setUp(self):
        self.metadata = {
            chunk_id: {
                "law_name": "법",
                "article_label": "제1조",
                "article_title": "제목",
            }
            for chunk_id in ("a", "b", "c", "d")
        }

    def test_analyze_policy_separates_shared_and_model_specific_misses(self):
        labels = {"q1": {"a": 3, "b": 2, "c": 2}}
        rankings = {
            "m1": {"q1": ranking("a", "d", "d2", "d3", "d4", "b")},
            "m2": {"q1": ranking("b", "d", "d2", "d3", "d4", "d5")},
        }
        cases = {"q1": {"question": "질문", "critical": True}}

        result = analyze_policy(labels, rankings, cases, self.metadata)
        question = result["questions"][0]

        self.assertEqual(
            [row["chunk_id"] for row in question["shared_missing"]], ["c"]
        )
        self.assertEqual(
            [
                row["chunk_id"]
                for row in question["models"]["m1"]["model_specific_missing"]
            ],
            [],
        )
        self.assertEqual(
            [
                row["chunk_id"]
                for row in question["models"]["m2"]["model_specific_missing"]
            ],
            ["a"],
        )
        self.assertEqual(
            question["models"]["m1"]["retrieved_at_6_to_10"][0]["chunk_id"],
            "b",
        )

    def test_analyze_counts_binary_label_changes(self):
        cases = [
            {
                "query_id": "q1",
                "question": "질문",
                "critical": False,
                "judgments": [
                    {"chunk_id": "a", "relevance": 2},
                    {"chunk_id": "b", "relevance": 3},
                ],
            }
        ]
        adjudication = [
            {
                "query_id": "q1",
                "decisions": [
                    {"chunk_id": "a", "independent_relevance": 1}
                ],
            }
        ]
        ten = ranking("a", "b", "d", "d2", "d3", "d4", "d5", "d6", "d7", "d8")
        rankings = {"m1": {"q1": ten}, "m2": {"q1": ten}}

        result = analyze(cases, adjudication, rankings, self.metadata)

        self.assertEqual(result["label_sensitive_positive_count"], 1)
        self.assertEqual(
            result["label_sensitive_positive_chunks"][0]["chunk_id"], "a"
        )
        self.assertEqual(result["positive_not_evidence_rechecked_count"], 1)
        self.assertEqual(
            result["positive_not_evidence_rechecked"][0]["chunk_id"], "b"
        )


if __name__ == "__main__":
    unittest.main()
