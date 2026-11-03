# test_contract_ocr_service.py
"""
Description: 계약서 OCR 초안 서비스의 동의·비활성화 차단과 임시 파일 폐기를 검증한다.
외부 Google 호출은 모킹해 유료 요청 없이 업로드 경계를 확인한다.
Author: ooheunsu
Date: 2026-11-02
Before:
    - 프런트 업로드 OCR의 파일 보관 및 provider 호출 경계를 검증할 테스트가 없음.
After:
    - OCR 초안은 동의와 설정이 있어야만 생성되고 임시 원본이 남지 않음을 검증함.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from api.models.contract_snapshots import ContractType
from api.services.contract_ocr_service import (
    ContractOcrError,
    ContractOcrService,
    ContractOcrUnavailableError,
    DocumentAiOcrSettings,
)
from ml.document_processing.contracts.extraction_schema import ContractExtraction


def extraction() -> ContractExtraction:
    source = {
        "page_number": 1,
        "bounding_box": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.2, "y_max": 0.2},
    }
    return ContractExtraction.model_validate(
        {
            "contract_type": {"value": "jeonse", "status": "extracted", "source": source},
            "contract_date": {"value": "2026-11-02", "status": "extracted", "source": source},
            "start_date": {"value": "2026-12-01", "status": "extracted", "source": source},
            "end_date": {"value": "2028-11-30", "status": "extracted", "source": source},
            "property": {
                "address": {"value": "서울특별시 테스트구", "status": "extracted", "source": source},
                "detail_address": {"value": None, "status": "missing"},
            },
            "parties": [
                {"role": "lessor", "name": {"value": "임대인", "status": "extracted", "source": source}},
                {"role": "lessee", "name": {"value": "임차인", "status": "extracted", "source": source}},
            ],
            "payment": {
                "deposit": {"value": 100000000, "status": "extracted", "source": source},
            },
        }
    )


class ContractOcrServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ContractOcrService(
            DocumentAiOcrSettings(
                project="test-project",
                location="us",
                processor="processor123",
                version="pretrained-ocr-v2.1-2024-08-07",
                timeout_seconds=30,
            )
        )

    def test_disabled_service_rejects_before_reading_upload(self) -> None:
        with self.assertRaises(ContractOcrUnavailableError):
            ContractOcrService(None).create_draft(
                filename="contract.pdf",
                content=b"pdf",
                contract_type=ContractType.JEONSE,
                provider_consent=True,
            )

    def test_provider_consent_is_required(self) -> None:
        with self.assertRaisesRegex(ContractOcrError, "동의"):
            self.service.create_draft(
                filename="contract.pdf",
                content=b"pdf",
                contract_type=ContractType.JEONSE,
                provider_consent=False,
            )

    @patch("api.services.contract_ocr_service.extract_document_ai", return_value=extraction())
    @patch("api.services.contract_ocr_service.validate_template")
    @patch("api.services.contract_ocr_service.request_document", return_value=({"text": "", "pages": []}, 0.1))
    @patch("api.services.contract_ocr_service.load_adc_credentials", return_value=MagicMock())
    @patch("api.services.contract_ocr_service.prepare")
    def test_returns_draft_and_removes_temporary_upload(
        self,
        prepare: MagicMock,
        _credentials: MagicMock,
        _request: MagicMock,
        _template: MagicMock,
        _extract: MagicMock,
    ) -> None:
        captured_path: Path | None = None

        def prepared(path: Path, *_args: object) -> tuple[bytes, dict[str, str], str, str]:
            nonlocal captured_path
            captured_path = path
            self.assertTrue(path.is_file())
            return b"pdf", {"resource": "projects/test"}, "cache-key", "url"

        prepare.side_effect = prepared
        draft = self.service.create_draft(
            filename="contract.pdf",
            content=b"%PDF-test",
            contract_type=ContractType.JEONSE,
            provider_consent=True,
        )

        self.assertEqual(draft.status, "needs_review")
        self.assertEqual(draft.provider, "Google Document AI")
        self.assertIsNotNone(captured_path)
        self.assertFalse(captured_path.exists())


if __name__ == "__main__":
    unittest.main()
