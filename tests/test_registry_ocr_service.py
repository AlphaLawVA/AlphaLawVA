# test_registry_ocr_service.py
"""
Description: 등기사항증명서 OCR 초안 서비스의 동의·형식 검증과 구조화 응답을 확인한다.
외부 호출 없이 주입된 Document AI 응답으로 서비스 경계만 검증한다.
Author: ooheunsu
Date: 2026-11-04
Before:
    - 등기사항증명서를 업로드해 OCR 초안을 받는 서비스 테스트가 없는 상태.
After:
    - 비활성·미동의·형식 오류와 정상 구조화 응답이 회귀 테스트됨.
"""

from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from pypdf import PdfWriter

from api.services.contract_ocr_service import (
    ContractOcrError,
    ContractOcrUnavailableError,
    DocumentAiOcrSettings,
)
from api.services.registry_ocr_service import RegistryOcrService
from tests.document_processing.test_registry_document_ai_extractor_layout import (
    document_ai_json,
    first_page,
    second_page,
)

SETTINGS = DocumentAiOcrSettings(
    project="test-project",
    location="us",
    processor="processor",
    version="pretrained-ocr-v2.1-2024-08-07",
    timeout_seconds=30,
)


def blank_pdf(pages: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def registry_document():
    # 제목 줄을 첫 페이지에 포함해 텍스트 인덱스가 어긋나지 않게 한다.
    title = ("등기사항전부증명서(현재 유효사항)", 0.32, 0.14, 0.77)
    return document_ai_json([[title, *first_page()], second_page()])


class RegistryOcrServiceTests(unittest.TestCase):
    def test_is_unavailable_without_settings(self):
        with self.assertRaises(ContractOcrUnavailableError):
            RegistryOcrService(None).create_draft(
                filename="r.pdf", content=blank_pdf(), provider_consent=True
            )

    def test_requires_provider_consent_and_pdf(self):
        service = RegistryOcrService(SETTINGS)

        with self.assertRaisesRegex(ContractOcrError, "동의"):
            service.create_draft(filename="r.pdf", content=blank_pdf(), provider_consent=False)
        with self.assertRaisesRegex(ContractOcrError, "PDF"):
            service.create_draft(filename="r.png", content=b"x", provider_consent=True)

    def test_returns_structured_draft_that_needs_review(self):
        service = RegistryOcrService(SETTINGS)

        with (
            patch("api.services.registry_ocr_service.load_adc_credentials", return_value=None),
            patch(
                "api.services.registry_ocr_service.request_document",
                return_value=(registry_document(), 1.0),
            ) as request,
        ):
            draft = service.create_draft(
                filename="registry.pdf", content=blank_pdf(2), provider_consent=True
            )

        self.assertIsNone(request.call_args.kwargs["expected_pages"])
        self.assertEqual(draft.status, "needs_review")
        self.assertEqual(draft.page_count, 2)
        self.assertEqual(draft.extraction.owners[0].name.value, "홍길동")
        self.assertTrue(draft.warnings)

    def test_rejects_documents_that_are_not_registry_certificates(self):
        service = RegistryOcrService(SETTINGS)
        document = registry_document()
        document["text"] = "임대차계약서"

        with (
            patch("api.services.registry_ocr_service.load_adc_credentials", return_value=None),
            patch(
                "api.services.registry_ocr_service.request_document",
                return_value=(document, 1.0),
            ),
            self.assertRaisesRegex(ContractOcrError, "등기사항증명서로 인식"),
        ):
            service.create_draft(
                filename="registry.pdf", content=blank_pdf(2), provider_consent=True
            )


if __name__ == "__main__":
    unittest.main()
