# contract_review_retriever.py
"""
Description: 계약서 검토용 비식별 질의를 법령과 판례 검색기로 전달하고
출처 메타데이터를 보존한 근거 목록으로 변환한다.
Author: ooheunsu
Date: 2026-10-27
Before:
    - 계약서 검토 그래프가 비식별 검색 질의까지만 구성한 상태.
After:
    - 주입된 법령·판례 검색 결과를 항목별 근거 객체로 변환할 수 있음.
"""

from __future__ import annotations

import os
from threading import RLock
from typing import Any, Callable, Protocol

from ml.agent.contract_review_graph import (
    ContractReviewEvidence,
    ContractReviewRetrievalQuery,
)
from ml.rag.precedent_retriever import BgeM3PrecedentRetriever, PrecedentSearchResult
from ml.rag.statute_retriever import (
    BgeM3StatuteRetriever,
    StatuteSearchResult,
    load_bge_model,
)


class StatuteRetriever(Protocol):
    """계약서 검토에 필요한 법령 검색 최소 인터페이스다."""

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
    ) -> list[StatuteSearchResult]: ...


class PrecedentRetriever(Protocol):
    """계약서 검토에 필요한 판례 검색 최소 인터페이스다."""

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
    ) -> list[PrecedentSearchResult]: ...


class ContractReviewRagRetriever:
    """검색기 구현을 계약서 검토 그래프의 근거 인터페이스로 맞춘다."""

    DEFAULT_EVIDENCE_TOP_K = 3
    DEFAULT_MIN_SIMILARITY = 0.35
    # 판례 유사도는 법령보다 구조적으로 낮다(관련 판례가 0.27~0.43, 무관한 결과는 0.2 미만).
    # 그래서 법령과 같은 기준으로 섞지 않고 종류별로 따로 고른다.
    DEFAULT_PRECEDENT_EVIDENCE_TOP_K = 2
    DEFAULT_PRECEDENT_MIN_SIMILARITY = 0.2

    def __init__(
        self,
        *,
        statute_retriever: StatuteRetriever | None = None,
        precedent_retriever: PrecedentRetriever | None = None,
        statute_top_k: int = 3,
        precedent_top_k: int = 3,
        evidence_top_k: int = DEFAULT_EVIDENCE_TOP_K,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
        precedent_evidence_top_k: int = DEFAULT_PRECEDENT_EVIDENCE_TOP_K,
        precedent_min_similarity: float = DEFAULT_PRECEDENT_MIN_SIMILARITY,
    ) -> None:
        if min(statute_top_k, precedent_top_k, evidence_top_k, precedent_evidence_top_k) < 1:
            raise ValueError("검색 결과 수는 1 이상이어야 합니다.")
        if not -1 <= min_similarity <= 1 or not -1 <= precedent_min_similarity <= 1:
            raise ValueError("최소 유사도는 -1 이상 1 이하여야 합니다.")
        self._statute_retriever = statute_retriever
        self._precedent_retriever = precedent_retriever
        self._statute_top_k = statute_top_k
        self._precedent_top_k = precedent_top_k
        self._evidence_top_k = evidence_top_k
        self._min_similarity = min_similarity
        self._precedent_evidence_top_k = precedent_evidence_top_k
        self._precedent_min_similarity = precedent_min_similarity
        self._lock = RLock()

    def retrieve(
        self,
        queries: list[ContractReviewRetrievalQuery],
    ) -> list[ContractReviewEvidence]:
        """질의별 법령·판례 출처를 보존한 근거 목록을 반환한다."""

        with self._lock:
            evidence: list[ContractReviewEvidence] = []
            for query in queries:
                candidates: list[ContractReviewEvidence] = []
                if self._statute_retriever is not None:
                    candidates.extend(self._retrieve_statutes(query))
                if self._precedent_retriever is not None:
                    candidates.extend(self._retrieve_precedents(query))
                evidence.extend(self._select_relevant_evidence(candidates))
            return evidence

    def _select_relevant_evidence(
        self,
        candidates: list[ContractReviewEvidence],
    ) -> list[ContractReviewEvidence]:
        """법령과 판례를 종류별 기준·개수로 골라 항목별로 보존한다."""

        def top(source_type: str, minimum: float, limit: int) -> list[ContractReviewEvidence]:
            relevant = [
                candidate
                for candidate in candidates
                if candidate.source_type == source_type and candidate.similarity >= minimum
            ]
            return sorted(
                relevant,
                key=lambda candidate: (-candidate.similarity, candidate.rank, candidate.source_id),
            )[:limit]

        return top("statute", self._min_similarity, self._evidence_top_k) + top(
            "precedent", self._precedent_min_similarity, self._precedent_evidence_top_k
        )

    def _retrieve_statutes(
        self,
        query: ContractReviewRetrievalQuery,
    ) -> list[ContractReviewEvidence]:
        assert self._statute_retriever is not None
        return [
            ContractReviewEvidence(
                item_id=query.item_id,
                source_type="statute",
                source_id=result.chunk_id,
                title=" ".join(
                    part
                    for part in (result.law_name, result.article_label)
                    if part
                )
                or result.chunk_id,
                text=result.text,
                rank=result.rank,
                similarity=result.similarity,
                metadata={
                    "law_id": result.law_id,
                    "law_name": result.law_name,
                    "article_label": result.article_label,
                    "article_title": result.article_title,
                    "effective_date": result.effective_date,
                    "source_node_id": result.source_node_id,
                },
            )
            for result in self._statute_retriever.search(
                query.query,
                top_k=self._statute_top_k,
            )
        ]

    def _retrieve_precedents(
        self,
        query: ContractReviewRetrievalQuery,
    ) -> list[ContractReviewEvidence]:
        assert self._precedent_retriever is not None
        return [
            ContractReviewEvidence(
                item_id=query.item_id,
                source_type="precedent",
                source_id=result.precedent_id,
                title=result.case_name or result.case_no or result.precedent_id,
                text=result.text,
                rank=result.rank,
                similarity=result.similarity,
                metadata={
                    "case_no": result.case_no,
                    "case_name": result.case_name,
                    "court_name": result.court_name,
                    "decision_date": result.decision_date,
                    "decision_year": result.decision_year,
                    "case_type": result.case_type,
                    "judgment_type": result.judgment_type,
                },
            )
            for result in self._precedent_retriever.search(
                query.query,
                top_k=self._precedent_top_k,
            )
        ]


