# test_collect_korean_dictionary_definitions.py
"""
Description: 국립국어원 용어 정의 수집기의 표제어 정규화, 표본 추출,
응답 변환과 검토 우선순위 계산을 검증한다.
Author: choeminju
Date: 2026-09-21
Before:
    - 온용어와 표준국어대사전 정의를 함께 처리하는 수집기가 구현된 상태.

After:
    - 외부 API 호출 없이 핵심 데이터 변환과 순위 규칙을 검증 가능.
"""

import csv
import tempfile
import unittest
from pathlib import Path

from ml.data_collection.precedents.collect_korean_dictionary_definitions import (
    extract_onterm_results,
    extract_stdict_results,
    normalize_headword,
    normalize_onterm_definition,
    rank_definitions,
    select_sample_candidates,
    write_review_csv,
)


class CollectKoreanDictionaryDefinitionsTest(unittest.TestCase):
    def test_normalize_headword_removes_dictionary_separators(self) -> None:
        self.assertEqual(normalize_headword("근-저당^권"), "근저당권")

    def test_select_sample_keeps_required_terms(self) -> None:
        candidates = [
            {"match_key": f"용어{index}"} for index in range(20)
        ] + [{"match_key": "근저당권"}]
        selected = select_sample_candidates(candidates, 5, ["근저당권"])
        self.assertEqual(len(selected), 5)
        self.assertIn("근저당권", {row["match_key"] for row in selected})

    def test_extract_onterm_results_accepts_list_shape(self) -> None:
        payload = {
            "channel": {
                "returnCode": "1",
                "return_object": [
                    {"returnCode": 1, "resultlist": [{"word": "대항력"}]}
                ],
            }
        }
        self.assertEqual(extract_onterm_results(payload), [{"word": "대항력"}])

    def test_extract_stdict_results_accepts_empty_not_found_response(self) -> None:
        self.assertEqual(extract_stdict_results({}), [])

    def test_normalize_onterm_keeps_categories_and_license(self) -> None:
        row = normalize_onterm_definition(
            {
                "word": "근저당권",
                "definition": "정의",
                "category_main": "인문사회학",
                "category_sub": "법률",
                "source": "한국법제연구원",
                "glossary": "법령 용어 사례집",
                "kr_gvrn_lcns_ty": "4",
            },
            "근저당권",
        )
        self.assertEqual(row["match_type"], "exact")
        self.assertEqual(row["category_sub"], "법률")
        self.assertTrue(row["transform_restricted"])

    def test_rank_definitions_prioritizes_legal_real_estate_context(self) -> None:
        common = {
            "provider": "onterm",
            "lookup_term": "보증금",
            "headword": "보증금",
            "match_type": "exact",
            "category_main": "인문사회학",
            "source": "출처",
            "glossary": "용어집",
            "license_type": "1",
            "transform_restricted": False,
            "related_words": "",
            "usage_example": "",
        }
        military = {
            **common,
            "definition": "군수품 계약 이행을 위한 금전.",
            "category_sub": "군사",
        }
        legal = {
            **common,
            "definition": "임차인이 임대인에게 맡기는 임대차 보증금.",
            "category_sub": "법률",
        }
        ranked = rank_definitions([military, legal])
        self.assertEqual(ranked[0]["category_sub"], "법률")
        self.assertGreater(ranked[0]["relevance_score"], ranked[1]["relevance_score"])

    def test_write_review_csv_keeps_term_level_fields(self) -> None:
        rows = [
            {
                "term": "보증금",
                "total_case_count": 42,
                "lookup_status": "exact_onterm",
                "ranked_definitions": [
                    {
                        "rank": 1,
                        "definition": "임대차와 관련하여 맡기는 돈.",
                        "score_reasons": ["표제어 정확 일치"],
                    }
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.csv"
            write_review_csv(path, rows)
            with path.open(encoding="utf-8-sig", newline="") as file:
                result = next(csv.DictReader(file))

        self.assertEqual(result["term"], "보증금")
        self.assertEqual(result["total_case_count"], "42")
        self.assertEqual(result["lookup_status"], "exact_onterm")


if __name__ == "__main__":
    unittest.main()
