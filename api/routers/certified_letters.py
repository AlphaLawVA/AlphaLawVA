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
