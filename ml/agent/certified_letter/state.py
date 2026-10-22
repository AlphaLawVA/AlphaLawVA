# state.py
"""
Description: 내용증명 LangGraph가 사용하는 내부 도메인 값과 상태를 정의한다.
공개 API 모델과 분리하여 검색·생성 구현 세부 정보가 API 계약으로 새지 않게 한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 내용증명 워크플로가 공유할 타입 안전한 내부 상태가 없는 상태.
After:
    - 계약 사실, 검색 근거, 초안과 검증 결과를 독립적으로 전달할 수 있음.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import TypedDict


@dataclass(frozen=True)
class PartyData:
    name: str
    address: str
    detail_address: str | None = None
    phone_number: str | None = None


@dataclass(frozen=True)
class PropertyData:
    address: str
    detail_address: str | None = None
    building_type: str | None = None


@dataclass(frozen=True)
class PeriodData:
    start_date: date
    end_date: date


@dataclass(frozen=True)
class PaymentData:
    deposit: int
    monthly_rent: int | None = None


@dataclass(frozen=True)
class ContractData:
    contract_type: str
    property: PropertyData
    period: PeriodData
    payment: PaymentData
    lessor: PartyData
    lessee: PartyData


@dataclass(frozen=True)
class CertifiedLetterRequestData:
    purpose: str
    facts: str
    requested_deadline: date | None
    contract: ContractData


@dataclass(frozen=True)
class NormalizedCertifiedLetterRequest:
    purpose: str
    facts: str
    requested_deadline: date | None
    deadline_text: str
    is_deadline_suggested: bool
    contract: ContractData
    warnings: tuple[str, ...] = ()


class CertifiedLetterScopeDecision(str, Enum):
    IN_SCOPE = "in_scope"
    NEEDS_INFORMATION = "needs_information"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True)
class ScopeClassification:
    decision: CertifiedLetterScopeDecision
    missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class StatuteEvidenceData:
    chunk_id: str
    text: str
    similarity: float
    statute_id: str | None
    law_name: str
    article_label: str
    article_title: str | None = None
    effective_date: str | None = None
    source_node_id: str | None = None
    source_url: str | None = None
    explanation: str | None = None


@dataclass(frozen=True)
class PrecedentEvidenceData:
    precedent_id: str
    text: str
    similarity: float
    case_number: str | None = None
    case_name: str | None = None
    court_name: str | None = None
    decision_date: str | None = None
    summary: str | None = None
    matched_reason: str | None = None
    source_url: str | None = None
    rerank_texts: tuple[str, ...] = ()


@dataclass(frozen=True)
class DraftPartyData:
    name: str
    address: str
    detail_address: str | None = None


@dataclass(frozen=True)
class LetterDraftData:
    title: str
    sender: DraftPartyData
    recipient: DraftPartyData
    body: str
    strategy_summary: str
    follow_up_actions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    is_repairable: bool = True


@dataclass(frozen=True)
class WorkflowFailure:
    code: str
    message: str


class CertifiedLetterGraphState(TypedDict, total=False):
    request: CertifiedLetterRequestData
    normalized_request: NormalizedCertifiedLetterRequest
    scope_decision: str
    missing_information: tuple[str, ...]
    legal_issues: tuple[str, ...]
    statute_queries: tuple[str, ...]
    precedent_queries: tuple[str, ...]
    statute_candidates: tuple[StatuteEvidenceData, ...]
    selected_statutes: tuple[StatuteEvidenceData, ...]
    precedent_candidates: tuple[PrecedentEvidenceData, ...]
    selected_precedents: tuple[PrecedentEvidenceData, ...]
    draft: LetterDraftData
    validation_issues: tuple[ValidationIssue, ...]
    repair_attempts: int
    outcome: str
    message: str
    failure: WorkflowFailure
    trace: dict[str, str | int | float | bool | None]
