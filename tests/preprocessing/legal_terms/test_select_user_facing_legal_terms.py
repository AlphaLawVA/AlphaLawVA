# test_select_user_facing_legal_terms.py
"""
Description: 사용자용 법률 용어 후보 선별에서 사용자 예시와 보수적 기본값이
정확한 우선순위로 적용되는지 검증한다.
Author: choeminju
Date: 2026-10-05
"""

from __future__ import annotations

import unittest

from ml.preprocessing.legal_terms.select_user_facing_legal_terms import (
    classify_row,
    classify_term,
    definition_warning,
    generation_status,
)


class UserFacingLegalTermSelectionTests(unittest.TestCase):
    """후보 선택 우선순위와 대표 경계 사례를 검증한다."""

    def test_user_selected_examples_are_kept(self) -> None:
        """사용자가 어려운 용어로 지정한 예시는 항상 보존한다."""
        for term in ("가압류", "각하", "대항력", "채권계약", "중가산금"):
            self.assertEqual(classify_term(term), ("selected", "사용자_선택_예시"))

    def test_user_excluded_examples_are_removed(self) -> None:
        """사용자가 쉬운 용어로 지정한 예시는 항상 제외한다."""
        for term in ("압류", "민법", "보증인", "법원", "혐의"):
            self.assertEqual(classify_term(term), ("excluded", "사용자_제외_예시"))

    def test_uncertain_specialized_term_is_preserved(self) -> None:
        """명시적으로 쉽다고 판단하지 못한 전문 용어는 후보에 남긴다."""
        self.assertEqual(
            classify_term("처분금지가처분"),
            ("selected", "애매하면_후보_보존"),
        )

    def test_statute_name_is_not_a_glossary_concept(self) -> None:
        """개별 법률명은 용어 설명 후보에서 제외한다."""
        self.assertEqual(
            classify_term("주택법"),
            ("excluded", "법률명_또는_고유명"),
        )

    def test_previously_excluded_row_stays_excluded(self) -> None:
        """앞 단계의 오탐·범위 밖 판정은 후보 보존 규칙으로 되살리지 않는다."""
        row = {"term": "임의전문용어", "include_in_glossary": False}
        self.assertEqual(
            classify_row(row),
            ("excluded", "기존_정제에서_오탐_또는_범위밖"),
        )

    def test_multi_definition_and_preserved_component_are_generation_ready(self) -> None:
        """다중 정의와 보존하기로 한 구성 용어는 정의별 생성 대상으로 본다."""
        self.assertEqual(
            generation_status({"review_status": "sense_selection_required"}, "selected"),
            "ready",
        )
        self.assertEqual(
            generation_status(
                {"review_status": "preserved_inactive_component"}, "selected"
            ),
            "ready",
        )

    def test_definition_review_term_is_ready_with_warning(self) -> None:
        """문맥 미검증 용어도 생성하되 내부 품질 경고를 남긴다."""
        row = {"review_status": "definition_review_required"}
        self.assertEqual(
            generation_status(row, "selected"),
            "ready_with_context_warning",
        )
        self.assertIn("실제 사용 문맥", definition_warning(row, "selected"))

    def test_regular_ready_term_has_no_warning(self) -> None:
        """일반 생성 가능 용어에는 불필요한 경고를 붙이지 않는다."""
        row = {"review_status": "ready"}
        self.assertEqual(definition_warning(row, "selected"), "")


if __name__ == "__main__":
    unittest.main()
