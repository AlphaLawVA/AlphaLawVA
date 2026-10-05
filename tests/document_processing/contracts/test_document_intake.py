# test_document_intake.py
"""
Description: 계약서 입력의 형식 검증, PDF 처리 경로 분류와 개인정보 비노출
직렬화 동작을 실제 외부 OCR 호출 없이 검증한다.
Author: ooheunsu
Date: 2026-09-20
Before:
    - 계약서 문서 입력 검사 모듈이 구현된 상태.
After:
    - 정상 문서와 위장·과대·암호화·혼합 PDF의 처리 정책이 테스트됨.
"""

import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter

from ml.document_processing.contracts.document_intake import (
    ContractDocumentInspection,
    DocumentIntakeError,
    DocumentKind,
    ProcessingRoute,
    inspect_contract_document,
)


class FakePage:
    def __init__(self, text):
        self.text = text

    def extract_text(self):
        return self.text


class FakeReader:
    def __init__(self, texts, *, encrypted=False):
        self.pages = [FakePage(text) for text in texts]
        self.is_encrypted = encrypted


class ContractDocumentIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def write(self, name, content):
        path = self.root / name
        path.write_bytes(content)
        return path

    def inspect_pdf(self, texts, **kwargs):
        path = self.write("contract.pdf", b"%PDF-1.7\nmock")
        return inspect_contract_document(
            path,
            pdf_reader_factory=lambda _path: FakeReader(texts),
            **kwargs,
        )

    def test_routes_text_pdf_without_exposing_text_by_default(self):
        result = self.inspect_pdf(["주택 임대차 계약서 보증금 지급일 임대인 임차인"])

        self.assertEqual(result.kind, DocumentKind.PDF)
        self.assertEqual(result.route, ProcessingRoute.DIRECT_TEXT)
        self.assertEqual(result.page_count, 1)
        self.assertNotIn("extracted_text", result.to_dict()["pages"][0])
        self.assertIn("계약서", result.to_dict(include_text=True)["pages"][0]["extracted_text"])

    def test_routes_scanned_and_mixed_pdf(self):
        scanned = self.inspect_pdf([""])
        mixed = self.inspect_pdf(
            ["주택 임대차 계약서 보증금 지급일 임대인 임차인", ""]
        )

        self.assertEqual(scanned.route, ProcessingRoute.OCR)
        self.assertIsNone(scanned.pages[0].extracted_text)
        self.assertEqual(mixed.route, ProcessingRoute.HYBRID)
        self.assertEqual(
            [page.route for page in mixed.pages],
            [ProcessingRoute.DIRECT_TEXT, ProcessingRoute.OCR],
        )

    def test_routes_supported_images_to_ocr(self):
        png = self.write("contract.png", b"\x89PNG\r\n\x1a\nmock")
        jpeg = self.write("contract.jpeg", b"\xff\xd8\xffmock")

        png_result = inspect_contract_document(png)
        jpeg_result = inspect_contract_document(jpeg)

        self.assertEqual(png_result.media_type, "image/png")
        self.assertEqual(jpeg_result.media_type, "image/jpeg")
        self.assertEqual(png_result.route, ProcessingRoute.OCR)

    def test_real_blank_pdf_is_routed_to_ocr(self):
        path = self.root / "blank-contract.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        with path.open("wb") as file:
            writer.write(file)

        result = inspect_contract_document(path)

        self.assertEqual(result.kind, DocumentKind.PDF)
        self.assertEqual(result.page_count, 1)
        self.assertEqual(result.route, ProcessingRoute.OCR)

    def test_rejects_extension_spoofing_and_unknown_content(self):
        spoofed = self.write("contract.pdf", b"\x89PNG\r\n\x1a\nmock")
        unknown = self.write("contract.jpg", b"not-an-image")

        with self.assertRaisesRegex(DocumentIntakeError, "확장자와 실제 형식"):
            inspect_contract_document(spoofed)
        with self.assertRaisesRegex(DocumentIntakeError, "파일 내용"):
            inspect_contract_document(unknown)

    def test_rejects_size_page_and_encryption_limits(self):
        oversized = self.write("contract.png", b"\x89PNG\r\n\x1a\nmock")
        pdf = self.write("contract.pdf", b"%PDF-1.7\nmock")

        with self.assertRaisesRegex(DocumentIntakeError, "크기 제한"):
            inspect_contract_document(oversized, max_file_size=4)
        with self.assertRaisesRegex(DocumentIntakeError, "페이지 수"):
            inspect_contract_document(
                pdf,
                max_pdf_pages=1,
                pdf_reader_factory=lambda _path: FakeReader(["text", "text"]),
            )
        with self.assertRaisesRegex(DocumentIntakeError, "암호화"):
            inspect_contract_document(
                pdf,
                pdf_reader_factory=lambda _path: FakeReader([], encrypted=True),
            )

    def test_result_has_stable_hash_and_does_not_store_source_path(self):
        first = self.inspect_pdf(["주택 임대차 계약서 보증금 지급일 임대인 임차인"])
        second = self.inspect_pdf(["주택 임대차 계약서 보증금 지급일 임대인 임차인"])

        self.assertIsInstance(first, ContractDocumentInspection)
        self.assertEqual(first.sha256, second.sha256)
        self.assertNotIn(str(self.root), str(first.to_dict()))


if __name__ == "__main__":
    unittest.main()
