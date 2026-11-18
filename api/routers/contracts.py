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
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    Request,
    UploadFile,
    status,
)

from api.models.contracts import (
    ContractReviewAccepted,
    ContractReviewJobResponse,
    ContractReviewRequest,
    SpecialClauseDraftAccepted,
    SpecialClauseDraftJobResponse,
    SpecialClauseDraftRequest,
)
from api.models.contract_ocr import ContractOcrDraftResponse
from api.models.registry_ocr import RegistryOcrDraftResponse
from api.models.contract_snapshots import ContractType
from api.models.contract_snapshots import ConfirmedContractSnapshot
from api.models.errors import ApiErrorResponse
from api.models.registry_snapshots import ConfirmedRegistrySnapshot
from api.services.confirmed_snapshot_store import ConfirmedSnapshotRepository
from api.services.contract_review_service import ContractReviewService
from api.services.special_clause_service import SpecialClauseService
from api.services.contract_ocr_service import MAX_UPLOAD_BYTES, ContractOcrService
from api.services.registry_ocr_service import RegistryOcrService


# 문서(Swagger) 그룹은 엔드포인트마다 지정한다: OCR, 스냅샷, 계약서 검토, 특약 생성.
router = APIRouter(prefix="/api/v1")

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


def get_contract_ocr_service(request: Request) -> ContractOcrService:
    service = getattr(request.app.state, "contract_ocr_service", None)
    if service is None:
        raise RuntimeError("계약서 OCR 서비스가 초기화되지 않았습니다.")
    return service


def get_registry_ocr_service(request: Request) -> RegistryOcrService:
    service = getattr(request.app.state, "registry_ocr_service", None)
    if service is None:
        raise RuntimeError("등기사항증명서 OCR 서비스가 초기화되지 않았습니다.")
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
ContractOcrServiceDependency = Annotated[
    ContractOcrService,
    Depends(get_contract_ocr_service),
]
RegistryOcrServiceDependency = Annotated[
    RegistryOcrService,
    Depends(get_registry_ocr_service),
]


@router.post(
    "/contract-ocr-drafts",
    tags=["contract-ocr"],
    response_model=ContractOcrDraftResponse,
    responses={400: {"model": ApiErrorResponse}, 503: {"model": ApiErrorResponse}},
    summary="사용자 확인 전 계약서 OCR 초안 생성",
)
async def create_contract_ocr_draft(
    file: Annotated[UploadFile, File(description="3페이지 표준 계약서 PDF")],
    contract_type: Annotated[ContractType, Form()],
    provider_consent: Annotated[bool, Form()],
    service: ContractOcrServiceDependency,
) -> ContractOcrDraftResponse:
    try:
        # Read at most one byte beyond the public upload limit so oversized
        # files are rejected by the service without loading an unbounded body.
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        return service.create_draft(
            filename=file.filename,
            content=content,
            contract_type=contract_type,
            provider_consent=provider_consent,
        )
    finally:
        await file.close()


@router.post(
    "/registry-ocr-drafts",
    tags=["contract-ocr"],
    response_model=RegistryOcrDraftResponse,
    responses={400: {"model": ApiErrorResponse}, 503: {"model": ApiErrorResponse}},
    summary="사용자 확인 전 등기사항증명서 OCR 초안 생성",
)
async def create_registry_ocr_draft(
    file: Annotated[UploadFile, File(description="등기사항증명서 PDF")],
    provider_consent: Annotated[bool, Form()],
    service: RegistryOcrServiceDependency,
) -> RegistryOcrDraftResponse:
    try:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        return service.create_draft(
            filename=file.filename,
            content=content,
            provider_consent=provider_consent,
        )
    finally:
        await file.close()


@router.post(
    "/contract-snapshots",
    tags=["contract-snapshots"],
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
    tags=["contract-snapshots"],
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
    "/contract-reviews",
    tags=["contract-review"],
    response_model=ContractReviewAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **SNAPSHOT_LOOKUP_RESPONSES,
        409: {"model": ApiErrorResponse, "description": "중복 방지 키 충돌"},
    },
    summary="사용자 확인 계약 정보의 검토 작업 접수",
)
def create_contract_review(
    payload: ContractReviewRequest,
    background_tasks: BackgroundTasks,
    service: ContractReviewServiceDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ContractReviewAccepted:
    submission = service.submit(payload, idempotency_key=idempotency_key)
    if submission.is_new:
        background_tasks.add_task(service.process, submission.response.review_id)
    return submission.response


@router.get(
    "/contract-reviews/{review_id}",
    tags=["contract-review"],
    response_model=ContractReviewJobResponse,
    responses={404: {"model": ApiErrorResponse, "description": "작업 없음 또는 만료"}},
    summary="계약서 검토 작업 조회",
)
def get_contract_review(
    review_id: UUID,
    service: ContractReviewServiceDependency,
) -> ContractReviewJobResponse:
    return service.get(review_id)


@router.post(
    "/special-clause-drafts",
    tags=["special-clause"],
    response_model=SpecialClauseDraftAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **VALIDATION_RESPONSES,
        409: {"model": ApiErrorResponse, "description": "중복 방지 키 충돌"},
    },
    summary="자연어 특약 생성 작업 접수",
)
def create_special_clause_draft(
    payload: SpecialClauseDraftRequest,
    background_tasks: BackgroundTasks,
    service: SpecialClauseServiceDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> SpecialClauseDraftAccepted:
    submission = service.submit(payload, idempotency_key=idempotency_key)
    if submission.is_new:
        background_tasks.add_task(service.process, submission.response.draft_id)
    return submission.response


@router.get(
    "/special-clause-drafts/{draft_id}",
    tags=["special-clause"],
    response_model=SpecialClauseDraftJobResponse,
    responses={404: {"model": ApiErrorResponse, "description": "작업 없음 또는 만료"}},
    summary="특약 생성 작업 조회",
)
def get_special_clause_draft(
    draft_id: UUID,
    service: SpecialClauseServiceDependency,
) -> SpecialClauseDraftJobResponse:
    return service.get(draft_id)
