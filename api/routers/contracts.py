# contracts.py
"""
Description: 계약서 검토와 특약 생성 요청 HTTP 엔드포인트를 정의한다.
라우터는 요청 검증 후 도메인 서비스에 위임하고 공개 응답 모델을 적용한다.
Author: ooheunsu
Date: 2026-10-24
Before:
    - 계약서 검토와 특약 생성 기능을 호출할 버전 명시 HTTP 경로가 없는 상태.
After:
    - 확인된 계약 정보 검토와 자연어 특약 요청 API 경로가 제공됨.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from api.models.contracts import (
    ContractReviewRequest,
    ContractReviewResponse,
    SpecialClauseDraftRequest,
    SpecialClauseDraftResponse,
)
from api.models.contract_snapshots import ConfirmedContractSnapshot
from api.models.errors import ApiErrorResponse
from api.models.registry_snapshots import ConfirmedRegistrySnapshot
from api.services.confirmed_snapshot_store import ConfirmedSnapshotRepository
from api.services.contract_review_service import ContractReviewService
from api.services.special_clause_service import SpecialClauseService


router = APIRouter(prefix="/api/v1", tags=["contracts"])

VALIDATION_RESPONSES = {
    422: {"model": ApiErrorResponse, "description": "요청 값 검증 실패"},
}
SNAPSHOT_LOOKUP_RESPONSES = {
    **VALIDATION_RESPONSES,
    404: {"model": ApiErrorResponse, "description": "확정 스냅샷 없음"},
}


def get_contract_review_service(request: Request) -> ContractReviewService:
    service = getattr(request.app.state, "contract_review_service", None)
    if service is None:
        raise RuntimeError("계약서 검토 서비스가 초기화되지 않았습니다.")
    return service


def get_confirmed_snapshot_store(request: Request) -> ConfirmedSnapshotRepository:
    store = getattr(request.app.state, "confirmed_snapshot_store", None)
    if store is None:
        raise RuntimeError("확정 스냅샷 저장소가 초기화되지 않았습니다.")
    return store


def get_special_clause_service(request: Request) -> SpecialClauseService:
    service = getattr(request.app.state, "special_clause_service", None)
    if service is None:
        raise RuntimeError("특약 생성 서비스가 초기화되지 않았습니다.")
    return service


ContractReviewServiceDependency = Annotated[
    ContractReviewService,
    Depends(get_contract_review_service),
]
ConfirmedSnapshotStoreDependency = Annotated[
    ConfirmedSnapshotRepository,
    Depends(get_confirmed_snapshot_store),
]
SpecialClauseServiceDependency = Annotated[
    SpecialClauseService,
    Depends(get_special_clause_service),
]


@router.post(
    "/contract-snapshots",
    response_model=ConfirmedContractSnapshot,
    status_code=status.HTTP_201_CREATED,
    responses=VALIDATION_RESPONSES,
    summary="사용자 확인 계약서 스냅샷 저장",
)
def save_contract_snapshot(
    payload: ConfirmedContractSnapshot,
    store: ConfirmedSnapshotStoreDependency,
) -> ConfirmedContractSnapshot:
    return store.save_contract(payload)


@router.post(
    "/registry-snapshots",
    response_model=ConfirmedRegistrySnapshot,
    status_code=status.HTTP_201_CREATED,
    responses=VALIDATION_RESPONSES,
    summary="사용자 확인 등본 스냅샷 저장",
)
def save_registry_snapshot(
    payload: ConfirmedRegistrySnapshot,
    store: ConfirmedSnapshotStoreDependency,
) -> ConfirmedRegistrySnapshot:
    return store.save_registry(payload)


@router.post(
    "/contract-reviews",
    response_model=ContractReviewResponse,
    responses=SNAPSHOT_LOOKUP_RESPONSES,
    summary="사용자 확인 계약 정보의 검토 대기 항목 생성",
)
def create_contract_review(
    payload: ContractReviewRequest,
    service: ContractReviewServiceDependency,
) -> ContractReviewResponse:
    return service.review(payload)


@router.post(
    "/special-clause-drafts",
    response_model=SpecialClauseDraftResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=VALIDATION_RESPONSES,
    summary="자연어 특약 생성 요청 접수",
)
def create_special_clause_draft(
    payload: SpecialClauseDraftRequest,
    service: SpecialClauseServiceDependency,
) -> SpecialClauseDraftResponse:
    return service.create_draft(payload)
