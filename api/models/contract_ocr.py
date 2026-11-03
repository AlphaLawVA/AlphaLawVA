# contract_ocr.py
"""
Description: Google Document AI 계약서 OCR 초안 API의 응답 구조를 정의한다.
사용자 확인 전 추출값과 원문 대조용 좌표만 반환하며 원본 파일은 저장하지 않는다.
Author: ooheunsu
Date: 2026-11-02
Before:
    - 계약서 OCR CLI 결과를 프런트 확인 화면으로 전달할 공개 응답 모델이 없음.
After:
    - 표준 계약서 OCR 초안과 확인 필요 항목을 FastAPI 응답으로 검증할 수 있음.
"""

from __future__ import annotations

from pydantic import Field

from api.models.contracts import ContractApiModel
from ml.document_processing.contracts.extraction_schema import ContractExtraction


class ContractOcrReviewItem(ContractApiModel):
    path: str = Field(min_length=1)
    status: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    source: dict[str, object] | None = None


class ContractOcrDraftResponse(ContractApiModel):
    status: str = Field(pattern="^needs_review$")
    provider: str = Field(min_length=1)
    extraction: ContractExtraction
    review_items: list[ContractOcrReviewItem]
    warnings: list[str]
    processing_seconds: float = Field(ge=0)
    retention_notice: str = Field(min_length=1)
