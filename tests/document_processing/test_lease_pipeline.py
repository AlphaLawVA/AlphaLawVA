# test_lease_pipeline.py
"""
Description: 표준임대차 재실행 파이프라인의 입력 제한 및 확인 목록을 검사한다.
Author: ooheunsu
Date: 2026-10-06
Before:
    - 합성자료용 재실행 파이프라인이 추가됨.
After:
    - 지원하지 않는 입력 차단과 누락 필드 보존을 검증함.
"""

import unittest

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

    def test_sale_rejected_before_file_access(self):
        with self.assertRaises(UnsupportedLeaseInput):
            run_replay(None, "sale")

    def test_wrong_page_order(self):
        document = {
            "text": "",
            "pages": [{"pageNumber": n, "lines": [{}]} for n in (2, 1, 3)],
        }
        with self.assertRaises(UnsupportedLeaseInput):
            validate_template(document)

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
