# test_validate_legal_term_morphology.py
"""
Description: 판례 법률용어 형태소 검증의 활용형 오탐 제거와 명사성 용례
유지 동작을 검증한다.
Author: choeminju
Date: 2026-09-22
"""

import unittest

from kiwipiepy import Kiwi

from ml.preprocessing.precedents.validate_legal_term_morphology import (
    validate_match_span,
)


class ValidateLegalTermMorphologyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kiwi = Kiwi()

    def validate_text_span(self, text: str, matched_text: str):
        start = text.index(matched_text)
        end = start + len(matched_text)
        return validate_match_span(list(self.kiwi.tokenize(text)), start, end)

    def test_rejects_verb_stem_inside_conjugation(self) -> None:
        is_valid, reason, morphemes = self.validate_text_span(
            "계약에 대하여 판단했다.",
            "대하",
        )
        self.assertFalse(is_valid)
        self.assertEqual(reason, "non_nominal_morpheme")
        self.assertEqual(morphemes[0]["tag"], "VV")

    def test_keeps_same_surface_when_used_as_noun(self) -> None:
        is_valid, reason, _ = self.validate_text_span(
            "대하를 양식했다.",
            "대하",
        )
        self.assertTrue(is_valid)
        self.assertIsNone(reason)

    def test_keeps_legal_noun_before_person_suffix(self) -> None:
        is_valid, reason, _ = self.validate_text_span(
            "매수인이 잔금을 지급했다.",
            "매수",
        )
        self.assertTrue(is_valid)
        self.assertIsNone(reason)

    def test_keeps_compound_legal_noun(self) -> None:
        is_valid, reason, morphemes = self.validate_text_span(
            "소유권이전등기를 마쳤다.",
            "소유권이전등기",
        )
        self.assertTrue(is_valid)
        self.assertIsNone(reason)
        self.assertEqual("".join(row["form"] for row in morphemes), "소유권이전등기")

    def test_keeps_phrase_with_adjective_and_noun(self) -> None:
        is_valid, reason, _ = self.validate_text_span(
            "특별한 사정이 없으면 계약이 유지된다.",
            "특별한 사정",
        )
        self.assertTrue(is_valid)
        self.assertIsNone(reason)

    def test_keeps_statute_name_with_particles(self) -> None:
        is_valid, reason, _ = self.validate_text_span(
            "주택공급에 관한 규칙을 적용했다.",
            "주택공급에 관한 규칙",
        )
        self.assertTrue(is_valid)
        self.assertIsNone(reason)

    def test_rejects_expression_ending_with_particle(self) -> None:
        is_valid, reason, _ = self.validate_text_span(
            "당사자가 고의로 처분했다.",
            "고의로",
        )
        self.assertFalse(is_valid)
        self.assertEqual(reason, "non_nominal_morpheme")


if __name__ == "__main__":
    unittest.main()
