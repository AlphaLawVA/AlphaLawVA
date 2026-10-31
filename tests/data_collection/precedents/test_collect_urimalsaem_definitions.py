# test_collect_urimalsaem_definitions.py
"""
Description: 우리말샘 전용 정의 수집기의 표제어 정규화, 응답 해석,
다의어 보존과 법률 분야 검토 우선순위 계산을 검증한다.
Author: choeminju
Date: 2026-09-22
Before:
    - 우리말샘 정의를 별도 파일로 수집하는 코드가 구현된 상태.

After:
    - 외부 API 호출 없이 핵심 응답 변환과 상태 판정을 검증 가능.
"""

import unittest

from ml.data_collection.precedents.collect_urimalsaem_definitions import (
    build_result_row,
    extract_item_senses,
    extract_search_items,
    normalize_headword,
    normalize_sense,
)


class CollectUrimalsaemDefinitionsTest(unittest.TestCase):
    def test_normalize_headword_removes_dictionary_separators(self) -> None:
        self.assertEqual(normalize_headword("임차권 ^등기^명령"), "임차권등기명령")

    def test_extract_search_items_accepts_single_object(self) -> None:
        payload = {"channel": {"item": {"word": "근저당권", "sense": {}}}}
        self.assertEqual(len(extract_search_items(payload)), 1)

    def test_extract_item_senses_accepts_list_shape(self) -> None:
        item = {"sense": [{"target_code": "1"}, {"target_code": "2"}]}
        self.assertEqual(len(extract_item_senses(item)), 2)

    def test_normalize_sense_keeps_source_metadata(self) -> None:
        row = normalize_sense(
            {
                "word": "대항-력",
            },
            {
                "target_code": "123",
                "sense_no": "002",
                "definition": "법률상 권리를 주장할 수 있는 힘.",
                "pos": "명사",
                "cat": "법률",
                "type": "일반어",
                "origin": "對抗力",
                "link": "https://example.test/123",
            },
            "대항력",
        )
        self.assertEqual(row["match_type"], "exact")
        self.assertEqual(row["category"], "법률")
        self.assertTrue(row["is_legal_category"])
        self.assertEqual(row["review_priority"], 0)
        self.assertEqual(row["source_link"], "https://example.test/123")

    def test_build_result_row_preserves_multiple_exact_senses(self) -> None:
        candidate = {
            "match_key": "보증금",
            "term": "보증금",
            "total_case_count": 10,
        }
        items = [
            {
                "word": "보증금",
                "sense": [{
                    "target_code": "1",
                    "sense_no": "001",
                    "definition": "계약 이행을 보증하기 위해 맡기는 돈.",
                    "cat": "법률",
                }],
            },
            {
                "word": "보증금",
                "sense": [{
                    "target_code": "2",
                    "sense_no": "002",
                    "definition": "용기를 돌려받기 위해 제품값에 더하는 돈.",
                    "cat": "환경",
                }],
            },
        ]
        row = build_result_row(candidate, items, [])
        self.assertEqual(row["lookup_status"], "exact")
        self.assertEqual(row["definition_count"], 2)
        self.assertEqual(row["legal_category_definition_count"], 1)
        self.assertEqual(row["definitions"][0]["category"], "법률")

    def test_build_result_row_excludes_non_exact_headword(self) -> None:
        candidate = {"match_key": "지위승계", "term": "지위승계"}
        items = [
            {
                "word": "승계",
                "sense": [{"target_code": "1", "definition": "정의"}],
            }
        ]
        row = build_result_row(candidate, items, [])
        self.assertEqual(row["lookup_status"], "not_found")
        self.assertEqual(row["definition_count"], 0)

    def test_build_result_row_marks_request_error(self) -> None:
        candidate = {"match_key": "근저당권", "term": "근저당권"}
        row = build_result_row(
            candidate,
            [],
            [{"error_type": "request_or_parse_error", "error": "오류"}],
        )
        self.assertEqual(row["lookup_status"], "error")


if __name__ == "__main__":
    unittest.main()
