# precedent_search_service.py
"""
Description: 판례 검색기 결과와 final_cases 원본을 공개 API 응답으로 조합한다.
검색 세션, 원문 링크, 요약 미리보기와 선택형 범위 판정·LLM 설명을 관리한다.
Author: choeminju
Date: 2026-10-14
Before:
    - 검색기와 판례 원본은 존재하지만 화면용 판례 단위 응답으로 조합되지 않은 상태.
After:
    - 목록·상세·내부 원문 API에서 재사용할 판례 검색 서비스가 제공됨.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, Sequence
from uuid import UUID

from api.models.precedents import (
    FullTextSection,
    MatchedChunk,
    PrecedentDetailResponse,
    PrecedentFavoriteListResponse,
    PrecedentFavoriteResponse,
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
    SummaryPreview,
    SummarySource,
)
from api.services.precedent_case_repository import PrecedentCaseRepository
from api.services.precedent_favorites import (
    PrecedentFavorite,
    PrecedentFavoriteStore,
)
from api.services.precedent_search_sessions import (
    SearchResultInteractionState,
    SimilarityExplanationSnapshot,
    PrecedentSearchSessionStore,
)
from ml.rag.precedent_retriever import (
    BgeM3PrecedentRetriever,
    PrecedentChunkMatch,
    PrecedentSearchResult,
)


logger = logging.getLogger(__name__)

SUMMARY_PREVIEW_MAX_LENGTH = 180
OFFICIAL_SOURCE_URL = "https://law.go.kr/LSW/precInfoP.do?mode=0&precSeq={precedent_id}"
NO_RESULTS_MESSAGE = "입력한 상황과 유사한 판례를 찾지 못했습니다. 상황을 더 구체적으로 입력해 주세요."
DEFAULT_OUT_OF_SCOPE_MESSAGE = (
    "부동산 계약이나 임대차 분쟁과 관련된 상황을 구체적으로 입력해 주세요."
)
FULL_TEXT_FIELDS = (
    ("order", "주문", "주문"),
    ("claim", "청구취지", "청구취지"),
    ("lower_court_decision", "원심판결", "원심판결"),
    ("reason", "이유", "이유"),
)


class PrecedentSearchBackendUnavailableError(Exception):
    """Raised when the configured precedent retrieval backend cannot run."""


class SimilarityExplanationUnavailableError(Exception):
    """Raised when no similarity explanation provider is configured."""


class PrecedentRetriever(Protocol):
    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
    ) -> list[PrecedentSearchResult]: ...


@dataclass(frozen=True)
class ScopeDecision:
    is_in_scope: bool
    message: str | None = None


class QueryScopeGuard(Protocol):
    def evaluate(self, query: str) -> ScopeDecision: ...


class SimilarityExplainer(Protocol):
    @property
    def method(self) -> SimilarityExplanationMethod: ...

    @property
    def model_name(self) -> str: ...

    def explain(
        self,
        *,
        query: str,
        precedent: dict[str, Any],
        matched_chunks: Sequence[PrecedentChunkMatch],
    ) -> str: ...


class PrecedentSearchService:
    def __init__(
        self,
        *,
        retriever: PrecedentRetriever | None = None,
        repository: PrecedentCaseRepository | None = None,
        sessions: PrecedentSearchSessionStore | None = None,
        favorites: PrecedentFavoriteStore | None = None,
        scope_guard: QueryScopeGuard | None = None,
        similarity_explainer: SimilarityExplainer | None = None,
    ) -> None:
        self.retriever = retriever if retriever is not None else BgeM3PrecedentRetriever()
        self.repository = (
            repository if repository is not None else PrecedentCaseRepository()
        )
        self.sessions = sessions if sessions is not None else PrecedentSearchSessionStore()
        self.favorites = favorites if favorites is not None else PrecedentFavoriteStore()
        self.scope_guard = scope_guard
        self.similarity_explainer = similarity_explainer

    def search(self, request: PrecedentSearchRequest) -> PrecedentSearchResponse:
        if self.scope_guard is not None:
            decision = self.scope_guard.evaluate(request.query)
            if not decision.is_in_scope:
                session = self.sessions.create(request.query, [])
                return PrecedentSearchResponse(
                    search_id=session.search_id,
                    status=SearchStatus.OUT_OF_SCOPE,
                    query=SearchQuery(text=session.query),
                    result_count=0,
                    results=[],
                    message=decision.message or DEFAULT_OUT_OF_SCOPE_MESSAGE,
                )

        try:
            results = self.retriever.search(request.query, top_k=request.limit)
        except (FileNotFoundError, RuntimeError) as error:
            raise PrecedentSearchBackendUnavailableError(
                "판례 검색 백엔드를 사용할 수 없습니다."
            ) from error
        session = self.sessions.create(request.query, results)
        if not results:
            return PrecedentSearchResponse(
                search_id=session.search_id,
                status=SearchStatus.NO_RESULTS,
                query=SearchQuery(text=session.query),
                result_count=0,
                results=[],
                message=NO_RESULTS_MESSAGE,
            )

        items = [
            self._build_search_item(session.search_id, result)
            for result in results
        ]
        return PrecedentSearchResponse(
            search_id=session.search_id,
            status=SearchStatus.COMPLETED,
            query=SearchQuery(text=session.query),
            result_count=len(items),
            results=items,
        )

    def get_detail(
        self,
        search_id: UUID,
        precedent_id: str,
    ) -> PrecedentDetailResponse:
        session, result, interaction = self.sessions.get_result_context(
            search_id,
            precedent_id,
        )
        case_data = self.repository.get(precedent_id)
        return PrecedentDetailResponse(
            search_id=search_id,
            query=SearchQuery(text=session.query),
            precedent=self._build_metadata(case_data),
            summaries=self._build_summaries(case_data),
            official_fields=self._build_official_fields(case_data),
            similarity_explanation=self._build_similarity_explanation_response(
                matched_chunks=result.matched_chunks,
                snapshot=interaction.snapshot(),
            ),
            is_favorited=self.favorites.is_favorited(search_id, precedent_id),
            links=self._build_links(precedent_id, search_id=search_id),
        )

    def generate_similarity_explanation(
        self,
        search_id: UUID,
        precedent_id: str,
    ) -> SimilarityExplanation:
        session, result, interaction = self.sessions.get_result_context(
            search_id,
            precedent_id,
        )
        return self._generate_similarity_explanation(
            search_id=search_id,
            query=session.query,
            result=result,
            interaction=interaction,
        )

    def create_favorite(
        self,
        search_id: UUID,
        precedent_id: str,
    ) -> PrecedentFavoriteResponse:
        session, result, interaction = self.sessions.get_result_context(
            search_id,
            precedent_id,
        )
        favorite = self.favorites.create(
            search_id=search_id,
            query=session.query,
            result=result,
            interaction=interaction,
        )
        return self._build_favorite_response(favorite)

    def list_favorites(self) -> PrecedentFavoriteListResponse:
        favorites = [
            self._build_favorite_response(favorite)
            for favorite in self.favorites.list()
        ]
        return PrecedentFavoriteListResponse(
            favorite_count=len(favorites),
            favorites=favorites,
        )

    def get_favorite(self, favorite_id: UUID) -> PrecedentFavoriteResponse:
        return self._build_favorite_response(self.favorites.get(favorite_id))

    def delete_favorite(self, favorite_id: UUID) -> None:
        self.favorites.delete(favorite_id)

    def generate_favorite_similarity_explanation(
        self,
        favorite_id: UUID,
    ) -> SimilarityExplanation:
        favorite = self.favorites.get(favorite_id)
        return self._generate_similarity_explanation(
            search_id=favorite.search_id,
            query=favorite.query,
            result=favorite.result,
            interaction=favorite.interaction,
        )

    def get_full_text(self, precedent_id: str) -> PrecedentFullTextResponse:
        case_data = self.repository.get(precedent_id)
        sections = []
        for key, title, source_field in FULL_TEXT_FIELDS:
            content = self._optional_text(case_data.get(source_field))
            if content is not None:
                sections.append(
                    FullTextSection(
                        position=len(sections) + 1,
                        key=key,
                        title=title,
                        content=content,
                    )
                )
        return PrecedentFullTextResponse(
            precedent=self._build_metadata(case_data),
            summaries=self._build_summaries(case_data),
            official_fields=self._build_official_fields(case_data),
            sections=sections,
            links=self._build_links(precedent_id),
        )

    def _build_search_item(
        self,
        search_id: UUID,
        result: PrecedentSearchResult,
    ) -> PrecedentSearchItem:
        case_data = self.repository.get(result.precedent_id)
        return PrecedentSearchItem(
            rank=result.rank,
            precedent=self._build_metadata(case_data),
            similarity_score=result.similarity,
            matched_chunk_count=len(result.matched_chunks),
            matched_chunks=[
                self._build_matched_chunk(chunk)
                for chunk in result.matched_chunks
            ],
            preview=self._build_preview(case_data),
            is_favorited=self.favorites.is_favorited(
                search_id,
                result.precedent_id,
            ),
            links=self._build_links(result.precedent_id, search_id=search_id),
        )

    def _generate_similarity_explanation(
        self,
        *,
        search_id: UUID,
        query: str,
        result: PrecedentSearchResult,
        interaction: SearchResultInteractionState,
    ) -> SimilarityExplanation:
        if self.similarity_explainer is None:
            raise SimilarityExplanationUnavailableError(
                "유사성 설명 모델이 설정되지 않았습니다."
            )

        case_data = self.repository.get(result.precedent_id)
        method = self.similarity_explainer.method
        model = self.similarity_explainer.model_name
        should_generate = interaction.start_generation(method, model)
        if not should_generate:
            return self._build_similarity_explanation_response(
                matched_chunks=result.matched_chunks,
                snapshot=interaction.snapshot(),
            )

        try:
            reason = self.similarity_explainer.explain(
                query=query,
                precedent=case_data,
                matched_chunks=result.matched_chunks,
            ).strip()
            if not reason:
                raise ValueError("유사성 설명이 비어 있습니다.")
        except Exception as error:
            interaction.fail_generation(method, model)
            logger.warning(
                "Precedent similarity explanation failed: "
                "search_id=%s precedent_id=%s model=%s error_type=%s error=%s",
                search_id,
                result.precedent_id,
                model,
                type(error).__name__,
                error,
            )
        else:
            interaction.complete_generation(method, model, reason)

        return self._build_similarity_explanation_response(
            matched_chunks=result.matched_chunks,
            snapshot=interaction.snapshot(),
        )

    @classmethod
    def _build_similarity_explanation_response(
        cls,
        *,
        matched_chunks: Sequence[PrecedentChunkMatch],
        snapshot: SimilarityExplanationSnapshot,
    ) -> SimilarityExplanation:
        chunks = [cls._build_matched_chunk(chunk) for chunk in matched_chunks]
        method = snapshot.method or SimilarityExplanationMethod.MATCHED_CHUNKS_ONLY
        return SimilarityExplanation(
            status=snapshot.status,
            method=method,
            model=snapshot.model,
            matched_reason=snapshot.matched_reason,
            matched_chunks=chunks,
        )

    def _build_favorite_response(
        self,
        favorite: PrecedentFavorite,
    ) -> PrecedentFavoriteResponse:
        precedent_id = favorite.result.precedent_id
        case_data = self.repository.get(precedent_id)
        return PrecedentFavoriteResponse(
            favorite_id=favorite.favorite_id,
            created_at=favorite.created_at,
            search_id=favorite.search_id,
            query=SearchQuery(text=favorite.query),
            precedent=self._build_metadata(case_data),
            summaries=self._build_summaries(case_data),
            official_fields=self._build_official_fields(case_data),
            similarity_explanation=self._build_similarity_explanation_response(
                matched_chunks=favorite.result.matched_chunks,
                snapshot=favorite.interaction.snapshot(),
            ),
            links=self._build_favorite_links(
                favorite.favorite_id,
                precedent_id,
            ),
        )

    @staticmethod
    def _build_matched_chunk(chunk: PrecedentChunkMatch) -> MatchedChunk:
        return MatchedChunk(
            rank=chunk.rank,
            chunk_id=chunk.chunk_id,
            section=chunk.section,
            chunk_type=chunk.chunk_type,
            section_chunk_index=chunk.section_chunk_index,
            similarity_score=chunk.similarity,
            text=chunk.text,
        )

    @classmethod
    def _build_metadata(cls, case_data: dict[str, Any]) -> PrecedentMetadata:
        precedent_id = str(case_data["판례일련번호"])
        case_numbers = case_data.get("사건번호목록")
        normalized_case_numbers = (
            [item.strip() for item in case_numbers if isinstance(item, str) and item.strip()]
            if isinstance(case_numbers, list)
            else []
        )
        return PrecedentMetadata(
            precedent_id=precedent_id,
            case_number=cls._optional_text(case_data.get("사건번호")),
            case_numbers=normalized_case_numbers,
            case_name=cls._optional_text(case_data.get("사건명")),
            court_name=cls._optional_text(case_data.get("법원명")),
            decision_date=cls._optional_text(case_data.get("선고일자")),
            case_type=cls._optional_text(case_data.get("사건종류명")),
            judgment_type=cls._optional_text(case_data.get("판결유형")),
        )

    @classmethod
    def _build_preview(cls, case_data: dict[str, Any]) -> SummaryPreview:
        easy_summary = cls._optional_text(case_data.get("쉬운요약"))
        if easy_summary is not None:
            return SummaryPreview(
                summary=cls._truncate_preview(easy_summary),
                source=SummarySource.EASY_SUMMARY,
            )
        precedent_summary = cls._optional_text(case_data.get("생성요약"))
        if precedent_summary is not None:
            return SummaryPreview(
                summary=cls._truncate_preview(precedent_summary),
                source=SummarySource.PRECEDENT_SUMMARY,
            )
        return SummaryPreview()

    @classmethod
    def _build_summaries(cls, case_data: dict[str, Any]) -> PrecedentSummaries:
        return PrecedentSummaries(
            precedent_summary=cls._optional_text(case_data.get("생성요약")),
            easy_summary=cls._optional_text(case_data.get("쉬운요약")),
        )

    @classmethod
    def _build_official_fields(
        cls,
        case_data: dict[str, Any],
    ) -> PrecedentOfficialFields:
        return PrecedentOfficialFields(
            issue=cls._optional_text(case_data.get("판시사항")),
            holding=cls._optional_text(case_data.get("판결요지")),
            referenced_statutes=cls._optional_text(case_data.get("참조조문")),
            referenced_precedents=cls._optional_text(case_data.get("참조판례")),
        )

    @staticmethod
    def _build_links(
        precedent_id: str,
        *,
        search_id: UUID | None = None,
    ) -> PrecedentLinks:
        detail = (
            f"/api/v1/precedent-searches/{search_id}/results/{precedent_id}"
            if search_id is not None
            else None
        )
        return PrecedentLinks(
            detail=detail,
            full_text=f"/api/v1/precedents/{precedent_id}/full-text",
            official_source=OFFICIAL_SOURCE_URL.format(precedent_id=precedent_id),
        )

    @staticmethod
    def _build_favorite_links(
        favorite_id: UUID,
        precedent_id: str,
    ) -> PrecedentLinks:
        return PrecedentLinks(
            detail=f"/api/v1/precedent-favorites/{favorite_id}",
            full_text=f"/api/v1/precedents/{precedent_id}/full-text",
            official_source=OFFICIAL_SOURCE_URL.format(precedent_id=precedent_id),
        )

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _truncate_preview(text: str) -> str:
        if len(text) <= SUMMARY_PREVIEW_MAX_LENGTH:
            return text
        return text[: SUMMARY_PREVIEW_MAX_LENGTH - 3].rstrip() + "..."
