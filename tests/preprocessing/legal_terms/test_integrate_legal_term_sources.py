# test_integrate_legal_term_sources.py
"""
Description: 판례·법령 법률용어 통합 시 표기 정규화, 출처 보존과
정의 상태 분리가 정확히 동작하는지 검증한다.
Author: choeminju
Date: 2026-09-27
Before:
    - 법률용어 통합 함수가 구현된 상태.

After:
    - 외부 API 없이 통합 스키마와 후속 조회 대상 선별을 검증 가능.
"""

import unittest

from ml.preprocessing.legal_terms.integrate_legal_term_sources import (
    build_integrated_rows,
    lookup_candidate,
    normalize_term,
)


class IntegrateLegalTermSourcesTest(unittest.TestCase):
    def test_normalize_term_removes_spacing_and_normalizes_width(self) -> None:
        self.assertEqual(normalize_term(" 소유권  이전 등기 "), "소유권이전등기")
        self.assertEqual(normalize_term("ＡＢＣ 권리"), "abc권리")

    def test_merges_same_term_and_keeps_definition_sources_separate(self) -> None:
        precedent = [{
            "match_key": "보증금",
            "term": "보증금",
            "official_variants": ["보증금"],
            "total_case_count": 12,
            "total_occurrence_count": 20,
            "field_stats": {"생성요약": {"case_count": 10}},
        }]
        statutes = [{
            "term": "보증 금",
            "document_count": 1,
            "article_count": 1,
            "occurrence_count": 2,
            "sources": [{
                "law_name": "주택임대차보호법",
                "article": "제3조",
                "source_id": "law:1:article:3",
                "occurrence_count": 2,
                "context": "보증금 반환",
            }],
            "extraction_method": ["legal_compound_rule"],
            "official_definitions": [{
                "law_name": "주택임대차보호법",
                "article": "제2조",
                "source_id": "law:1:article:2",
                "definition": "법령 원문 정의",
            }],
        }]
        dictionaries = [[{
            "match_key": "보증금",
            "lookup_status": "exact",
            "definitions": [{"definition": "우리말샘 정의"}],
        }]]

        rows = build_integrated_rows(precedent, statutes, dictionaries)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_domains"], ["precedent", "statute"])
        self.assertEqual(rows[0]["definition_status"], "urimalsaem_exact")
        self.assertEqual(
            rows[0]["definitions"]["urimalsaem"][0]["definition"],
            "우리말샘 정의",
        )
        self.assertEqual(
            rows[0]["definitions"]["statute_official"][0]["definition"],
            "법령 원문 정의",
        )

    def test_marks_new_statute_term_for_dictionary_lookup(self) -> None:
        rows = build_integrated_rows(
            [],
            [{
                "term": "임차권등기명령",
                "sources": [{
                    "law_name": "주택임대차보호법",
                    "article": "제3조의3",
                    "source_id": "law:1:article:3-3",
                    "occurrence_count": 1,
                    "context": "임차권등기명령을 신청할 수 있다.",
                }],
                "extraction_method": ["article_title"],
                "official_definitions": [],
            }],
            [],
        )

        self.assertEqual(rows[0]["definition_status"], "definition_pending")
        candidate = lookup_candidate(rows[0])
        self.assertEqual(candidate["match_key"], "임차권등기명령")
        self.assertEqual(candidate["document_count"], 1)

    def test_keeps_known_not_found_term_out_of_lookup_queue(self) -> None:
        rows = build_integrated_rows(
            [{"match_key": "지위승계", "term": "지위승계"}],
            [],
            [[{
                "match_key": "지위승계",
                "lookup_status": "not_found",
                "definitions": [],
            }]],
        )

        self.assertEqual(rows[0]["definition_status"], "urimalsaem_not_found")
        self.assertNotEqual(rows[0]["urimalsaem_lookup_status"], "not_queried")


if __name__ == "__main__":
    unittest.main()
