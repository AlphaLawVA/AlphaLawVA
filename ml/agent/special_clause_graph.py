"""
Description: 자연어 특약 요청을 비식별 RAG 질의와 근거 기반 생성 입력으로 변환한다.
생성기는 근거 ID가 연결된 특약 초안 또는 추가 확인 질문만 반환할 수 있다.
Author: ooheunsu
Date: 2026-10-31
Before:
    - 특약 생성 API가 자연어 요청을 생성 대기 상태로만 반환하는 상태.
After:
    - 비식별 검색 질의, 근거 검색, 구조화 생성 단계를 LangGraph로 연결함.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, TypedDict

from langgraph.graph import END, StateGraph

from ml.agent.contract_review_graph import (
    ContractReviewEvidence,
    ContractReviewEvidenceRetriever,
    ContractReviewRetrievalQuery,
)


@dataclass(frozen=True)
class SpecialClauseGeneration:
    """근거가 연결된 특약 초안 또는 사용자 추가 입력 요청이다."""

    item_id: str
    status: str
    draft_text: str | None
    needs_input: list[str]
    evidence_source_ids: list[str]


class SpecialClauseGenerator(Protocol):
    """특약 요청과 검색 근거를 사용자 확인용 초안으로 변환하는 경계다."""

    def generate(
        self,
        queries: list[ContractReviewRetrievalQuery],
        evidence: list[ContractReviewEvidence],
    ) -> list[SpecialClauseGeneration]: ...


@dataclass(frozen=True)
class SpecialClauseGraphResult:
    """특약 생성 그래프의 검색 및 생성 결과다."""

    queries: list[ContractReviewRetrievalQuery]
    evidence: list[ContractReviewEvidence]
    generations: list[SpecialClauseGeneration]
    generation_unavailable: bool


class SpecialClauseGraphState(TypedDict):
    """특약 생성 LangGraph 노드 간 전달 상태다."""

    requests: list[str]
    retrieval_queries: list[ContractReviewRetrievalQuery]
    evidence_retriever: ContractReviewEvidenceRetriever | None
    evidence: list[ContractReviewEvidence]
    generator: SpecialClauseGenerator | None
    generations: list[SpecialClauseGeneration]
    generation_unavailable: bool


class SpecialClauseGraph:
    """특약 요청을 검색·생성 경계로 전달하는 최소 상태 그래프다."""

    def __init__(self) -> None:
        builder = StateGraph(SpecialClauseGraphState)
        builder.add_node("build_retrieval_queries", self._build_retrieval_queries)
        builder.add_node("retrieve_evidence", self._retrieve_evidence)
        builder.add_node("generate_drafts", self._generate_drafts)
        builder.set_entry_point("build_retrieval_queries")
        builder.add_edge("build_retrieval_queries", "retrieve_evidence")
        builder.add_edge("retrieve_evidence", "generate_drafts")
        builder.add_edge("generate_drafts", END)
        self._graph = builder.compile()

    def prepare(
        self,
        requests: list[str],
        *,
        evidence_retriever: ContractReviewEvidenceRetriever | None = None,
        generator: SpecialClauseGenerator | None = None,
    ) -> SpecialClauseGraphResult:
        """요청 검증 후 검색·생성 결과를 사용자 확인용으로 구성한다."""

        result = self._graph.invoke(
            {
                "requests": requests,
                "retrieval_queries": [],
                "evidence_retriever": evidence_retriever,
                "evidence": [],
                "generator": generator,
                "generations": [],
                "generation_unavailable": False,
            }
        )
        return SpecialClauseGraphResult(
            queries=result["retrieval_queries"],
            evidence=result["evidence"],
            generations=result["generations"],
            generation_unavailable=result["generation_unavailable"],
        )

    @staticmethod
    def _build_retrieval_queries(
        state: SpecialClauseGraphState,
    ) -> dict[str, list[ContractReviewRetrievalQuery]]:
        return {
            "retrieval_queries": [
                ContractReviewRetrievalQuery(
                    item_id=f"request-{index}",
                    category="special_clause_request",
                    query=(
                        "부동산 계약 특약 작성 요청: "
                        f"{SpecialClauseGraph._redact_request(request)}"
                    ),
                )
                for index, request in enumerate(state["requests"], start=1)
            ]
        }

    @staticmethod
    def _retrieve_evidence(
        state: SpecialClauseGraphState,
    ) -> dict[str, list[ContractReviewEvidence]]:
        retriever = state["evidence_retriever"]
        if retriever is None:
            return {"evidence": []}
        return {"evidence": retriever.retrieve(state["retrieval_queries"])}

    @staticmethod
    def _generate_drafts(
        state: SpecialClauseGraphState,
    ) -> dict[str, list[SpecialClauseGeneration] | bool]:
        generator = state["generator"]
        if generator is None or not state["evidence"]:
            return {"generations": [], "generation_unavailable": False}
        try:
            generations = generator.generate(
                state["retrieval_queries"],
                state["evidence"],
            )
        except (RuntimeError, ValueError):
            return {"generations": [], "generation_unavailable": True}
        return {"generations": generations, "generation_unavailable": False}

    @staticmethod
    def _redact_request(request: str) -> str:
        redacted = re.sub(r"\d{6}-\d{7}", "식별번호", request)
        return re.sub(
            r"(?<!\d)\d{2,4}(?:-\d{2,6}){1,3}(?!\d)",
            "연락처 또는 계좌번호",
            redacted,
        )
