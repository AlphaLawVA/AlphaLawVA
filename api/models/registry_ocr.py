# registry_ocr.py
"""
Description: Google Document AI 등기사항증명서 OCR 초안 API의 응답 구조를 정의한다.
사용자 확인 전 추출값과 원문 대조용 좌표만 반환하며 원본 파일은 저장하지 않는다.
Author: ooheunsu
Date: 2026-11-04
Before:
    - 등기부 추출기 결과를 프런트 확인 화면으로 전달할 공개 응답 모델이 없음.
After:
    - 등기사항증명서 OCR 초안과 확인 필요 경고를 FastAPI 응답으로 검증할 수 있음.
"""

from __future__ import annotations

from pydantic import Field

from api.models.contracts import ContractApiModel
from ml.document_processing.contracts.extraction_schema import RegistryExtraction


class RegistryOcrDraftResponse(ContractApiModel):
    status: str = Field(pattern="^needs_review$")
    provider: str = Field(min_length=1)
    page_count: int = Field(ge=1)
    extraction: RegistryExtraction
    warnings: list[str]
    processing_seconds: float = Field(ge=0)
    retention_notice: str = Field(min_length=1)
