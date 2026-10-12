# test_filter_legal_term_noise.py
"""
Description: 판례 법률용어 노이즈 필터가 명확한 조각만 제외하는지 검증한다.
Author: choeminju
Date: 2026-09-21
"""

import unittest

from ml.preprocessing.precedents.filter_legal_term_noise import (
    classify_noise,
    filter_candidate_rows,
    filter_case_match_rows,
)


class FilterLegalTermNoiseTest(unittest.TestCase):
    def test_classifies_high_confidence_noise(self) -> None:
        self.assertIn("definition_boilerplate", classify_noise("라 한다)"))
        self.assertIn("unbalanced_delimiter", classify_noise("라 한다)"))
        self.assertEqual(classify_noise("2년"), ["numeric_duration"])
        self.assertEqual(classify_noise("유효한"), ["grammatical_fragment"])
        self.assertEqual(classify_noise("사업자 등"), ["enumeration_fragment"])

    def test_preserves_meaningful_legal_terms(self) -> None:
        for term in (
            "근저당권설정",
            "등기말소",
            "선량한 관리자의 주의",
            "입주자대표회의",
            "지급기한",
            "평등",
        ):
            self.assertEqual(classify_noise(term), [], term)

    def test_duplicate_match_key_keeps_first_row(self) -> None:
        rows = [
            {"match_key": "보증금", "term": "보증금"},
            {"match_key": "보증금", "term": "보증 금"},
        ]
        kept, excluded = filter_candidate_rows(rows)
        self.assertEqual(kept, [rows[0]])
        self.assertEqual(excluded[0]["noise_reasons"], ["duplicate_match_key"])

    def test_filters_case_matches_and_removes_empty_fields(self) -> None:
        rows = [
            {
                "precedent_id": "1",
                "matched_fields": {
                    "생성요약": [
                        {"match_key": "보증금", "term": "보증금"},
                        {"match_key": "유효한", "term": "유효한"},
                    ],
                    "주문": [{"match_key": "본다", "term": "본다"}],
                },
            }
        ]
        filtered = filter_case_match_rows(rows, {"유효한", "본다"})
        self.assertEqual(
            filtered[0]["matched_fields"],
            {"생성요약": [{"match_key": "보증금", "term": "보증금"}]},
        )


if __name__ == "__main__":
    unittest.main()
