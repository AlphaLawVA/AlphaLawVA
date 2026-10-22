# certified_letters.py
"""
Description: 내용증명 초안 생성 작업의 공개 요청·응답 스키마를 정의한다.
계약 입력, 비동기 작업 상태, 법령 근거와 유사 판례를 명시적으로 검증한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 슈퍼로바의 느슨한 contract_data와 동기 생성 응답만 참고할 수 있는 상태.
After:
    - AlphaLawVA 내용증명 생성 API의 타입 안전한 I/O 계약을 검증할 수 있음.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CertifiedLetterApiModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class ContractType(str, Enum):
    JEONSE = "jeonse"
    MONTHLY_RENT = "monthly_rent"


class CertifiedLetterJobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class CertifiedLetterOutcome(str, Enum):
    DRAFT_READY = "draft_ready"
    NEEDS_INFORMATION = "needs_information"
    OUT_OF_SCOPE = "out_of_scope"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ContractPartyInput(CertifiedLetterApiModel):
    name: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=500)
    detail_address: str | None = Field(default=None, min_length=1, max_length=200)
    phone_number: str | None = Field(default=None, min_length=7, max_length=30)


class ContractPropertyInput(CertifiedLetterApiModel):
    address: str = Field(min_length=1, max_length=500)
    detail_address: str | None = Field(default=None, min_length=1, max_length=200)
    building_type: str | None = Field(default=None, min_length=1, max_length=100)


class ContractPeriodInput(CertifiedLetterApiModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_period(self) -> ContractPeriodInput:
        if self.end_date <= self.start_date:
            raise ValueError("end_date는 start_date보다 늦어야 합니다.")
        return self


class ContractPaymentInput(CertifiedLetterApiModel):
    deposit: int = Field(ge=0, description="원 단위 보증금")
    monthly_rent: int | None = Field(
        default=None,
        ge=0,
        description="원 단위 월세; 전세 계약이면 null",
    )


class CertifiedLetterContractInput(CertifiedLetterApiModel):
    contract_type: ContractType
    property: ContractPropertyInput
    period: ContractPeriodInput
    payment: ContractPaymentInput
    lessor: ContractPartyInput
    lessee: ContractPartyInput

    @model_validator(mode="after")
    def validate_payment_for_contract_type(self) -> CertifiedLetterContractInput:
        monthly_rent = self.payment.monthly_rent
        if self.contract_type is ContractType.JEONSE and monthly_rent is not None:
            raise ValueError("전세 계약의 monthly_rent는 null이어야 합니다.")
        if self.contract_type is ContractType.MONTHLY_RENT and (
            monthly_rent is None or monthly_rent <= 0
        ):
            raise ValueError("월세 계약에는 1원 이상의 monthly_rent가 필요합니다.")
        return self


class CertifiedLetterDraftRequest(CertifiedLetterApiModel):
    purpose: str = Field(
        min_length=2,
        max_length=200,
        description="내용증명으로 달성하려는 목적",
    )
    facts: str = Field(
        min_length=10,
        max_length=3000,
        description="사용자가 확인한 구체적인 사실관계",
    )
    requested_deadline: date | None = Field(
        default=None,
        description="사용자가 지정한 이행 기한; 없으면 상대 기한을 제안할 수 있음",
    )
    contract: CertifiedLetterContractInput


class CertifiedLetterDraftAccepted(CertifiedLetterApiModel):
    draft_id: UUID
    status: CertifiedLetterJobStatus = CertifiedLetterJobStatus.QUEUED
    created_at: datetime

    @model_validator(mode="after")
    def validate_accepted_timestamp(self) -> CertifiedLetterDraftAccepted:
        _require_utc(self.created_at, "created_at")
        return self


class CertifiedLetterParty(CertifiedLetterApiModel):
    name: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=500)
    detail_address: str | None = Field(default=None, min_length=1, max_length=200)


class CertifiedLetterDocument(CertifiedLetterApiModel):
    title: str = Field(min_length=1, max_length=200)
    sender: CertifiedLetterParty
    recipient: CertifiedLetterParty
    body: str = Field(min_length=1, max_length=20000)


class FollowUpAction(CertifiedLetterApiModel):
    order: int = Field(ge=1)
    action: str = Field(min_length=1, max_length=1000)


class StatuteEvidence(CertifiedLetterApiModel):
    statute_id: str | None = Field(
        default=None,
        min_length=1,
        description="국가법령정보센터 등 외부 출처의 법령 식별자",
    )
    law_name: str = Field(min_length=1, max_length=300)
    article_label: str = Field(min_length=1, max_length=100)
    article_title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str = Field(min_length=1)
    explanation: str = Field(min_length=1, max_length=2000)
    effective_date: date | None = None
    source_node_id: str | None = Field(default=None, min_length=1)
    source_url: str | None = Field(default=None, min_length=1, max_length=2000)


class SimilarPrecedent(CertifiedLetterApiModel):
    precedent_id: str = Field(
        min_length=1,
        description="국가법령정보센터 판례일련번호",
    )
    case_number: str | None = Field(default=None, min_length=1, max_length=200)
    case_name: str | None = Field(default=None, min_length=1, max_length=500)
    court_name: str | None = Field(default=None, min_length=1, max_length=200)
    decision_date: date | None = None
    summary: str = Field(min_length=1, max_length=4000)
    matched_reason: str | None = Field(default=None, min_length=1, max_length=2000)
    source_url: str = Field(min_length=1, max_length=2000)


class CertifiedLetterDraftResult(CertifiedLetterApiModel):
    document: CertifiedLetterDocument
    strategy_summary: str = Field(min_length=1, max_length=4000)
    follow_up_actions: list[FollowUpAction]
    statute_evidence: list[StatuteEvidence] = Field(min_length=1)
    similar_precedents: list[SimilarPrecedent]
    warnings: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_follow_up_order(self) -> CertifiedLetterDraftResult:
        expected = list(range(1, len(self.follow_up_actions) + 1))
        actual = [action.order for action in self.follow_up_actions]
        if actual != expected:
            raise ValueError("follow_up_actions의 order는 1부터 연속해야 합니다.")
        if any(not warning.strip() for warning in self.warnings):
            raise ValueError("warnings에는 빈 문자열을 포함할 수 없습니다.")
        return self


class CertifiedLetterJobError(CertifiedLetterApiModel):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1000)


class CertifiedLetterDraftJobResponse(CertifiedLetterApiModel):
    draft_id: UUID
    status: CertifiedLetterJobStatus
    outcome: CertifiedLetterOutcome | None = None
    result: CertifiedLetterDraftResult | None = None
    missing_information: list[str] = Field(default_factory=list)
    message: str | None = Field(default=None, min_length=1, max_length=2000)
    error: CertifiedLetterJobError | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_job_state(self) -> CertifiedLetterDraftJobResponse:
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")
        if self.completed_at is not None:
            _require_utc(self.completed_at, "completed_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at은 created_at보다 빠를 수 없습니다.")
        if self.completed_at is not None and self.completed_at < self.created_at:
            raise ValueError("completed_at은 created_at보다 빠를 수 없습니다.")

        if self.status in {
            CertifiedLetterJobStatus.QUEUED,
            CertifiedLetterJobStatus.PROCESSING,
        }:
            if any(
                value is not None
                for value in (
                    self.outcome,
                    self.result,
                    self.message,
                    self.error,
                    self.completed_at,
                )
            ) or self.missing_information:
                raise ValueError("진행 중 작업에는 결과, 오류 또는 완료 시각이 없어야 합니다.")
            return self

        if self.status is CertifiedLetterJobStatus.FAILED:
            if self.error is None or self.completed_at is None:
                raise ValueError("failed 상태에는 error와 completed_at이 필요합니다.")
            if self.outcome is not None or self.result is not None:
                raise ValueError("failed 상태에는 outcome이나 result가 없어야 합니다.")
            return self

        if self.outcome is None or self.completed_at is None:
            raise ValueError("completed 상태에는 outcome과 completed_at이 필요합니다.")
        if self.error is not None:
            raise ValueError("completed 상태에는 error가 없어야 합니다.")

        if self.outcome is CertifiedLetterOutcome.DRAFT_READY:
            if self.result is None:
                raise ValueError("draft_ready 결과에는 result가 필요합니다.")
            if self.missing_information or self.message is not None:
                raise ValueError(
                    "draft_ready 결과에는 missing_information이나 message가 없어야 합니다."
                )
            return self

        if self.result is not None or not self.message:
            raise ValueError("초안 미생성 결과에는 result 없이 message가 필요합니다.")
        if (
            self.outcome is CertifiedLetterOutcome.NEEDS_INFORMATION
            and not self.missing_information
        ):
            raise ValueError("needs_information에는 누락 정보가 필요합니다.")
        if (
            self.outcome is not CertifiedLetterOutcome.NEEDS_INFORMATION
            and self.missing_information
        ):
            raise ValueError("needs_information 외 결과에는 누락 정보가 없어야 합니다.")
        return self


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name}은 UTC timezone을 포함해야 합니다.")
