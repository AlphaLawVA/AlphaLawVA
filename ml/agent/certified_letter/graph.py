# graph.py
"""
Description: 검증 가능한 내용증명 생성 LangGraph의 노드와 조건부 경로를 구성한다.
범위 밖·정보 부족·근거 부족을 조기 종료하고 초안 재작성을 한 번으로 제한한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 내용증명 생성 계획은 있지만 실행 가능한 상태 그래프가 없는 상태.
After:
    - 가짜 또는 실제 의존성을 주입해 동일한 생성 경로를 실행할 수 있음.
"""

from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph

from ml.agent.certified_letter.nodes import (
    CertifiedLetterNodeDependencies,
    CertifiedLetterNodes,
)
from ml.agent.certified_letter.state import CertifiedLetterGraphState


def build_certified_letter_graph(
    dependencies: CertifiedLetterNodeDependencies,
):
    nodes = CertifiedLetterNodes(dependencies)
    builder = StateGraph(CertifiedLetterGraphState)

    builder.add_node("normalize_input", nodes.normalize_input)
    builder.add_node("check_scope", nodes.check_scope)
    builder.add_node("check_required_information", nodes.check_required_information)
    builder.add_node("build_search_queries", nodes.build_search_queries)
    builder.add_node("retrieve_statutes", nodes.retrieve_statutes)
    builder.add_node("rerank_statutes", nodes.rerank_statutes)
    builder.add_node("retrieve_precedents", nodes.retrieve_precedents)
    builder.add_node("rerank_precedents", nodes.rerank_precedents)
    builder.add_node("check_evidence", nodes.check_evidence)
    builder.add_node("generate_draft", nodes.generate_draft)
    builder.add_node("validate_draft", nodes.validate_draft)
    builder.add_node("repair_draft", nodes.repair_draft)
    builder.add_node("mark_validation_failure", nodes.mark_validation_failure)
    builder.add_node("finalize_result", nodes.finalize_result)

    builder.add_edge(START, "normalize_input")
    builder.add_edge("normalize_input", "check_scope")
    builder.add_conditional_edges(
        "check_scope",
        _route_after_scope,
        {
            "continue": "check_required_information",
            "finish": "finalize_result",
        },
    )
    builder.add_conditional_edges(
        "check_required_information",
        _route_after_required_information,
        {
            "continue": "build_search_queries",
            "finish": "finalize_result",
        },
    )
    builder.add_edge("build_search_queries", "retrieve_statutes")
    builder.add_edge("retrieve_statutes", "rerank_statutes")
    builder.add_edge("rerank_statutes", "retrieve_precedents")
    builder.add_edge("retrieve_precedents", "rerank_precedents")
    builder.add_edge("rerank_precedents", "check_evidence")
    builder.add_conditional_edges(
        "check_evidence",
        _route_after_evidence,
        {"generate": "generate_draft", "finish": "finalize_result"},
    )
    builder.add_edge("generate_draft", "validate_draft")
    builder.add_conditional_edges(
        "validate_draft",
        _route_after_validation,
        {
            "finish": "finalize_result",
            "repair": "repair_draft",
            "fail": "mark_validation_failure",
        },
    )
    builder.add_edge("repair_draft", "validate_draft")
    builder.add_edge("mark_validation_failure", "finalize_result")
    builder.add_edge("finalize_result", END)
    return builder.compile()


def _route_after_scope(
    state: CertifiedLetterGraphState,
) -> Literal["continue", "finish"]:
    return (
        "finish"
        if state.get("outcome") in {"out_of_scope", "needs_information"}
        else "continue"
    )


def _route_after_required_information(
    state: CertifiedLetterGraphState,
) -> Literal["continue", "finish"]:
    return "finish" if state.get("outcome") == "needs_information" else "continue"


def _route_after_evidence(
    state: CertifiedLetterGraphState,
) -> Literal["generate", "finish"]:
    if state.get("outcome") == "insufficient_evidence":
        return "finish"
    return "generate"


def _route_after_validation(
    state: CertifiedLetterGraphState,
) -> Literal["finish", "repair", "fail"]:
    issues = state.get("validation_issues", ())
    if not issues:
        return "finish"
    if state.get("repair_attempts", 0) == 0 and all(
        issue.is_repairable for issue in issues
    ):
        return "repair"
    return "fail"
