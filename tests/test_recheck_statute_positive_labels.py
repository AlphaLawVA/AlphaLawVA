# test_recheck_statute_positive_labels.py
"""
Description: 미재검토 양성 청크 선별과 독립 판정 병합을 테스트한다.
Author: ooheunsu
Date: 2026-09-02
Before:
    - 양성 라벨 전수 근거 재검토 모듈이 구현된 상태.

After:
    - 양성 후보 선별, 중복 방지 및 독립 라벨 적용을 자동 검증.
"""

import unittest

from ml.evaluation.recheck_statute_positive_labels import (
    apply_independent_labels,
    build_positive_targets,
    combine_decisions,
)


def case():
    return {
        "query_id": "q1",
        "question": "질문",
        "purpose": "목적",
        "required_concepts": ["요건"],
        "judgments": [
            {"chunk_id": "a", "relevance": 3, "reason": "기존"},
            {"chunk_id": "b", "relevance": 2, "reason": "기존"},
            {"chunk_id": "c", "relevance": 1, "reason": "기존"},
        ],
        "review": {"status": "draft"},
    }


def decision(chunk_id, score):
    return {
        "candidate_id": chunk_id.upper(),
        "chunk_id": chunk_id,
        "independent_relevance": score,
        "relevance": score,
        "majority_class_assessment": "not_applicable",
        "confidence": "high",
        "reason": "근거",
        "evidence_excerpt": "원문",
    }


class RecheckStatutePositiveLabelsTests(unittest.TestCase):
    def test_build_targets_selects_only_unreviewed_positive_chunks(self):
        chunks = {
            chunk_id: {
                "retrieval_text": "본문",
                "metadata": {
                    "law_name": "법",
                    "article_label": "제1조",
                    "article_title": "제목",
                },
            }
            for chunk_id in ("a", "b", "c")
        }
        existing = [{"query_id": "q1", "decisions": [decision("a", 3)]}]

        targets = build_positive_targets([case()], existing, chunks)

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["candidates"][0]["chunk_id"], "b")

    def test_combine_rejects_duplicate_chunk_decisions(self):
        rows = [{"query_id": "q1", "decisions": [decision("a", 3)]}]

        with self.assertRaisesRegex(ValueError, "중복"):
            combine_decisions(rows, rows)

    def test_apply_independent_labels_updates_reviewed_judgment(self):
        combined = [{"query_id": "q1", "decisions": [decision("a", 1)]}]

        revised = apply_independent_labels([case()], combined)

        self.assertEqual(revised[0]["judgments"][0]["relevance"], 1)
        self.assertEqual(revised[0]["judgments"][1]["relevance"], 2)
        self.assertEqual(revised[0]["review"]["status"], "draft")


if __name__ == "__main__":
    unittest.main()
