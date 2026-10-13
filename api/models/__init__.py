# __init__.py
"""
Description: AlphaLawVA API 모델 패키지를 정의한다.
요청과 응답에 사용하는 Pydantic 스키마를 이 패키지에 배치한다.
Author: choeminju
Date: 2026-10-13
Before:
    - API 요청과 응답 모델을 배치할 패키지가 없는 상태.
After:
    - 공개 API 스키마를 관리할 모델 패키지가 제공됨.
"""

from api.models.errors import (
    ApiError,
    ApiErrorCode,
    ApiErrorResponse,
    ApiFieldError,
)
from api.models.precedents import (
    FullTextSection,
    MatchedChunk,
    PrecedentDetailResponse,
    PrecedentFullTextResponse,
    PrecedentLinks,
    PrecedentMetadata,
    PrecedentOfficialFields,
    PrecedentSearchItem,
    PrecedentSearchRequest,
    PrecedentSearchResponse,
    PrecedentSummaries,
    SearchQuery,
    SearchStatus,
    SimilarityExplanation,
    SimilarityExplanationMethod,
    SimilarityExplanationStatus,
    SummaryPreview,
    SummarySource,
)

__all__ = [
    "ApiError",
    "ApiErrorCode",
    "ApiErrorResponse",
    "ApiFieldError",
    "FullTextSection",
    "MatchedChunk",
    "PrecedentDetailResponse",
    "PrecedentFullTextResponse",
    "PrecedentLinks",
    "PrecedentMetadata",
    "PrecedentOfficialFields",
    "PrecedentSearchItem",
    "PrecedentSearchRequest",
    "PrecedentSearchResponse",
    "PrecedentSummaries",
    "SearchQuery",
    "SearchStatus",
    "SimilarityExplanation",
    "SimilarityExplanationMethod",
    "SimilarityExplanationStatus",
    "SummaryPreview",
    "SummarySource",
]
