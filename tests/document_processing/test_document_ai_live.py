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
from unittest.mock import patch

import requests
from pypdf import PdfWriter

from ml.document_processing.contracts.document_ai_live import (
    LiveOCRError,
    prepare,
    request_document,
    run_live,
)


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
            env_file=self.root / ".env",
        )
        self.options["env_file"].write_text(
            "GOOGLE_DOCUMENT_AI_ACCESS_TOKEN=fake-test-token\n", encoding="utf-8"
        )

    def run_case(self, **kwargs):
        return run_live(
            self.pdf, self.root / "cache", "jeonse", **(self.options | kwargs)
        )

    def test_default_dry_run_does_not_call_or_write(self):
        with patch("requests.Session") as session:
            plan = self.run_case()
        session.assert_not_called()
        self.assertEqual(plan["status"], "approval_required")
        self.assertEqual(plan["config"]["page_count"], 3)
        self.assertFalse((self.root / "cache").exists())

    def test_synthetic_declaration_required(self):
        with self.assertRaises(LiveOCRError):
            self.run_case(synthetic_only=False)

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
        self.options["env_file"].unlink()
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
        with patch(target, side_effect=LiveOCRError("failed")) as request:
            for _ in range(2):
                with self.assertRaises(LiveOCRError):
                    self.run_case(execute_approved=True, estimated_cost_usd=0.01)
            self.assertEqual(request.call_count, 1)
        attempt = next((self.root / "cache").glob("*/attempt.json"))
        self.assertNotIn("fake-test-token", attempt.read_text())

    def test_downstream_failure_preserves_ocr_and_reuses_cache(self):
        target = "ml.document_processing.contracts.document_ai_live."
        document = {"text": "test", "pages": [{}, {}, {}]}
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

    def test_transport_disables_redirects_and_keeps_coordinates(self):
        document = {"text": "test", "pages": [{}, {}, {}]}
        with patch("requests.Session") as factory:
            session = factory.return_value.__enter__.return_value
            session.post.return_value.status_code = 200
            session.post.return_value.json.return_value = {"document": document}
            actual, elapsed = request_document(
                "https://example.invalid", b"pdf", "fake", 30
            )
        self.assertEqual(actual, document)
        self.assertGreaterEqual(elapsed, 0)
        self.assertEqual(session.post.call_count, 1)
        kwargs = session.post.call_args.kwargs
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], (10, 30))
        self.assertEqual(kwargs["json"]["fieldMask"], "text,pages")

    def test_transport_error_does_not_leak_token_or_retry(self):
        with patch("requests.Session") as factory:
            session = factory.return_value.__enter__.return_value
            session.post.side_effect = requests.Timeout("secret-content")
            with self.assertRaises(LiveOCRError) as raised:
                request_document("https://example.invalid", b"pdf", "fake", 30)
        self.assertNotIn("secret-content", str(raised.exception))
        self.assertEqual(session.post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
