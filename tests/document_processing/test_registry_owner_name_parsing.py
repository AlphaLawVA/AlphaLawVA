# test_registry_owner_name_parsing.py
"""
Description: 등본 권리자 행에서 이름이 주민번호와 붙어 읽혀도 이름을 분리하는지 검증한다.
Author: ooheunsu
Date: 2026-11-04
Before:
    - OCR이 "소유자박도윤850101-*******"처럼 붙여 읽으면 소유자 이름을 읽지 못했음.
After:
    - 이름 뒤에 숫자·기호가 붙어도 앞의 글자 부분을 이름으로 읽는다.
"""

import unittest

from ml.document_processing.contracts.registry_document_ai_extractor import (
    _name_after_label,
)


class OwnerNameParsingTests(unittest.TestCase):
    def test_name_attached_to_resident_number(self) -> None:
        self.assertEqual(
            _name_after_label("소유자박도윤850101-*******", "소유자|공유자"),
            "박도윤",
        )

    def test_name_separated_by_space(self) -> None:
        self.assertEqual(
            _name_after_label("소유자김철수 850101-*******", "소유자|공유자"),
            "김철수",
        )

    def test_label_only_returns_none(self) -> None:
        self.assertIsNone(_name_after_label("소유자 850101-*******", "소유자|공유자"))


if __name__ == "__main__":
    unittest.main()
