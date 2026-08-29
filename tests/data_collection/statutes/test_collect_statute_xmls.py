# test_collect_statute_xmls.py
"""
Description: 법령 XML 수집기의 선정 목록 검증, 현행법령 확정 및
XML 식별정보 검증 동작을 테스트한다.
Author: ooheunsu
Date: 2026-08-17
Before:
    - XML 수집 코드가 있지만 주요 입력 및 응답 검증 동작이 확인되지 않은 상태.

After:
    - XML 수집기의 핵심 검증 동작을 자동 테스트로 확인 가능.
"""

import sys
import tempfile
import unittest
from pathlib import Path

STATUTE_MODULE_DIR = (
    Path(__file__).resolve().parents[3] / "ml/data_collection/statutes"
)
sys.path.insert(0, str(STATUTE_MODULE_DIR))

from collect_statute_xmls import (  # noqa: E402
    find_current_law,
    load_selected_laws,
    validate_xml,
)


class CollectStatuteXmlsTest(unittest.TestCase):
    def test_load_selected_laws_checks_declared_total(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selection.json"
            path.write_text(
                '{"total": 1, "laws": '
                '[{"law_id": "001248", "law_name": "주택임대차보호법"}]}',
                encoding="utf-8",
            )

            laws = load_selected_laws(path)

        self.assertEqual(len(laws), 1)

    def test_find_current_law_uses_exact_name_and_id(self) -> None:
        payload = {
            "LawSearch": {
                "law": [
                    {
                        "법령명한글": "주택임대차보호법",
                        "법령ID": "001248",
                        "법령일련번호": "276291",
                        "시행일자": "20260102",
                    },
                    {
                        "법령명한글": "주택임대차보호법 시행령",
                        "법령ID": "004950",
                        "법령일련번호": "287183",
                        "시행일자": "20260701",
                    },
                ]
            }
        }

        result = find_current_law(payload, "001248", "주택임대차보호법")

        self.assertEqual(result["법령일련번호"], "276291")

    def test_validate_xml_checks_law_identity(self) -> None:
        xml_bytes = """
        <법령>
          <기본정보>
            <법령ID>001248</법령ID>
            <법령명_한글>주택임대차보호법</법령명_한글>
          </기본정보>
        </법령>
        """.encode()

        validate_xml(xml_bytes, "001248", "주택임대차보호법")


if __name__ == "__main__":
    unittest.main()
