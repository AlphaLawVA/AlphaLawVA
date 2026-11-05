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
from dataclasses import dataclass, field
from typing import Protocol, TypedDict

from langgraph.graph import END, StateGraph

from ml.agent.output_validation import find_output_issues
from ml.agent.special_clause_scope import ScopeDecision, classify_request_scope
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
    title: str | None
    reason: str | None
    strategy: str | None
    needs_input: list[str]
    evidence_source_ids: list[str]


class SpecialClauseGenerator(Protocol):
    """특약 요청과 검색 근거를 사용자 확인용 초안으로 변환하는 경계다."""

    def generate(
        self,
        queries: list[ContractReviewRetrievalQuery],
        evidence: list[ContractReviewEvidence],
        feedback: dict[str, list[str]] | None = None,
    ) -> list[SpecialClauseGeneration]: ...


class SpecialClauseScopeClassifier(Protocol):
    """특약 요청 목록을 범위 안·정보 부족·범위 밖으로 판정하는 경계다."""

    def classify(self, requests: list[str]) -> list[str]: ...


@dataclass(frozen=True)
class SpecialClauseGraphResult:
    """특약 생성 그래프의 검색 및 생성 결과다."""

    queries: list[ContractReviewRetrievalQuery]
    evidence: list[ContractReviewEvidence]
    generations: list[SpecialClauseGeneration]
    generation_unavailable: bool
    validation_dropped_item_ids: list[str] = field(default_factory=list)
    scope_decisions: dict[str, str] = field(default_factory=dict)


class SpecialClauseGraphState(TypedDict):
    """특약 생성 LangGraph 노드 간 전달 상태다."""

    requests: list[str]
    scope_classifier: SpecialClauseScopeClassifier | None
    scope_decisions: dict[str, str]
    retrieval_queries: list[ContractReviewRetrievalQuery]
    evidence_retriever: ContractReviewEvidenceRetriever | None
    evidence: list[ContractReviewEvidence]
    generator: SpecialClauseGenerator | None
    generations: list[SpecialClauseGeneration]
    generation_unavailable: bool
    generation_issues: dict[str, list[str]]
    generation_repair_attempts: int
    validation_dropped_item_ids: list[str]


