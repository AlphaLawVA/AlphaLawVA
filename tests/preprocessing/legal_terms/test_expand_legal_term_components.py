# test_expand_legal_term_components.py
"""
Description: 미검색 법률 복합 표현의 형태소 분해와 긴 구성 용어 우선 연결을
외부 API 없이 검증한다.
Author: choeminju
Date: 2026-09-27
Before:
    - 법률용어 구성 요소 확장 함수가 구현된 상태.

After:
    - 전체 표현 정의와 구성 요소 정의가 섞이지 않는지 회귀 검증 가능.
"""

import unittest

from kiwipiepy import Kiwi

from ml.preprocessing.legal_terms.expand_legal_term_components import (
    build_definition_index,
    build_expanded_rows,
    generate_component_candidates,
    morphology_components,
    select_longest_non_overlapping_components,
    select_lookup_candidates,
)


class ExpandLegalTermComponentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kiwi = Kiwi()

    def test_generates_nominal_tokens_and_compounds(self) -> None:
        candidates = morphology_components(
            self.kiwi,
            "강제집행신청",
            "강제집행신청",
        )

        self.assertIn("강제", candidates)
        self.assertIn("집행", candidates)
        self.assertIn("강제집행", candidates)
        self.assertIn("집행신청", candidates)
        self.assertNotIn("강제집행신청", candidates)

    def test_prefers_longest_non_overlapping_components(self) -> None:
        candidates = {
            "강제": {"morphology_token"},
            "집행": {"morphology_token"},
            "강제집행": {"morphology_ngram"},
            "신청": {"morphology_token"},
        }
        definitions = {
            key: {
                "term": key,
                "definitions": [{"definition": f"{key} 정의"}],
                "definition_source": "component_lookup",
            }
            for key in candidates
        }

        selected = select_longest_non_overlapping_components(
            "강제집행신청",
            candidates,
            definitions,
        )

        self.assertEqual(
            [row["match_key"] for row in selected],
            ["강제집행", "신청"],
        )

    def test_links_existing_exact_substring_without_new_lookup(self) -> None:
        integrated = [
            {
                "match_key": "강제집행신청",
                "term": "강제집행 신청",
                "urimalsaem_lookup_status": "not_found",
                "definitions": {"urimalsaem": []},
            },
            {
                "match_key": "강제집행",
                "term": "강제집행",
                "urimalsaem_lookup_status": "exact",
                "definitions": {
                    "urimalsaem": [{"definition": "강제집행 정의"}],
                },
            },
        ]

        candidates, by_parent = generate_component_candidates(
            integrated,
            self.kiwi,
        )
        candidate_keys = {row["match_key"] for row in candidates}
        definition_index = build_definition_index(integrated, [])
        expanded = build_expanded_rows(integrated, by_parent, definition_index)

        self.assertIn("강제집행", candidate_keys)
        self.assertEqual(
            expanded[0]["component_definition_status"],
            "components_available",
        )
        self.assertEqual(
            expanded[0]["component_definitions"][0]["match_key"],
            "강제집행",
        )
        self.assertEqual(expanded[0]["definitions"]["urimalsaem"], [])

    def test_component_definition_index_keeps_exact_rows_only(self) -> None:
        definitions = build_definition_index(
            [],
            [
                {
                    "match_key": "강제집행",
                    "term": "강제집행",
                    "lookup_status": "exact",
                    "definitions": [{"definition": "강제집행 정의"}],
                },
                {
                    "match_key": "집행신청",
                    "term": "집행신청",
                    "lookup_status": "not_found",
                    "definitions": [],
                },
            ],
        )

        self.assertIn("강제집행", definitions)
        self.assertNotIn("집행신청", definitions)

    def test_does_not_requery_previously_collected_component(self) -> None:
        candidates = [
            {"match_key": "강제집행"},
            {"match_key": "집행신청"},
        ]
        pending = select_lookup_candidates(
            candidates,
            [],
            [{"match_key": "강제집행", "lookup_status": "exact"}],
        )

        self.assertEqual(pending, [{"match_key": "집행신청"}])


if __name__ == "__main__":
    unittest.main()
