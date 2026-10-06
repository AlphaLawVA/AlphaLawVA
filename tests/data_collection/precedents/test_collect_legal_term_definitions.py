# test_collect_legal_term_definitions.py
"""
Description: 공식 법률용어 정의 응답의 행 변환, HTML 정리와 동일 정의 병합을 검증한다.
Author: choeminju
Date: 2026-09-21
Before:
    - 정제 용어 후보의 공식 정의 수집기 검증이 필요한 상태.

After:
    - 단일·복수 상세 응답과 용어별 정의 상태를 단위 테스트로 확인 가능.
"""

import unittest

from ml.data_collection.precedents.collect_legal_term_definitions import (
    DefinitionNotFoundError,
    build_definition_row,
    clean_definition_text,
    extract_service_entries,
    merge_identical_definitions,
    normalize_definition_entry,
)


class CollectLegalTermDefinitionsTest(unittest.TestCase):
    def test_extract_service_entries_accepts_single_values(self) -> None:
        payload = {
            "LsTrmService": {
                "법령용어일련번호": "1",
                "법령용어명_한글": "근저당권",
                "법령용어코드": "011402",
                "법령용어정의": "정의",
            }
        }
        self.assertEqual(
            extract_service_entries(payload)[0]["법령용어명_한글"],
            "근저당권",
        )

    def test_extract_service_entries_transposes_column_lists(self) -> None:
        payload = {
            "LsTrmService": {
                "법령용어일련번호": ["1", "2"],
                "법령용어명_한글": ["근저당권", "대항력"],
                "법령용어코드": ["011402", "011402"],
            }
        }
        rows = extract_service_entries(payload)
        self.assertEqual([row["법령용어일련번호"] for row in rows], ["1", "2"])

    def test_extract_service_entries_rejects_not_found_response(self) -> None:
        with self.assertRaisesRegex(DefinitionNotFoundError, "일치하는"):
            extract_service_entries({"Law": "일치하는 법령용어가 없습니다."})

    def test_clean_definition_text_removes_html_and_preserves_breaks(self) -> None:
        self.assertEqual(
            clean_definition_text("첫 문장<br>  둘째&nbsp;문장 "),
            "첫 문장\n둘째 문장",
        )

    def test_normalize_definition_entry_preserves_source(self) -> None:
        entry = normalize_definition_entry(
            {
                "법령용어일련번호": "1",
                "법령용어명_한글": "대항력",
                "법령용어명_한자": "對抗力",
                "법령용어코드": "011402",
                "법령용어코드명": "법령정의사전",
                "법령용어정의": "정의",
                "출처": "주택임대차보호법",
            }
        )
        self.assertEqual(entry["source_term_id"], "1")
        self.assertEqual(entry["source"], "주택임대차보호법")

    def test_merge_identical_definitions_keeps_all_ids_and_sources(self) -> None:
        entries = [
            {
                "source_term_id": "1",
                "term_korean": "대항력",
                "term_hanja": "",
                "dictionary_type_code": "011402",
                "dictionary_type_name": "법령정의사전",
                "definition": "같은 정의",
                "source": "법률 A",
            },
            {
                "source_term_id": "2",
                "term_korean": "대항력",
                "term_hanja": "",
                "dictionary_type_code": "011402",
                "dictionary_type_name": "법령정의사전",
                "definition": "같은 정의",
                "source": "법률 B",
            },
        ]
        rows = merge_identical_definitions(entries)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_term_ids"], ["1", "2"])
        self.assertEqual(rows[0]["sources"], ["법률 A", "법률 B"])

    def test_build_definition_row_marks_non_definition_dictionary(self) -> None:
        candidate = {
            "match_key": "사건",
            "term": "사건",
            "official_variants": ["사건"],
            "source_term_ids": ["1"],
            "dictionary_type_codes": ["011403"],
        }
        row = build_definition_row(candidate, [], [])
        self.assertEqual(row["definition_status"], "no_official_definition_type")
        self.assertEqual(row["definition_count"], 0)

    def test_build_definition_row_keeps_defined_status_with_not_found_id(self) -> None:
        candidate = {
            "match_key": "사건",
            "term": "사건",
            "official_variants": ["사건"],
            "source_term_ids": ["1", "2"],
            "dictionary_type_codes": ["011402", "011403"],
        }
        entry = {
            "source_term_id": "1",
            "term_korean": "사건",
            "term_hanja": "",
            "dictionary_type_code": "011402",
            "dictionary_type_name": "법령정의사전",
            "definition": "공식 정의",
            "source": "법률 A",
        }
        row = build_definition_row(
            candidate,
            [entry],
            [
                {
                    "requested_source_term_ids": "2",
                    "error_type": "not_found",
                    "error": "일치하는 법령용어가 없습니다.",
                }
            ],
        )
        self.assertEqual(row["definition_status"], "defined")
        self.assertEqual(row["definition_count"], 1)


if __name__ == "__main__":
    unittest.main()
