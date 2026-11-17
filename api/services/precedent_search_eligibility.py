# precedent_search_eligibility.py
"""
Description: 공통 판례 허용 목록 필터를 판례 검색 API의 BGE-M3 검색기에 연결한다.
환경 설정을 읽는 기존 API 서비스 팩토리와 호환되는 공개 이름을 유지한다.
Author: choeminju
Date: 2026-11-06
Before:
    - 전체 판례 벡터 DB에서 상가·매매·토지 판례도 검색 결과로 반환될 수 있음.
After:
    - 원본과 벡터 DB를 변경하지 않고 모든 판례 검색 기능이 같은 허용 목록을 재사용함.
"""

from __future__ import annotations

from ml.rag.precedent_eligibility import (
    DEFAULT_ELIGIBILITY_CANDIDATE_MULTIPLIER,
    DEFAULT_ELIGIBILITY_MANIFEST_PATH,
    ELIGIBILITY_SCHEMA_VERSION,
    EligibilityFilteredPrecedentRetriever,
    PrecedentRetriever,
    create_eligibility_filtered_precedent_retriever_from_env,
    load_eligible_precedent_ids,
)
from ml.rag.precedent_retriever import BgeM3PrecedentRetriever


__all__ = [
    "DEFAULT_ELIGIBILITY_CANDIDATE_MULTIPLIER",
    "DEFAULT_ELIGIBILITY_MANIFEST_PATH",
    "ELIGIBILITY_SCHEMA_VERSION",
    "EligibilityFilteredPrecedentRetriever",
    "create_precedent_search_retriever_from_env",
    "load_eligible_precedent_ids",
]


def create_precedent_search_retriever_from_env() -> PrecedentRetriever:
    """환경 설정에 따라 판례 검색 API에 공통 허용 목록 필터를 연결한다."""

    return create_eligibility_filtered_precedent_retriever_from_env(
        BgeM3PrecedentRetriever(),
    )
