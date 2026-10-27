# main.py
"""
Description: AlphaLawVA FastAPI 애플리케이션과 판례 API 의존성을 구성한다.
공통 오류 처리와 서버 상태 확인 엔드포인트를 제공한다.
Author: choeminju
Date: 2026-10-13
Before:
    - api 패키지는 있지만 실행 가능한 FastAPI 애플리케이션이 없는 상태.
After:
    - api.main:app으로 서버를 실행하고 판례 API와 상태를 확인할 수 있음.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from api.models.errors import ApiError, ApiErrorCode, ApiErrorResponse, ApiFieldError
from api.routers import contracts_router, precedents_router
from api.services.contract_review_service import ContractReviewService
from api.services.confirmed_snapshot_store import ConfirmedSnapshotRepository
from api.services.confirmed_snapshot_store import ConfirmedSnapshotStore
from api.services.confirmed_snapshot_store import SnapshotNotFoundError
from api.services.gemini_similarity_explainer import (
    create_gemini_similarity_explainer_from_env,
)
from api.services.precedent_case_repository import (
    InvalidPrecedentIdError,
    PrecedentCaseDataError,
    PrecedentCaseNotFoundError,
)
from api.services.precedent_favorites import PrecedentFavoriteNotFoundError
from api.services.precedent_search_service import (
    PrecedentSearchBackendUnavailableError,
    PrecedentSearchService,
    SimilarityExplanationUnavailableError,
)
from api.services.precedent_search_sessions import (
    SearchResultNotFoundError,
    SearchSessionNotFoundError,
    SimilarityExplanationInProgressError,
)
from api.services.special_clause_service import SpecialClauseService
from ml.rag.contract_review_retriever import (
    create_contract_review_rag_retriever_from_env,
)
from api.services.mysql_snapshot_store import create_mysql_snapshot_store_from_env


PRECEDENT_DEMO_PATH = (
    Path(__file__).resolve().parent / "static" / "precedent_search_demo.html"
)


def _error_response(
    *,
    status_code: int,
    code: ApiErrorCode,
    message: str,
    field_errors: list[ApiFieldError] | None = None,
) -> JSONResponse:
    response = ApiErrorResponse(
        error=ApiError(
            code=code,
            message=message,
            field_errors=field_errors or [],
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
    )


async def request_validation_error_handler(
    _request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    field_errors = [
        ApiFieldError(
            location=list(item["loc"]),
            message=item["msg"],
            type=item["type"],
        )
        for item in error.errors()
    ]
    return _error_response(
        status_code=422,
        code=ApiErrorCode.INVALID_REQUEST,
        message="요청 값이 올바르지 않습니다.",
        field_errors=field_errors,
    )


async def invalid_precedent_id_error_handler(
    _request: Request,
    _error: InvalidPrecedentIdError,
) -> JSONResponse:
    return _error_response(
        status_code=400,
        code=ApiErrorCode.INVALID_PRECEDENT_ID,
        message="판례일련번호 형식이 올바르지 않습니다.",
    )


async def precedent_not_found_error_handler(
    _request: Request,
    _error: PrecedentCaseNotFoundError,
) -> JSONResponse:
    return _error_response(
        status_code=404,
        code=ApiErrorCode.PRECEDENT_NOT_FOUND,
        message="요청한 판례를 찾을 수 없습니다.",
    )


async def search_session_not_found_error_handler(
    _request: Request,
    _error: SearchSessionNotFoundError,
) -> JSONResponse:
    return _error_response(
        status_code=404,
        code=ApiErrorCode.SEARCH_SESSION_NOT_FOUND,
        message="검색 기록이 없거나 만료되었습니다. 다시 검색해 주세요.",
    )


async def search_result_not_found_error_handler(
    _request: Request,
    _error: SearchResultNotFoundError,
) -> JSONResponse:
    return _error_response(
        status_code=404,
        code=ApiErrorCode.SEARCH_RESULT_NOT_FOUND,
        message="해당 검색 결과에 포함되지 않은 판례입니다.",
    )


async def precedent_favorite_not_found_error_handler(
    _request: Request,
    _error: PrecedentFavoriteNotFoundError,
) -> JSONResponse:
    return _error_response(
        status_code=404,
        code=ApiErrorCode.PRECEDENT_FAVORITE_NOT_FOUND,
        message="요청한 판례 즐겨찾기를 찾을 수 없습니다.",
    )


async def similarity_explanation_in_progress_error_handler(
    _request: Request,
    _error: SimilarityExplanationInProgressError,
) -> JSONResponse:
    return _error_response(
        status_code=409,
        code=ApiErrorCode.SIMILARITY_EXPLANATION_IN_PROGRESS,
        message="유사성 설명을 이미 생성하고 있습니다.",
    )


async def similarity_explanation_unavailable_error_handler(
    _request: Request,
    _error: SimilarityExplanationUnavailableError,
) -> JSONResponse:
    return _error_response(
        status_code=503,
        code=ApiErrorCode.SIMILARITY_EXPLANATION_UNAVAILABLE,
        message="유사성 설명 서비스를 사용할 수 없습니다.",
    )


async def precedent_data_error_handler(
    _request: Request,
    _error: PrecedentCaseDataError,
) -> JSONResponse:
    return _error_response(
        status_code=503,
        code=ApiErrorCode.PRECEDENT_DATA_UNAVAILABLE,
        message="판례 원문 데이터를 불러올 수 없습니다.",
    )


async def search_backend_error_handler(
    _request: Request,
    _error: PrecedentSearchBackendUnavailableError,
) -> JSONResponse:
    return _error_response(
        status_code=503,
        code=ApiErrorCode.SEARCH_BACKEND_UNAVAILABLE,
        message="판례 검색 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
    )


async def snapshot_not_found_error_handler(
    _request: Request,
    _error: SnapshotNotFoundError,
) -> JSONResponse:
    return _error_response(
        status_code=404,
        code=ApiErrorCode.SNAPSHOT_NOT_FOUND,
        message="확정 계약서 또는 등본 정보를 찾을 수 없습니다.",
    )


def create_app(
    precedent_search_service: PrecedentSearchService | None = None,
    *,
    contract_review_service: ContractReviewService | None = None,
    special_clause_service: SpecialClauseService | None = None,
    confirmed_snapshot_store: ConfirmedSnapshotRepository | None = None,
) -> FastAPI:
    app = FastAPI(
        title="AlphaLawVA API",
        description="AlphaLawVA backend API",
        version="0.1.0",
    )
    app.state.precedent_search_service = (
        precedent_search_service
        if precedent_search_service is not None
        else PrecedentSearchService(
            similarity_explainer=create_gemini_similarity_explainer_from_env(),
        )
    )
    app.state.confirmed_snapshot_store = confirmed_snapshot_store or (
        create_mysql_snapshot_store_from_env() or ConfirmedSnapshotStore()
    )
    app.state.contract_review_service = (
        contract_review_service
        if contract_review_service is not None
        else ContractReviewService(
            app.state.confirmed_snapshot_store,
            evidence_retriever=create_contract_review_rag_retriever_from_env(),
        )
    )
    app.state.special_clause_service = (
        special_clause_service
        if special_clause_service is not None
        else SpecialClauseService()
    )
    app.include_router(contracts_router)
    app.include_router(precedents_router)
    app.add_exception_handler(
        RequestValidationError,
        request_validation_error_handler,
    )
    app.add_exception_handler(
        InvalidPrecedentIdError,
        invalid_precedent_id_error_handler,
    )
    app.add_exception_handler(
        PrecedentCaseNotFoundError,
        precedent_not_found_error_handler,
    )
    app.add_exception_handler(
        SearchSessionNotFoundError,
        search_session_not_found_error_handler,
    )
    app.add_exception_handler(
        SearchResultNotFoundError,
        search_result_not_found_error_handler,
    )
    app.add_exception_handler(
        PrecedentFavoriteNotFoundError,
        precedent_favorite_not_found_error_handler,
    )
    app.add_exception_handler(
        SimilarityExplanationInProgressError,
        similarity_explanation_in_progress_error_handler,
    )
    app.add_exception_handler(
        SimilarityExplanationUnavailableError,
        similarity_explanation_unavailable_error_handler,
    )
    app.add_exception_handler(
        PrecedentCaseDataError,
        precedent_data_error_handler,
    )
    app.add_exception_handler(
        PrecedentSearchBackendUnavailableError,
        search_backend_error_handler,
    )
    app.add_exception_handler(
        SnapshotNotFoundError,
        snapshot_not_found_error_handler,
    )

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "AlphaLawVA"}

    @app.get("/demo/precedent-search", include_in_schema=False)
    def precedent_search_demo() -> FileResponse:
        return FileResponse(PRECEDENT_DEMO_PATH)

    return app


app = create_app()