class SharedBgeM3ModelLoader:
    """법령과 판례 검색기가 같은 BGE-M3 인스턴스를 재사용하게 한다."""

    def __init__(self, loader: Callable[[Any, str], Any]) -> None:
        self._loader = loader
        self._model: Any | None = None
        self._lock = RLock()

    def __call__(self, model_cache: Any, device: str) -> Any:
        with self._lock:
            if self._model is None:
                self._model = self._loader(model_cache, device)
            return self._model


_SHARED_BGE_MODEL_LOADER = SharedBgeM3ModelLoader(load_bge_model)


def _create_rag_retriever_from_env(
    enabled_variable: str,
) -> ContractReviewRagRetriever | None:
    """명시적으로 활성화한 기능에서만 지연 로딩 RAG 검색기를 만든다."""

    enabled = os.getenv(enabled_variable, "false").lower()
    if enabled not in {"1", "true", "yes"}:
        return None

    return ContractReviewRagRetriever(
        statute_retriever=BgeM3StatuteRetriever(model_loader=_SHARED_BGE_MODEL_LOADER),
        precedent_retriever=BgeM3PrecedentRetriever(
            model_loader=_SHARED_BGE_MODEL_LOADER
        ),
    )


def create_contract_review_rag_retriever_from_env() -> ContractReviewRagRetriever | None:
    """계약서 검토용 RAG 검색기를 환경 설정에 따라 만든다."""

    return _create_rag_retriever_from_env("CONTRACT_REVIEW_RAG_ENABLED")


def create_special_clause_rag_retriever_from_env() -> ContractReviewRagRetriever | None:
    """특약 생성용 RAG 검색기를 환경 설정에 따라 만든다."""

    return _create_rag_retriever_from_env("SPECIAL_CLAUSE_RAG_ENABLED")
