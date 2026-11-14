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
    RETRIEVED = "retrieved"
    UNAVAILABLE = "unavailable"


class AnalysisStatus(str, Enum):
    PENDING = "pending"
    ANALYZED = "analyzed"
    UNAVAILABLE = "unavailable"


class GenerationStatus(str, Enum):
    PENDING = "pending"
    GENERATED = "generated"
    UNAVAILABLE = "unavailable"


class ReviewItemStatus(str, Enum):
    PENDING_EVIDENCE = "pending_evidence"
    REVIEW_READY = "review_ready"
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


class ContractReviewEvidenceItem(ContractApiModel):
    item_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    rank: int = Field(ge=1)
    similarity: float = Field(ge=-1, le=1)
    metadata: dict[str, str | int | float | None] = Field(default_factory=dict)


class ContractReviewAnalysisItem(ContractApiModel):
    item_id: str = Field(min_length=1)
    summary: str = Field(min_length=2, max_length=500)
    review_status: str = Field(pattern="^(safe|check)$")
    # review_status가 safe이면 수정안·협상 안내는 빈 문자열이다.
    revision: str = Field(default="", max_length=1000)
    negotiation: str = Field(default="", max_length=700)
    evidence_source_ids: list[str] = Field(min_length=1, max_length=6)


class ContractReviewResponse(ContractApiModel):
    review_id: UUID
    status: str = Field(min_length=1)
    input_summary: ContractInputSummary
    contract_items: list[ContractReviewItem]
    registry_items: list[ContractReviewItem]
    precheck_items: list[ContractReviewItem]
    evidence: list[ContractReviewEvidenceItem]
    evidence_status: EvidenceStatus
    analysis: list[ContractReviewAnalysisItem]
    analysis_status: AnalysisStatus
    message: str = Field(min_length=1)
    disclaimer: str = Field(min_length=1)


class SpecialClauseDraftRequest(ContractApiModel):
    requests: list[str] = Field(min_length=1, max_length=10)


class SpecialClauseDraftItem(ContractApiModel):
    request_index: int = Field(ge=1)
    request_text: str = Field(min_length=2, max_length=1000)
    status: str = Field(min_length=1)
    draft_text: str | None = Field(default=None, min_length=2, max_length=1500)
    title: str | None = Field(default=None, min_length=2, max_length=100)
    reason: str | None = Field(default=None, min_length=2, max_length=700)
    strategy: str | None = Field(default=None, min_length=2, max_length=700)
    needs_input: list[str] = Field(default_factory=list, max_length=5)
    evidence_source_ids: list[str] = Field(default_factory=list, max_length=5)


class SpecialClauseDraftResponse(ContractApiModel):
    draft_id: UUID
    status: str = Field(min_length=1)
    items: list[SpecialClauseDraftItem] = Field(min_length=1)
    evidence: list[ContractReviewEvidenceItem]
    evidence_status: EvidenceStatus
    generation_status: GenerationStatus
    message: str = Field(min_length=1)
    disclaimer: str = Field(min_length=1)
