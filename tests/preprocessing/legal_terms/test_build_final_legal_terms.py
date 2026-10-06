# test_build_final_legal_terms.py
"""
Description: 최종 법률 용어 통합에서 정의 정규화, 중복 차단과 표본 구성이
원 정의를 손실하지 않고 동작하는지 검증한다.
Author: choeminju
Date: 2026-10-05
"""

from __future__ import annotations

import unittest

from ml.preprocessing.legal_terms.build_final_legal_terms import (
    merge_rows,
    normalize_definition,
)


class FinalLegalTermBuildTests(unittest.TestCase):
    """최종 용어 통합의 핵심 불변 조건을 검증한다."""

    def test_normalize_definition_preserves_source_metadata(self) -> None:
        """정의 본문과 우리말샘 식별자·출처를 보존한다."""
        source = {
            "provider": "urimalsaem",
            "lookup_term": "가압류",
            "headword": "가압류",
            "target_code": "123",
            "sense_no": "001",
            "definition": "원 정의",
            "category": "법률",
            "source_link": "https://example.com/123",
        }
        normalized = normalize_definition(source)
        self.assertEqual(normalized["definition_id"], "123")
        self.assertEqual(normalized["source_definition"], "원 정의")
        self.assertEqual(normalized["source_link"], "https://example.com/123")

    def test_merge_rows_rejects_duplicate_match_key(self) -> None:
        """서로 다른 후보군의 중복 용어를 조용히 덮어쓰지 않는다."""
        with self.assertRaisesRegex(ValueError, "중복 매치키"):
            merge_rows([{"match_key": "중복"}], [{"match_key": "중복"}])

    def test_merge_rows_sorts_unique_rows(self) -> None:
        """중복이 없으면 매치키 순으로 안정적으로 통합한다."""
        merged = merge_rows([{"match_key": "나"}], [{"match_key": "가"}])
        self.assertEqual([row["match_key"] for row in merged], ["가", "나"])


if __name__ == "__main__":
    unittest.main()
