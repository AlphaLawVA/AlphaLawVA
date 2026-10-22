# precedents.py
"""
Description: 판례 자연어 검색과 상세·내부 원문 조회 HTTP 엔드포인트를 정의한다.
요청 검증과 응답 모델 적용 후 모든 판례 처리 로직을 검색 서비스에 위임한다.
Author: choeminju
Date: 2026-10-14
Before:
    - 판례 검색 서비스는 있지만 프론트엔드가 호출할 HTTP 경로가 없는 상태.
After:
    - 버전이 명시된 판례 검색·상세·내부 원문 API 라우터가 제공됨.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from api.models.errors import ApiErrorResponse
from api.models.precedents import (
    PrecedentDetailResponse,
    PrecedentFavoriteCreateRequest,
    PrecedentFavoriteListResponse,
    PrecedentFavoriteResponse,
    PrecedentFullTextResponse,
    PrecedentSearchRequest,
    PrecedentSearchResponse,
    SimilarityExplanation,
)
from api.services.precedent_search_service import PrecedentSearchService


router = APIRouter(prefix="/api/v1", tags=["precedents"])

VALIDATION_AND_BACKEND_RESPONSES = {
    422: {"model": ApiErrorResponse, "description": "요청 값 검증 실패"},
    503: {"model": ApiErrorResponse, "description": "판례 검색 백엔드 사용 불가"},
}
PRECEDENT_LOOKUP_RESPONSES = {
    400: {"model": ApiErrorResponse, "description": "잘못된 판례일련번호"},
    404: {"model": ApiErrorResponse, "description": "검색 기록 또는 판례 없음"},
    503: {"model": ApiErrorResponse, "description": "판례 데이터 사용 불가"},
}
EXPLANATION_RESPONSES = {
    **PRECEDENT_LOOKUP_RESPONSES,
    409: {"model": ApiErrorResponse, "description": "유사성 설명 생성 중"},
}
FAVORITE_LOOKUP_RESPONSES = {
    404: {"model": ApiErrorResponse, "description": "즐겨찾기 없음"},
    503: {"model": ApiErrorResponse, "description": "판례 데이터 사용 불가"},
}


def get_precedent_search_service(request: Request) -> PrecedentSearchService:
    service = getattr(request.app.state, "precedent_search_service", None)
    if service is None:
        raise RuntimeError("판례 검색 서비스가 초기화되지 않았습니다.")
    return service


PrecedentSearchServiceDependency = Annotated[
    PrecedentSearchService,
    Depends(get_precedent_search_service),
]


@router.post(
    "/precedent-searches",
    response_model=PrecedentSearchResponse,
    responses=VALIDATION_AND_BACKEND_RESPONSES,
    summary="자연어 상황으로 유사 판례 검색",
)
def search_precedents(
    payload: PrecedentSearchRequest,
    service: PrecedentSearchServiceDependency,
) -> PrecedentSearchResponse:
    return service.search(payload)


@router.get(
    "/precedent-searches/{search_id}/results/{precedent_id}",
    response_model=PrecedentDetailResponse,
    responses=PRECEDENT_LOOKUP_RESPONSES,
    summary="검색 결과 판례 상세 조회",
)
def get_precedent_detail(
    search_id: UUID,
    precedent_id: str,
    service: PrecedentSearchServiceDependency,
) -> PrecedentDetailResponse:
    return service.get_detail(search_id, precedent_id)


@router.post(
    "/precedent-searches/{search_id}/results/{precedent_id}/similarity-explanation",
    response_model=SimilarityExplanation,
    responses=EXPLANATION_RESPONSES,
    summary="검색 결과와 내 상황 비교 생성",
)
def generate_precedent_similarity_explanation(
    search_id: UUID,
    precedent_id: str,
    service: PrecedentSearchServiceDependency,
) -> SimilarityExplanation:
    return service.generate_similarity_explanation(search_id, precedent_id)


@router.post(
    "/precedent-favorites",
    response_model=PrecedentFavoriteResponse,
    responses=PRECEDENT_LOOKUP_RESPONSES,
    summary="검색 결과 판례 즐겨찾기",
    description=(
        "인증·영구 DB 연결 전에는 현재 API 프로세스 메모리에 검색 질문, "
        "판례 결과와 설명 상태를 함께 보관합니다."
    ),
)
def create_precedent_favorite(
    payload: PrecedentFavoriteCreateRequest,
    service: PrecedentSearchServiceDependency,
) -> PrecedentFavoriteResponse:
    return service.create_favorite(payload.search_id, payload.precedent_id)


@router.get(
    "/precedent-favorites",
    response_model=PrecedentFavoriteListResponse,
    summary="판례 즐겨찾기 목록 조회",
)
def list_precedent_favorites(
    service: PrecedentSearchServiceDependency,
) -> PrecedentFavoriteListResponse:
    return service.list_favorites()


@router.get(
    "/precedent-favorites/{favorite_id}",
    response_model=PrecedentFavoriteResponse,
    responses=FAVORITE_LOOKUP_RESPONSES,
    summary="판례 즐겨찾기 상세 조회",
)
def get_precedent_favorite(
    favorite_id: UUID,
    service: PrecedentSearchServiceDependency,
) -> PrecedentFavoriteResponse:
    return service.get_favorite(favorite_id)


@router.post(
    "/precedent-favorites/{favorite_id}/similarity-explanation",
    response_model=SimilarityExplanation,
    responses={
        **FAVORITE_LOOKUP_RESPONSES,
        409: {"model": ApiErrorResponse, "description": "유사성 설명 생성 중"},
    },
    summary="즐겨찾기 판례와 내 상황 비교 생성",
)
def generate_favorite_similarity_explanation(
    favorite_id: UUID,
    service: PrecedentSearchServiceDependency,
) -> SimilarityExplanation:
    return service.generate_favorite_similarity_explanation(favorite_id)


@router.delete(
    "/precedent-favorites/{favorite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=FAVORITE_LOOKUP_RESPONSES,
    summary="판례 즐겨찾기 삭제",
)
def delete_precedent_favorite(
    favorite_id: UUID,
    service: PrecedentSearchServiceDependency,
) -> Response:
    service.delete_favorite(favorite_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/precedents/{precedent_id}/full-text",
    response_model=PrecedentFullTextResponse,
    responses=PRECEDENT_LOOKUP_RESPONSES,
    summary="AlphaLawVA 내부 판례 원문 조회",
)
def get_precedent_full_text(
    precedent_id: str,
    service: PrecedentSearchServiceDependency,
) -> PrecedentFullTextResponse:
    return service.get_full_text(precedent_id)
