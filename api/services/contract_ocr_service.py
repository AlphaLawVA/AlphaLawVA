# contract_ocr_service.py
"""
Description: 업로드된 표준 계약서 PDF를 Google Document AI로 일회성 인식하고
사용자 확인 전 구조화 초안을 반환한다. 원본 파일과 OCR 원문은 영속 저장하지 않는다.
Author: ooheunsu
Date: 2026-11-02
Before:
    - Document AI는 합성자료 전용 CLI에서만 호출할 수 있음.
After:
    - FastAPI가 명시 동의한 표준 계약서 PDF를 메모리·임시파일 범위에서 처리함.
"""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from api.models.contract_ocr import ContractOcrDraftResponse, ContractOcrReviewItem
from api.models.contract_snapshots import ContractType
from ml.document_processing.contracts.document_ai_extractor import extract_document_ai
from ml.document_processing.contracts.document_ai_live import (
    LiveOCRError,
    load_adc_credentials,
    prepare,
    request_document,
)
from ml.document_processing.contracts.lease_pipeline import review_fields, validate_template


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
SUPPORTED_SUFFIXES = {".pdf"}


class ContractOcrError(ValueError):
    """업로드 OCR 요청을 안전하게 처리할 수 없을 때 사용한다."""


class ContractOcrUnavailableError(ContractOcrError):
    """OCR provider 설정이 없거나 명시적으로 비활성화됐을 때 사용한다."""


@dataclass(frozen=True)
class DocumentAiOcrSettings:
    project: str
    location: str
    processor: str
    version: str
    timeout_seconds: int


class ContractOcrService:
    def __init__(self, settings: DocumentAiOcrSettings | None) -> None:
        self._settings = settings

    def create_draft(
        self,
        *,
        filename: str | None,
        content: bytes,
        contract_type: ContractType,
        provider_consent: bool,
    ) -> ContractOcrDraftResponse:
        if self._settings is None:
            raise ContractOcrUnavailableError("계약서 OCR 기능이 현재 활성화되어 있지 않습니다.")
        if not provider_consent:
            raise ContractOcrError("Google Document AI 외부 OCR 전송 동의가 필요합니다.")
        if not filename or Path(filename).suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ContractOcrError("현재는 3페이지 표준 계약서 PDF만 지원합니다.")
        if not content:
            raise ContractOcrError("빈 파일은 처리할 수 없습니다.")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ContractOcrError("파일 크기는 20MB 이하여야 합니다.")

        started = perf_counter()
        try:
            with tempfile.TemporaryDirectory(prefix="alphalawva-contract-ocr-") as directory:
                pdf_path = Path(directory) / "contract.pdf"
                pdf_path.write_bytes(content)
                raw, config, _, _ = prepare(
                    pdf_path,
                    self._settings.project,
                    self._settings.location,
                    self._settings.processor,
                    self._settings.version,
                )
                document, _ = request_document(
                    config["resource"],
                    self._settings.location,
                    raw,
                    self._settings.timeout_seconds,
                    credentials=load_adc_credentials(),
                )
        except LiveOCRError as error:
            raise ContractOcrError(str(error)) from None
        except OSError:
            raise ContractOcrError("업로드 파일을 처리할 수 없습니다.") from None

        try:
            validate_template(document, contract_type.value)
            extraction = extract_document_ai(document, contract_type.value)
        except ValueError as error:
            raise ContractOcrError(str(error)) from None

        review_items = [
            ContractOcrReviewItem.model_validate(item)
            for item in review_fields(extraction.model_dump(mode="json"))
        ]
        return ContractOcrDraftResponse(
            status="needs_review",
            provider="Google Document AI",
            extraction=extraction,
            review_items=review_items,
            warnings=extraction.warnings,
            processing_seconds=perf_counter() - started,
            retention_notice=(
                "업로드 원본과 Document AI 원문 응답은 이 OCR 초안 응답을 만든 뒤 저장하지 않습니다."
            ),
        )


def create_contract_ocr_service_from_env() -> ContractOcrService:
    enabled = os.environ.get("DOCUMENT_AI_OCR_ENABLED", "false").lower() == "true"
    required = {
        "project": os.environ.get("DOCUMENT_AI_PROJECT_ID", "").strip(),
        "processor": os.environ.get("DOCUMENT_AI_PROCESSOR_ID", "").strip(),
        "version": os.environ.get("DOCUMENT_AI_PROCESSOR_VERSION", "").strip(),
    }
    if not enabled or not all(required.values()):
        return ContractOcrService(None)
    try:
        timeout_seconds = int(os.environ.get("DOCUMENT_AI_OCR_TIMEOUT_SECONDS", "120"))
    except ValueError:
        return ContractOcrService(None)
    if not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 300:
        return ContractOcrService(None)
    return ContractOcrService(
        DocumentAiOcrSettings(
            project=required["project"],
            location=os.environ.get("DOCUMENT_AI_LOCATION", "us").strip(),
            processor=required["processor"],
            version=required["version"],
            timeout_seconds=timeout_seconds,
        )
    )
