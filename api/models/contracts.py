# contracts.py
"""
Description: 계약서 검토와 특약 생성 요청의 공개 API 스키마를 정의한다.
확정된 계약 정보와 자연어 요청을 분리해 RAG 연결 전 단계의 상태를 명시한다.
Author: ooheunsu
Date: 2026-10-24
Before:
    - 계약서 검토와 특약 생성 기능에 합의된 공개 I/O 스키마가 없는 상태.
After:
    - 사용자 확인 계약 정보와 특약 자연어 요청을 검증하는 Pydantic 스키마가 제공됨.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api.models.contract_snapshots import ContractReviewWorkflowInput, ContractType


class ContractApiModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class EvidenceStatus(str, Enum):
    PENDING_RETRIEVAL = "pending_retrieval"


class ReviewItemStatus(str, Enum):
    PENDING_EVIDENCE = "pending_evidence"
    NEEDS_INPUT = "needs_input"


class ContractReviewRequest(ContractReviewWorkflowInput):
    """확정 계약서·등본 스냅샷을 참조하는 계약서 검토 요청이다."""


class ContractInputSummary(ContractApiModel):
    contract_snapshot_id: UUID
    registry_snapshot_id: UUID | None = None
    contract_type: ContractType
    property_address: str
    has_registry: bool
    special_term_count: int = Field(ge=0)


class ContractReviewItem(ContractApiModel):
    item_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    label: str = Field(min_length=1)
    original_text: str | None = Field(default=None, min_length=1)
    status: ReviewItemStatus
    message: str = Field(min_length=1)


class ContractReviewResponse(ContractApiModel):
    review_id: UUID
    status: str = Field(min_length=1)
    input_summary: ContractInputSummary
    contract_items: list[ContractReviewItem]
    registry_items: list[ContractReviewItem]
    evidence_status: EvidenceStatus
    message: str = Field(min_length=1)
    disclaimer: str = Field(min_length=1)


class SpecialClauseDraftRequest(ContractApiModel):
    requests: list[str] = Field(min_length=1, max_length=10)


class SpecialClauseDraftItem(ContractApiModel):
    request_index: int = Field(ge=1)
    request_text: str = Field(min_length=2, max_length=1000)
    status: str = Field(min_length=1)


class SpecialClauseDraftResponse(ContractApiModel):
    draft_id: UUID
    status: str = Field(min_length=1)
    items: list[SpecialClauseDraftItem] = Field(min_length=1)
    evidence_status: EvidenceStatus
    message: str = Field(min_length=1)
    disclaimer: str = Field(min_length=1)
