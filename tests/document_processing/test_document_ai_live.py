# test_document_ai_live.py
"""
Description: 유료 호출 없이 OCR 승인, 입력 검사, 중복 호출 방지와 전송 옵션을 검증한다.
Author: ooheunsu
Date: 2026-10-06
Before:
    - 합성 PDF 전용 Google 실시간 연결 코드가 추가됨.
After:
    - 실제 PDF 파싱과 가짜 HTTP 응답으로 비용 및 오류 경계를 검증함.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from google.api_core.exceptions import Forbidden
from google.cloud import documentai_v1 as documentai
from pypdf import PdfWriter

from ml.document_processing.contracts.document_ai_live import (
    LiveOCRError,
    prepare,
    request_document,
    run_live,
)
from ml.document_processing.contracts.lease_pipeline import review_fields


class DocumentAILiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = self.root / "synthetic.pdf"
        writer = PdfWriter()
        for _ in range(3):
            writer.add_blank_page(width=595, height=842)
        writer.write(self.pdf)
        self.options = dict(
            project="test-project",
            location="us",
            processor="abc123",
            version="pretrained-ocr-v2.1-2024-08-07",
            synthetic_only=True,
        )

    def run_case(self, **kwargs):
        return run_live(
            self.pdf, self.root / "cache", "jeonse", **(self.options | kwargs)
        )

    def test_default_dry_run_does_not_call_or_write(self):
        target = "google.cloud.documentai_v1.DocumentProcessorServiceClient"
        with patch(target) as client:
            plan = self.run_case()
        client.assert_not_called()
        self.assertEqual(plan["status"], "approval_required")
        self.assertEqual(plan["config"]["page_count"], 3)
        self.assertFalse((self.root / "cache").exists())

    def test_synthetic_declaration_required(self):
        with self.assertRaises(LiveOCRError):
            self.run_case(synthetic_only=False)

    def test_sale_contract_type_is_available_for_dry_run(self):
        result = run_live(
            self.pdf,
            self.root / "cache",
            "sale",
            **self.options,
        )
        self.assertEqual(result["status"], "approval_required")

    def test_bad_version_and_location(self):
        for kwargs in ({"version": "stable"}, {"location": "us.evil.com"}):
            with self.assertRaises(LiveOCRError):
                self.run_case(**kwargs)

    def test_requires_three_actual_pdf_pages(self):
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.write(self.pdf)
        with self.assertRaises(LiveOCRError):
            self.run_case()

    def test_approval_requires_cost(self):
        with self.assertRaises(LiveOCRError):
            self.run_case(execute_approved=True)

    def test_missing_credentials_do_not_claim_attempt(self):
        target = "ml.document_processing.contracts.document_ai_live.load_adc_credentials"
        with patch(target, side_effect=LiveOCRError("missing ADC")):
            with self.assertRaises(LiveOCRError):
                self.run_case(execute_approved=True, estimated_cost_usd=0.01)
        self.assertFalse((self.root / "cache").exists())

    def test_request_is_versioned_and_input_changes_cache_key(self):
        args = {
            k: self.options[k] for k in ("project", "location", "processor", "version")
        }
        _, _, key, url = prepare(self.pdf, **args)
        self.assertIn("/processorVersions/pretrained-ocr-v2.1-2024-08-07:process", url)
        args["version"] = "pretrained-ocr-v2.0-2023-06-02"
        self.assertNotEqual(key, prepare(self.pdf, **args)[2])

    def test_timeout_blocks_second_attempt(self):
        target = "ml.document_processing.contracts.document_ai_live.request_document"
        auth = "ml.document_processing.contracts.document_ai_live.load_adc_credentials"
        with patch(auth, return_value=MagicMock(quota_project_id="test-project")):
            with patch(target, side_effect=LiveOCRError("failed")) as request:
                for _ in range(2):
                    with self.assertRaises(LiveOCRError):
                        self.run_case(execute_approved=True, estimated_cost_usd=0.01)
                self.assertEqual(request.call_count, 1)
        attempt = next((self.root / "cache").glob("*/attempt.json"))
        self.assertEqual(json.loads(attempt.read_text())["automatic_retries"], 0)

    def test_downstream_failure_preserves_ocr_and_reuses_cache(self):
        target = "ml.document_processing.contracts.document_ai_live."
        document = {"text": "test", "pages": [{}, {}, {}]}
        with patch(
            target + "load_adc_credentials",
            return_value=MagicMock(quota_project_id="test-project"),
        ):
            with patch(
                target + "request_document", return_value=(document, 1.25)
            ) as request:
                with patch(target + "run_replay", side_effect=ValueError("template")):
                    for _ in range(2):
                        with self.assertRaises(ValueError):
                            self.run_case(execute_approved=True, estimated_cost_usd=0.01)
                self.assertEqual(request.call_count, 1)
        plan = self.run_case()
        self.assertEqual(plan["request_count"], 0)
        document_path = Path(plan["cache_directory"]) / "document.json"
        document_path.write_text(json.dumps({"text": "tampered"}))
        with self.assertRaises(LiveOCRError):
            self.run_case()

    def test_official_client_disables_retries_and_keeps_coordinates(self):
        response = MagicMock(
            document=documentai.Document(
                text="test",
                pages=[documentai.Document.Page() for _ in range(3)],
            )
        )
        target = "google.cloud.documentai_v1.DocumentProcessorServiceClient"
        with patch(target) as factory:
            factory.return_value.process_document.return_value = response
            actual, elapsed = request_document("projects/test", "us", b"pdf", 30)
        self.assertEqual(actual["text"], "test")
        self.assertEqual(len(actual["pages"]), 3)
        self.assertGreaterEqual(elapsed, 0)
        kwargs = factory.return_value.process_document.call_args.kwargs
        self.assertIsNone(kwargs["retry"])
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(list(kwargs["request"].field_mask.paths), ["text", "pages"])

    def test_transport_error_does_not_leak_token_or_retry(self):
        target = "google.cloud.documentai_v1.DocumentProcessorServiceClient"
        with patch(target) as factory:
            factory.return_value.process_document.side_effect = OSError("secret-content")
            with self.assertRaises(LiveOCRError) as raised:
                request_document("projects/test", "us", b"pdf", 30)
        self.assertNotIn("secret-content", str(raised.exception))
        self.assertEqual(factory.return_value.process_document.call_count, 1)

    def test_http_error_preserves_status_without_response_body(self):
        target = "google.cloud.documentai_v1.DocumentProcessorServiceClient"
        with patch(target) as factory:
            factory.return_value.process_document.side_effect = Forbidden(
                "secret-provider-response"
            )
            with self.assertRaisesRegex(LiveOCRError, "FORBIDDEN") as raised:
                request_document("projects/test", "us", b"pdf", 30)
        self.assertNotIn("secret-provider-response", str(raised.exception))
        self.assertEqual(factory.return_value.process_document.call_count, 1)

    def test_ambiguous_empty_field_has_confirmation_reason(self):
        items = list(
            review_fields(
                {
                    "disclosures": {
                        "tax_arrears": {
                            "value": None,
                            "status": "ambiguous",
                            "raw_text": "미납 국세·지방세 없음 있음",
                        }
                    }
                }
            )
        )
        self.assertEqual(items[0]["reason"], "confirm_ambiguous_value")


if __name__ == "__main__":
    unittest.main()
