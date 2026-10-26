# __init__.py
"""
Description: AlphaLawVA API 서비스 패키지를 정의한다.
라우터와 검색기·데이터 접근 계층 사이의 애플리케이션 로직을 배치한다.
Author: choeminju
Date: 2026-10-13
Before:
    - 라우터에서 위임할 애플리케이션 서비스 패키지가 없는 상태.
After:
    - 검색과 데이터 조합 로직을 분리할 서비스 패키지가 제공됨.
"""

from api.services.precedent_case_repository import (
    DEFAULT_FINAL_CASES_DIR,
    InvalidPrecedentIdError,
    PrecedentCaseDataError,
    PrecedentCaseNotFoundError,
    PrecedentCaseRepository,
    PrecedentCaseRepositoryError,
)
from api.services.contract_review_service import ContractReviewService
from api.services.confirmed_snapshot_store import (
    ConfirmedSnapshotStore,
    SnapshotNotFoundError,
)
from api.services.gemini_similarity_explainer import (
    GeminiSimilarityExplainer,
    create_gemini_similarity_explainer_from_env,
)
from api.services.precedent_favorites import (
    PrecedentFavorite,
    PrecedentFavoriteNotFoundError,
    PrecedentFavoriteStore,
)
from api.services.precedent_search_service import (
    PrecedentSearchBackendUnavailableError,
    PrecedentSearchService,
    QueryScopeGuard,
    ScopeDecision,
    SimilarityExplainer,
    SimilarityExplanationUnavailableError,
)
from api.services.precedent_search_sessions import (
    PrecedentSearchSession,
    PrecedentSearchSessionStore,
    SearchResultInteractionState,
    SearchResultNotFoundError,
    SearchSessionError,
    SearchSessionNotFoundError,
    SimilarityExplanationInProgressError,
    SimilarityExplanationSnapshot,
)
from api.services.special_clause_service import SpecialClauseService

__all__ = [
    "DEFAULT_FINAL_CASES_DIR",
    "ContractReviewService",
    "ConfirmedSnapshotStore",
    "InvalidPrecedentIdError",
    "GeminiSimilarityExplainer",
    "PrecedentFavorite",
    "PrecedentFavoriteNotFoundError",
    "PrecedentFavoriteStore",
    "PrecedentCaseDataError",
    "PrecedentCaseNotFoundError",
    "PrecedentCaseRepository",
    "PrecedentCaseRepositoryError",
    "PrecedentSearchBackendUnavailableError",
    "PrecedentSearchService",
    "PrecedentSearchSession",
    "PrecedentSearchSessionStore",
    "QueryScopeGuard",
    "ScopeDecision",
    "SearchResultInteractionState",
    "SearchResultNotFoundError",
    "SearchSessionError",
    "SearchSessionNotFoundError",
    "SimilarityExplainer",
    "SimilarityExplanationInProgressError",
    "SimilarityExplanationSnapshot",
    "SimilarityExplanationUnavailableError",
    "SpecialClauseService",
    "SnapshotNotFoundError",
    "create_gemini_similarity_explainer_from_env",
]
