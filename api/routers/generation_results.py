# generation_results.py
"""
Description: 계약서 검토·특약 생성 결과를 내 보관함에 저장·조회하는 HTTP 엔드포인트를 정의한다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 계약서 검토와 특약 생성 결과를 저장하는 API 경로가 없는 상태.
After:
    - 로그인 사용자가 결과를 저장하고 목록·상세로 다시 볼 수 있음.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from api.dependencies.auth import CurrentUser
from api.models.errors import ApiErrorResponse
from api.models.generation_results import (
    GenerationResultDetail,
    GenerationResultList,
    GenerationResultSaveRequest,
)
from api.services.generation_results import GenerationResultService

router = APIRouter(prefix="/api/v1", tags=["generation-results"])


def get_generation_result_service(request: Request) -> GenerationResultService:
    service = getattr(request.app.state, "generation_result_service", None)
    if service is None:
        raise RuntimeError("생성 결과 저장 서비스가 초기화되지 않았습니다.")
    return service


GenerationResultServiceDependency = Annotated[
    GenerationResultService,
    Depends(get_generation_result_service),
]


@router.post(
    "/generation-results",
    response_model=GenerationResultDetail,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ApiErrorResponse, "description": "로그인 필요"},
        404: {"model": ApiErrorResponse, "description": "저장할 결과 없음 또는 보관 시간 지남"},
        409: {"model": ApiErrorResponse, "description": "저장할 수 없는 결과"},
    },
    summary="방금 만든 계약서 검토·특약 생성 결과를 내 보관함에 저장",
)
def save_generation_result(
    payload: GenerationResultSaveRequest,
    current_user: CurrentUser,
    service: GenerationResultServiceDependency,
) -> GenerationResultDetail:
    return service.save(current_user.user_id, payload.kind, payload.source_id)


@router.get(
    "/generation-results",
    response_model=GenerationResultList,
    responses={401: {"model": ApiErrorResponse, "description": "로그인 필요"}},
    summary="내 저장 결과 목록 조회",
)
def list_generation_results(
    current_user: CurrentUser,
    service: GenerationResultServiceDependency,
) -> GenerationResultList:
    return service.list(current_user.user_id)


@router.get(
    "/generation-results/{result_id}",
    response_model=GenerationResultDetail,
    responses={
        401: {"model": ApiErrorResponse, "description": "로그인 필요"},
        404: {"model": ApiErrorResponse, "description": "저장 결과 없음"},
    },
    summary="내 저장 결과 상세 조회",
)
def get_generation_result(
    result_id: UUID,
    current_user: CurrentUser,
    service: GenerationResultServiceDependency,
) -> GenerationResultDetail:
    return service.get(current_user.user_id, result_id)
