# errors.py
"""
Description: AlphaLawVA API의 공통 오류 응답과 필드 검증 오류 스키마를 정의한다.
프론트엔드가 상태 코드와 별개로 안정적인 오류 코드를 처리할 수 있게 한다.
Author: choeminju
Date: 2026-10-14
Before:
    - FastAPI 기본 오류와 판례 도메인 오류의 응답 형식이 통일되지 않은 상태.
After:
    - 오류 코드·안내 문구·선택형 필드 오류 목록을 공통 모델로 반환할 수 있음.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ApiErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    INVALID_PRECEDENT_ID = "invalid_precedent_id"
    PRECEDENT_NOT_FOUND = "precedent_not_found"
    SEARCH_SESSION_NOT_FOUND = "search_session_not_found"
    SEARCH_RESULT_NOT_FOUND = "search_result_not_found"
    PRECEDENT_FAVORITE_NOT_FOUND = "precedent_favorite_not_found"
    SIMILARITY_EXPLANATION_IN_PROGRESS = "similarity_explanation_in_progress"
    SIMILARITY_EXPLANATION_UNAVAILABLE = "similarity_explanation_unavailable"
    PRECEDENT_DATA_UNAVAILABLE = "precedent_data_unavailable"
    SEARCH_BACKEND_UNAVAILABLE = "search_backend_unavailable"
    SNAPSHOT_NOT_FOUND = "snapshot_not_found"


class ApiErrorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiFieldError(ApiErrorModel):
    location: list[str | int] = Field(
        description="오류가 발생한 요청 필드 위치"
    )
    message: str
    type: str


class ApiError(ApiErrorModel):
    code: ApiErrorCode
    message: str
    field_errors: list[ApiFieldError] = Field(default_factory=list)


class ApiErrorResponse(ApiErrorModel):
    error: ApiError
