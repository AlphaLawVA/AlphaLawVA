"""사용자 보관함의 내용증명 저장·조회 API 모델을 정의한다."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from pydantic import Field, model_validator

from api.models.certified_letters import (
    CertifiedLetterApiModel,
    CertifiedLetterDraftResult,
)


class CertifiedLetterResultSaveRequest(CertifiedLetterApiModel):
    draft_id: UUID


class CertifiedLetterResultSummary(CertifiedLetterApiModel):
    result_id: UUID
    draft_id: UUID
    title: str = Field(min_length=1, max_length=200)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> CertifiedLetterResultSummary:
        for field_name in ("created_at", "updated_at"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError(f"{field_name}은 UTC timezone을 포함해야 합니다.")
        return self


class CertifiedLetterResultDetail(CertifiedLetterResultSummary):
    result: CertifiedLetterDraftResult


class CertifiedLetterResultList(CertifiedLetterApiModel):
    items: list[CertifiedLetterResultSummary]
