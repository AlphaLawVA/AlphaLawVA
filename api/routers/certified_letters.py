# certified_letters.py
"""
Description: 내용증명 초안 생성 접수와 비동기 작업 조회 HTTP 엔드포인트를 정의한다.
라우터는 요청·응답 처리만 담당하고 생성과 저장을 애플리케이션 서비스에 위임한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 내용증명 생성 워크플로를 호출할 공개 API 경로가 없는 상태.
After:
    - 202 접수와 UUID 기반 작업 상태 조회 경로가 제공됨.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request, status

from api.models.certified_letters import (
    CertifiedLetterDraftAccepted,
    CertifiedLetterDraftJobResponse,
    CertifiedLetterDraftRequest,
)
from api.models.errors import ApiErrorResponse
from api.models.user_results import (
    CertifiedLetterResultDetail,
    CertifiedLetterResultList,
    CertifiedLetterResultSaveRequest,
)
from api.dependencies.auth import CurrentUser
from api.services.certified_letter_results import CertifiedLetterResultService
from api.services.certified_letter_service import CertifiedLetterService


router = APIRouter(prefix="/api/v1", tags=["certified-letters"])


def get_certified_letter_service(request: Request) -> CertifiedLetterService:
    service = getattr(request.app.state, "certified_letter_service", None)
    if service is None:
        raise RuntimeError("내용증명 생성 서비스가 초기화되지 않았습니다.")
    return service


CertifiedLetterServiceDependency = Annotated[
    CertifiedLetterService,
    Depends(get_certified_letter_service),
]


def get_certified_letter_result_service(
    request: Request,
) -> CertifiedLetterResultService:
    service = getattr(request.app.state, "certified_letter_result_service", None)
    if service is None:
        raise RuntimeError("내용증명 결과 저장 서비스가 초기화되지 않았습니다.")
    return service


CertifiedLetterResultServiceDependency = Annotated[
    CertifiedLetterResultService,
    Depends(get_certified_letter_result_service),
]
IdempotencyKeyHeader = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        pattern=r".*\S.*",
        description="중복 생성 방지를 위한 클라이언트 생성 키",
    ),
]


@router.post(
    "/certified-letter-drafts",
    response_model=CertifiedLetterDraftAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        409: {"model": ApiErrorResponse, "description": "중복 방지 키 충돌"},
        422: {"model": ApiErrorResponse, "description": "요청 값 검증 실패"},
        503: {"model": ApiErrorResponse, "description": "생성 서비스 사용 불가"},
    },
    summary="내용증명 초안 생성 작업 접수",
)
def create_certified_letter_draft(
    payload: CertifiedLetterDraftRequest,
    background_tasks: BackgroundTasks,
    service: CertifiedLetterServiceDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> CertifiedLetterDraftAccepted:
    submission = service.submit(payload, idempotency_key=idempotency_key)
    if submission.is_new:
        background_tasks.add_task(service.process, submission.response.draft_id)
    return submission.response


@router.get(
    "/certified-letter-drafts/{draft_id}",
    response_model=CertifiedLetterDraftJobResponse,
    responses={
        404: {"model": ApiErrorResponse, "description": "작업 없음 또는 만료"},
    },
    summary="내용증명 초안 생성 작업 조회",
)
def get_certified_letter_draft(
    draft_id: UUID,
    service: CertifiedLetterServiceDependency,
) -> CertifiedLetterDraftJobResponse:
    return service.get(draft_id)


@router.post(
    "/certified-letter-results",
    response_model=CertifiedLetterResultDetail,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ApiErrorResponse, "description": "로그인 필요"},
        404: {"model": ApiErrorResponse, "description": "생성 작업 없음"},
        409: {"model": ApiErrorResponse, "description": "저장 가능한 결과가 아님"},
        503: {"model": ApiErrorResponse, "description": "인증 서비스 사용 불가"},
    },
    summary="완성된 내용증명 결과를 내 보관함에 저장",
)
def save_certified_letter_result(
    payload: CertifiedLetterResultSaveRequest,
    current_user: CurrentUser,
    service: CertifiedLetterResultServiceDependency,
) -> CertifiedLetterResultDetail:
    return service.save(current_user.user_id, payload.draft_id)


@router.get(
    "/certified-letter-results",
    response_model=CertifiedLetterResultList,
    responses={
        401: {"model": ApiErrorResponse, "description": "로그인 필요"},
        503: {"model": ApiErrorResponse, "description": "인증 서비스 사용 불가"},
    },
    summary="내 내용증명 저장 결과 목록 조회",
)
def list_certified_letter_results(
    current_user: CurrentUser,
    service: CertifiedLetterResultServiceDependency,
) -> CertifiedLetterResultList:
    return service.list(current_user.user_id)


@router.get(
    "/certified-letter-results/{result_id}",
    response_model=CertifiedLetterResultDetail,
    responses={
        401: {"model": ApiErrorResponse, "description": "로그인 필요"},
        404: {"model": ApiErrorResponse, "description": "저장 결과 없음"},
        503: {"model": ApiErrorResponse, "description": "인증 서비스 사용 불가"},
    },
    summary="내 내용증명 저장 결과 상세 조회",
)
def get_certified_letter_result(
    result_id: UUID,
    current_user: CurrentUser,
    service: CertifiedLetterResultServiceDependency,
) -> CertifiedLetterResultDetail:
    return service.get(current_user.user_id, result_id)