class SpecialClauseGraph:
    """특약 요청을 검색·생성 경계로 전달하는 최소 상태 그래프다."""

    def __init__(self) -> None:
        builder = StateGraph(SpecialClauseGraphState)
        builder.add_node("check_scope", self._check_scope)
        builder.add_node("build_retrieval_queries", self._build_retrieval_queries)
        builder.add_node("retrieve_evidence", self._retrieve_evidence)
        builder.add_node("generate_drafts", self._generate_drafts)
        builder.add_node("validate_generations", self._validate_generations)
        builder.add_node("repair_generations", self._repair_generations)
        builder.add_node("drop_invalid_generations", self._drop_invalid_generations)
        builder.set_entry_point("check_scope")
        builder.add_conditional_edges(
            "check_scope",
            self._route_after_scope,
            {"continue": "build_retrieval_queries", "finish": END},
        )
        builder.add_edge("build_retrieval_queries", "retrieve_evidence")
        builder.add_edge("retrieve_evidence", "generate_drafts")
        builder.add_edge("generate_drafts", "validate_generations")
        builder.add_conditional_edges(
            "validate_generations",
            self._route_after_validation,
            {
                "finish": END,
                "repair": "repair_generations",
                "drop": "drop_invalid_generations",
            },
        )
        builder.add_edge("repair_generations", "validate_generations")
        builder.add_edge("drop_invalid_generations", END)
        self._graph = builder.compile()

    def prepare(
        self,
        requests: list[str],
        *,
        evidence_retriever: ContractReviewEvidenceRetriever | None = None,
        generator: SpecialClauseGenerator | None = None,
        scope_classifier: SpecialClauseScopeClassifier | None = None,
    ) -> SpecialClauseGraphResult:
        """요청 검증 후 검색·생성 결과를 사용자 확인용으로 구성한다."""

        result = self._graph.invoke(
            {
                "requests": requests,
                "scope_classifier": scope_classifier,
                "scope_decisions": {},
                "retrieval_queries": [],
                "evidence_retriever": evidence_retriever,
                "evidence": [],
                "generator": generator,
                "generations": [],
                "generation_unavailable": False,
                "generation_issues": {},
                "generation_repair_attempts": 0,
                "validation_dropped_item_ids": [],
            }
        )
        return SpecialClauseGraphResult(
            queries=result["retrieval_queries"],
            evidence=result["evidence"],
            generations=result["generations"],
            generation_unavailable=result["generation_unavailable"],
            validation_dropped_item_ids=result["validation_dropped_item_ids"],
            scope_decisions=result["scope_decisions"],
        )

    @staticmethod
    def _check_scope(state: SpecialClauseGraphState) -> dict[str, dict[str, str]]:
        """요청마다 범위 안·정보 부족·범위 밖을 분류한다.

        너무 짧은 요청은 모델을 부르지 않고 정보 부족으로 본다. 나머지는 모델이 판정하고,
        모델을 쓸 수 없거나 응답 형식이 어긋나면 키워드 규칙으로 대신 판정한다.
        """

        requests = state["requests"]
        decisions = {
            f"request-{index}": classify_request_scope(request)
            for index, request in enumerate(requests, start=1)
        }
        keyword_decisions = {key: value.value for key, value in decisions.items()}
        classifier = state["scope_classifier"]
        if classifier is None:
            return {"scope_decisions": keyword_decisions}

        pending = [
            (index, request)
            for index, request in enumerate(requests, start=1)
            if decisions[f"request-{index}"] != ScopeDecision.NEEDS_INFORMATION
        ]
        if not pending:
            return {"scope_decisions": keyword_decisions}
        try:
            answers = classifier.classify(
                [SpecialClauseGraph._redact_request(request) for _, request in pending]
            )
        except (RuntimeError, ValueError):
            return {"scope_decisions": keyword_decisions}
        if len(answers) != len(pending):
            return {"scope_decisions": keyword_decisions}
        merged = dict(keyword_decisions)
        for (index, _), answer in zip(pending, answers):
            merged[f"request-{index}"] = ScopeDecision(answer).value
        return {"scope_decisions": merged}

    @staticmethod
    def _route_after_scope(state: SpecialClauseGraphState) -> str:
        in_scope = ScopeDecision.IN_SCOPE.value
        return (
            "continue"
            if any(value == in_scope for value in state["scope_decisions"].values())
            else "finish"
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
                if state["scope_decisions"].get(f"request-{index}")
                == ScopeDecision.IN_SCOPE.value
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
    def _validate_generations(
        state: SpecialClauseGraphState,
    ) -> dict[str, dict[str, list[str]]]:
        """생성된 초안과 질문에서 차단 대상 문제(법적 단정, 빈칸, 근거 없는 수치)를 찾는다."""

        queries = {query.item_id: query for query in state["retrieval_queries"]}
        evidence: dict[str, dict[str, str]] = {}
        for entry in state["evidence"]:
            evidence.setdefault(entry.item_id, {})[entry.source_id] = (
                entry.title + "\n" + entry.text
            )
        issues: dict[str, list[str]] = {}
        for generation in state["generations"]:
            query = queries.get(generation.item_id)
            if query is None:
                continue
            found = find_output_issues(
                kind="clause",
                item_id=generation.item_id,
                query=query.query,
                category=query.category,
                evidence=evidence.get(generation.item_id, {}),
                item={
                    "status": generation.status,
                    "draft_text": generation.draft_text or "",
                    "title": generation.title or "",
                    "reason": generation.reason or "",
                    "strategy": generation.strategy or "",
                    "needs_input": generation.needs_input,
                    "evidence_source_ids": generation.evidence_source_ids,
                },
            )
            if found:
                issues[generation.item_id] = [issue.message for issue in found]
        return {"generation_issues": issues}

    @staticmethod
    def _route_after_validation(state: SpecialClauseGraphState) -> str:
        if not state["generation_issues"]:
            return "finish"
        if state["generator"] is not None and state["generation_repair_attempts"] == 0:
            return "repair"
        return "drop"

    @staticmethod
    def _repair_generations(
        state: SpecialClauseGraphState,
    ) -> dict[str, list[SpecialClauseGeneration] | int]:
        """문제가 있는 항목만 안내 문구와 함께 모델에게 한 번 다시 쓰게 한다."""

        issues = state["generation_issues"]
        generator = state["generator"]
        queries = [q for q in state["retrieval_queries"] if q.item_id in issues]
        evidence = [e for e in state["evidence"] if e.item_id in issues]
        try:
            repaired = generator.generate(queries, evidence, feedback=issues)
        except (RuntimeError, ValueError):
            repaired = []
        replacements = {item.item_id: item for item in repaired if item.item_id in issues}
        merged = [
            replacements.get(generation.item_id, generation)
            for generation in state["generations"]
        ]
        return {
            "generations": merged,
            "generation_repair_attempts": state["generation_repair_attempts"] + 1,
        }

    @staticmethod
    def _drop_invalid_generations(
        state: SpecialClauseGraphState,
    ) -> dict[str, list[SpecialClauseGeneration] | list[str]]:
        """재작성 후에도 문제가 남은 항목은 사용자에게 보내지 않고 제외한다."""

        issues = state["generation_issues"]
        kept = [g for g in state["generations"] if g.item_id not in issues]
        dropped = [g.item_id for g in state["generations"] if g.item_id in issues]
        return {"generations": kept, "validation_dropped_item_ids": dropped}

    @staticmethod
    def _redact_request(request: str) -> str:
        redacted = re.sub(r"\d{6}-\d{7}", "식별번호", request)
        return re.sub(
            r"(?<!\d)\d{2,4}(?:-\d{2,6}){1,3}(?!\d)",
            "연락처 또는 계좌번호",
            redacted,
        )
