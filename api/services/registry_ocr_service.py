# registry_ocr_service.py
"""
Description: 업로드된 등기사항증명서 PDF를 Google Document AI로 일회성 인식하고
사용자 확인 전 구조화 초안을 반환한다. 원본 파일과 OCR 원문은 영속 저장하지 않는다.
Author: ooheunsu
Date: 2026-11-04
Before:
    - 등기사항증명서 추출기는 저장된 Document AI JSON만 입력받을 수 있음.
After:
    - FastAPI가 명시 동의한 등본 PDF를 메모리·임시파일 범위에서 인식하고 구조화함.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from time import perf_counter

from api.models.registry_ocr import RegistryOcrDraftResponse
from api.services.contract_ocr_service import (
    MAX_UPLOAD_BYTES,
    SUPPORTED_SUFFIXES,
    ContractOcrError,
    ContractOcrUnavailableError,
    DocumentAiOcrSettings,
    load_document_ai_settings_from_env,
)
from ml.document_processing.contracts.document_ai_live import (
    LiveOCRError,
    load_adc_credentials,
    request_document,
)
from ml.document_processing.contracts.document_intake import (
    DocumentIntakeError,
    DocumentKind,
    inspect_contract_document,
)
from ml.document_processing.contracts.registry_document_ai_extractor import (
    extract_registry_document_ai,
)

MAX_REGISTRY_PAGES = 15  # Document AI 동기 처리 한도를 넘지 않도록 제한한다.


class RegistryOcrService:
    def __init__(self, settings: DocumentAiOcrSettings | None) -> None:
        self._settings = settings

    def create_draft(
        self,
        *,
        filename: str | None,
        content: bytes,
        provider_consent: bool,
    ) -> RegistryOcrDraftResponse:
        if self._settings is None:
            raise ContractOcrUnavailableError("등기사항증명서 OCR 기능이 현재 활성화되어 있지 않습니다.")
        if not provider_consent:
            raise ContractOcrError("Google Document AI 외부 OCR 전송 동의가 필요합니다.")
        if not filename or Path(filename).suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ContractOcrError("현재는 등기사항증명서 PDF만 지원합니다.")
        if not content:
            raise ContractOcrError("빈 파일은 처리할 수 없습니다.")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ContractOcrError("파일 크기는 20MB 이하여야 합니다.")

        settings = self._settings
        started = perf_counter()
        try:
            with tempfile.TemporaryDirectory(prefix="alphalawva-registry-ocr-") as directory:
                pdf_path = Path(directory) / "registry.pdf"
                pdf_path.write_bytes(content)
                inspection = inspect_contract_document(
                    pdf_path, max_pdf_pages=MAX_REGISTRY_PAGES
                )
                if inspection.kind != DocumentKind.PDF:
                    raise ContractOcrError("등기사항증명서는 PDF만 지원합니다.")
                resource = (
                    f"projects/{settings.project}/locations/{settings.location}"
                    f"/processors/{settings.processor}"
                    f"/processorVersions/{settings.version}"
                )
                document, _ = request_document(
                    resource,
                    settings.location,
                    content,
                    settings.timeout_seconds,
                    credentials=load_adc_credentials(),
                    expected_pages=None,
                )
        except DocumentIntakeError as error:
            raise ContractOcrError(str(error)) from None
        except LiveOCRError as error:
            raise ContractOcrError(str(error)) from None
        except OSError:
            raise ContractOcrError("업로드 파일을 처리할 수 없습니다.") from None

        if "등기사항" not in re.sub(r"\s+", "", document.get("text", "")):
            raise ContractOcrError("등기사항증명서로 인식하지 못했습니다. 인터넷등기소·정부24에서 발급한 PDF를 올려주세요.")
        try:
            extraction = extract_registry_document_ai(document)
        except ValueError as error:
            raise ContractOcrError(str(error)) from None

        return RegistryOcrDraftResponse(
            status="needs_review",
            provider="Google Document AI",
            page_count=len(document["pages"]),
            extraction=extraction,
            warnings=extraction.warnings,
            processing_seconds=perf_counter() - started,
            retention_notice=(
                "업로드 원본과 Document AI 원문 응답은 이 OCR 초안 응답을 만든 뒤 저장하지 않습니다."
            ),
        )


def create_registry_ocr_service_from_env() -> RegistryOcrService:
    return RegistryOcrService(load_document_ai_settings_from_env())
