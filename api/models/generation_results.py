# generation_results.py
"""
Description: 계약서 검토·특약 생성 결과를 내 보관함에 저장·조회하는 API 모델을 정의한다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 계약서 검토와 특약 생성 결과를 저장하는 API 모델이 없는 상태.
After:
    - 저장 요청, 목록 항목, 상세(원본 응답 포함) 모델이 제공됨.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GenerationResultKind(str, Enum):
    CONTRACT_REVIEW = "contract_review"
    SPECIAL_CLAUSE = "special_clause"


class GenerationResultApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerationResultSaveRequest(GenerationResultApiModel):
    kind: GenerationResultKind
    source_id: UUID = Field(description="저장할 결과의 review_id 또는 draft_id")


class GenerationResultSummary(GenerationResultApiModel):
    result_id: UUID
    kind: GenerationResultKind
    source_id: UUID
    title: str = Field(min_length=1, max_length=200)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> GenerationResultSummary:
        for field_name in ("created_at", "updated_at"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError(f"{field_name}은 UTC timezone을 포함해야 합니다.")
        return self


class GenerationResultDetail(GenerationResultSummary):
    payload: dict[str, Any]


class GenerationResultList(GenerationResultApiModel):
    items: list[GenerationResultSummary]
