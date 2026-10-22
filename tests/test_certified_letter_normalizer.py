# test_certified_letter_normalizer.py
"""내용증명 내부 요청 정규화와 기본 기한 정책을 검증한다."""

import unittest
from datetime import date

from ml.agent.certified_letter.normalizer import (
    DEFAULT_RELATIVE_DEADLINE,
    SUGGESTED_DEADLINE_WARNING,
    normalize_request,
)
from ml.agent.certified_letter.state import (
    CertifiedLetterRequestData,
    ContractData,
    PartyData,
    PaymentData,
    PeriodData,
    PropertyData,
)


def make_request(*, requested_deadline: date | None) -> CertifiedLetterRequestData:
    return CertifiedLetterRequestData(
        purpose="  보증금   반환을 요청합니다. ",
        facts=" 첫 번째 사실입니다.  \r\n\r\n 두 번째   사실입니다. ",
        requested_deadline=requested_deadline,
        contract=ContractData(
            contract_type=" jeonse ",
            property=PropertyData(
                address=" 서울특별시  중구 세종대로 1 ",
                detail_address=" 101동   101호 ",
            ),
            period=PeriodData(
                start_date=date(2024, 10, 1),
                end_date=date(2026, 9, 30),
            ),
            payment=PaymentData(deposit=100_000_000),
            lessor=PartyData(
                name=" 홍길동 ",
                address=" 서울특별시  종로구 사직로 1 ",
                detail_address="   ",
            ),
            lessee=PartyData(
                name=" 김민지 ",
                address=" 서울특별시  중구 세종대로 1 ",
            ),
        ),
    )


class CertifiedLetterNormalizerTests(unittest.TestCase):
    def test_normalizes_text_and_preserves_paragraph_boundaries(self):
        normalized = normalize_request(
            make_request(requested_deadline=date(2026, 11, 15))
        )

        self.assertEqual(normalized.purpose, "보증금 반환을 요청합니다.")
        self.assertEqual(
            normalized.facts,
            "첫 번째 사실입니다.\n두 번째 사실입니다.",
        )
        self.assertEqual(normalized.contract.contract_type, "jeonse")
        self.assertEqual(
            normalized.contract.property.address,
            "서울특별시 중구 세종대로 1",
        )
        self.assertIsNone(normalized.contract.lessor.detail_address)
        self.assertEqual(normalized.deadline_text, "2026-11-15")
        self.assertFalse(normalized.is_deadline_suggested)
        self.assertEqual(normalized.warnings, ())

    def test_marks_default_relative_deadline_as_strategy_suggestion(self):
        normalized = normalize_request(make_request(requested_deadline=None))

        self.assertEqual(normalized.deadline_text, DEFAULT_RELATIVE_DEADLINE)
        self.assertTrue(normalized.is_deadline_suggested)
        self.assertEqual(normalized.warnings, (SUGGESTED_DEADLINE_WARNING,))


if __name__ == "__main__":
    unittest.main()
