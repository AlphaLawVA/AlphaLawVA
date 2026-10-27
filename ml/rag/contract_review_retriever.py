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

    def __init__(
        self,
        *,
        statute_retriever: StatuteRetriever | None = None,
        precedent_retriever: PrecedentRetriever | None = None,
        statute_top_k: int = 3,
        precedent_top_k: int = 3,
    ) -> None:
        if statute_top_k < 1 or precedent_top_k < 1:
            raise ValueError("검색 결과 수는 1 이상이어야 합니다.")
        self._statute_retriever = statute_retriever
        self._precedent_retriever = precedent_retriever
        self._statute_top_k = statute_top_k
        self._precedent_top_k = precedent_top_k
        self._lock = RLock()

    def retrieve(
        self,
        queries: list[ContractReviewRetrievalQuery],
    ) -> list[ContractReviewEvidence]:
        """질의별 법령·판례 출처를 보존한 근거 목록을 반환한다."""

        with self._lock:
            evidence: list[ContractReviewEvidence] = []
            for query in queries:
                if self._statute_retriever is not None:
                    evidence.extend(self._retrieve_statutes(query))
                if self._precedent_retriever is not None:
                    evidence.extend(self._retrieve_precedents(query))
            return evidence

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


def create_contract_review_rag_retriever_from_env() -> ContractReviewRagRetriever | None:
    """명시적으로 활성화한 환경에서만 지연 로딩 RAG 검색기를 만든다."""

    enabled = os.getenv("CONTRACT_REVIEW_RAG_ENABLED", "false").lower()
    if enabled not in {"1", "true", "yes"}:
        return None

    shared_model_loader = SharedBgeM3ModelLoader(load_bge_model)
    return ContractReviewRagRetriever(
        statute_retriever=BgeM3StatuteRetriever(model_loader=shared_model_loader),
        precedent_retriever=BgeM3PrecedentRetriever(model_loader=shared_model_loader),
    )
