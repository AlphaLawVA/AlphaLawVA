# test_review_legal_category_terms.py
"""
Description: 법률 분야 정의 용어 검토가 명백한 쉬운 말과 오탐만 제외하고,
애매한 용어와 복수 정의 용어를 보존하는지 검증한다.
Author: choeminju
Date: 2026-10-05
Before:
    - 법률 분야 정의 용어 검토 규칙이 구현된 상태.
After:
    - 보수적 포함 정책과 쉬운 정의 생성 준비 조건을 단위 테스트로 확인 가능.
"""

import unittest

from ml.preprocessing.legal_terms.review_legal_category_terms import (
    build_component_parent_index,
    build_review_rows,
    classify_term,
    validate_review_rows,
)


def definition(text: str = "정의", *, legal: bool = True) -> dict:
    return {
        "definition": text,
        "is_legal_category": legal,
        "category": "법률" if legal else "",
    }


class ReviewLegalCategoryTermsTest(unittest.TestCase):
    def test_excludes_only_explicit_easy_and_false_terms(self) -> None:
        easy = classify_term("계약", "full_term", 1, [])
        false_match = classify_term("주지", "full_term", 1, [])
        ambiguous = classify_term("대항력", "full_term", 1, [])

        self.assertEqual(easy[:3], ("exclude_easy_general", False, False))
        self.assertEqual(false_match[:3], ("exclude_false_match", False, False))
        self.assertEqual(ambiguous[:3], ("ready", True, True))

    def test_preserves_multiple_definitions_for_sense_selection(self) -> None:
        result = classify_term("과실", "component_term", 2, ["중대한과실"])
        self.assertEqual(result[:3], ("sense_selection_required", True, False))

    def test_preserves_unlinked_component_as_inactive(self) -> None:
        result = classify_term("근저당", "component_term", 1, [])
        self.assertEqual(
            result[:3],
            ("preserved_inactive_component", True, False),
        )

    def test_builds_parent_index_from_selected_components(self) -> None:
        index = build_component_parent_index(
            [
                {
                    "match_key": "임대차보증금반환채권",
                    "component_definitions": [
                        {"match_key": "임대차"},
                        {"match_key": "보증금반환채권"},
                    ],
                }
            ]
        )
        self.assertEqual(index["임대차"], ["임대차보증금반환채권"])

    def test_build_review_rows_uses_only_legal_definitions(self) -> None:
        full_rows = [
            {
                "match_key": "대항력",
                "term": "대항력",
                "definitions": {
                    "urimalsaem": [definition(), definition("일반 뜻", legal=False)]
                },
            }
        ]
        component_rows = [
            {
                "match_key": "임대차",
                "term": "임대차",
                "definitions": [definition()],
            }
        ]
        rows = build_review_rows(
            full_rows,
            component_rows,
            {"임대차": ["임대차보증금반환채권"]},
        )
        by_key = {(row["source_type"], row["match_key"]): row for row in rows}

        self.assertEqual(by_key[("full_term", "대항력")]["legal_definition_count"], 1)
        self.assertEqual(by_key[("full_term", "대항력")]["review_status"], "ready")
        self.assertEqual(
            by_key[("component_term", "임대차")]["review_status"],
            "context_ready",
        )
        validate_review_rows(rows)


if __name__ == "__main__":
    unittest.main()
