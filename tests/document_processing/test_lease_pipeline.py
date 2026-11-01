# test_lease_pipeline.py
"""
Description: 표준 임대차·매매 재실행 파이프라인의 입력 제한 및 확인 목록을 검사한다.
Author: ooheunsu
Date: 2026-10-06
Before:
    - 합성자료용 재실행 파이프라인이 추가됨.
After:
    - 지원하지 않는 입력 차단과 누락 필드 보존을 검증함.
"""

import unittest
from unittest.mock import patch

from ml.document_processing.contracts.document_ai_extractor import Line

from ml.document_processing.contracts.lease_pipeline import (
    UnsupportedLeaseInput,
    review_fields,
    run_replay,
    validate_template,
)


class LeasePipelineTests(unittest.TestCase):
    def test_missing_structure(self):
        for document in (None, {}, {"text": "", "pages": []}):
            with self.assertRaises(UnsupportedLeaseInput):
                validate_template(document)

    def test_unknown_contract_type_rejected_before_file_access(self):
        with self.assertRaises(UnsupportedLeaseInput):
            run_replay(None, "unknown")

    def test_wrong_page_order(self):
        document = {
            "text": "",
            "pages": [{"pageNumber": n, "lines": [{}]} for n in (2, 1, 3)],
        }
        with self.assertRaises(UnsupportedLeaseInput):
            validate_template(document)

    def test_sale_template_uses_sale_anchors(self):
        document = {
            "text": "sale",
            "pages": [
                {"pageNumber": number, "lines": [{}]} for number in range(1, 4)
            ],
        }
        lines = [
            Line("부동산매매계약서 제1조 제2조", 1, (0.1, 0.1, 0.9, 0.2)),
            Line("제3조 제6조", 2, (0.1, 0.1, 0.9, 0.2)),
            Line("제7조 매도인 매수인", 3, (0.1, 0.1, 0.9, 0.2)),
        ]
        target = "ml.document_processing.contracts.lease_pipeline.load_lines"
        with patch(target, return_value=lines):
            result = validate_template(document, "sale")
        self.assertEqual(result["template"], "court-general-sale-three-page")

    def test_review_includes_missing_and_nonmissing(self):
        result = list(
            review_fields(
                {
                    "a": {"value": None, "status": "missing"},
                    "b": {"value": 123, "status": "low_confidence"},
                }
            )
        )
        self.assertEqual([item["path"] for item in result], ["a", "b"])
        self.assertEqual(result[0]["reason"], "missing")


if __name__ == "__main__":
    unittest.main()
