# test_privacy.py
"""
Description: 계약서 OCR 문자열에서 주민등록번호가 저장 전 완전히
마스킹되는지 검증한다.
Author: ooheunsu
Date: 2026-10-05
Before:
    - OCR 개인정보 마스킹 유틸리티가 구현된 상태.
After:
    - 하이픈·공백 유무와 주변 문자를 포함한 주민번호 차단이 회귀 테스트됨.
"""

import unittest

from ml.document_processing.contracts.privacy import (
    MASKED_RESIDENT_NUMBER,
    contains_resident_registration_number,
    mask_resident_registration_numbers,
    redact_resident_number_rows,
)


class PrivacyMaskingTests(unittest.TestCase):
    def test_masks_resident_registration_number_before_follow_up_processing(self):
        original = "임대인 900101-1234567, 임차인 920405 2345678"

        masked = mask_resident_registration_numbers(original)

        self.assertEqual(masked.count(MASKED_RESIDENT_NUMBER), 2)
        self.assertFalse(contains_resident_registration_number(masked))
        self.assertNotIn("1234567", masked)
        self.assertNotIn("2345678", masked)

    def test_does_not_mask_unrelated_contract_numbers(self):
        original = "보증금 250000000원, 계약일 2026-10-05"

        self.assertEqual(mask_resident_registration_numbers(original), original)

    def test_redacts_misrecognized_value_on_resident_number_row(self):
        lines = [
            {"text": "주민등록번호", "box": [100, 200, 260, 240]},
            {"text": "000000-[000003", "box": [300, 202, 520, 242]},
            {"text": "전화번호", "box": [100, 300, 240, 340]},
        ]

        redacted = redact_resident_number_rows(lines)

        self.assertEqual(redacted[0]["text"], MASKED_RESIDENT_NUMBER)
        self.assertEqual(redacted[1]["text"], MASKED_RESIDENT_NUMBER)
        self.assertEqual(redacted[2]["text"], "전화번호")


if __name__ == "__main__":
    unittest.main()
