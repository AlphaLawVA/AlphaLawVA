# nodes.py
"""
Description: 내용증명 LangGraph의 상태 전이와 분류·검색·생성 포트 호출을 구현한다.
판례 검색 결과와 본문 생성 입력을 분리하고 재작성 횟수를 한 번으로 제한한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 내용증명 생성 단계와 조기 종료 규칙이 실행 가능한 노드로 없는 상태.
After:
    - 정규화, 의미 기반 범위 분류, 검색, 리랭크, 생성과 검증 노드를 조립할 수 있음.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ml.agent.certified_letter.normalizer import normalize_request
from ml.agent.certified_letter.ports import (
    DraftValidatorPort,
    LetterDraftGeneratorPort,
    PrecedentRerankerPort,
    PrecedentSearchPort,
    ScopeClassifierPort,
    StatuteRerankerPort,
    StatuteSearchPort,
)
from ml.agent.certified_letter.scope import missing_information_labels
from ml.agent.certified_letter.state import (
    CertifiedLetterScopeDecision,
    CertifiedLetterGraphState,
    WorkflowFailure,
    resolve_document_parties,
)
from ml.agent.certified_letter.value_normalizer import (
    count_prompt_transformations,
    missing_fact_values,
    normalize_draft,
)
from ml.agent.certified_letter.prompts import build_generation_prompt, build_repair_prompt


STATUTE_CANDIDATE_LIMIT = 10
STATUTE_MIN_SIMILARITY = 0.35
STATUTE_RESULT_LIMIT = 3
PRECEDENT_CANDIDATE_LIMIT = 20
PRECEDENT_RESULT_LIMIT = 3


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CertifiedLetterNodeDependencies:
    scope_classifier: ScopeClassifierPort
    statute_search: StatuteSearchPort
    precedent_search: PrecedentSearchPort
    statute_reranker: StatuteRerankerPort
    precedent_reranker: PrecedentRerankerPort
    generator: LetterDraftGeneratorPort
    validator: DraftValidatorPort


class CertifiedLetterNodes:
    def __init__(self, dependencies: CertifiedLetterNodeDependencies) -> None:
        self.dependencies = dependencies

    def normalize_input(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        return {
            "normalized_request": normalize_request(state["request"]),
            "repair_attempts": 0,
            "trace": {"workflow_version": "certified_letter_graph_v0_1"},
        }

    def check_scope(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        request = state["normalized_request"]
        classification = self.dependencies.scope_classifier.classify_scope(
            request.sender_role,
            request.purpose,
            request.facts,
        )
        decision = classification.decision
        if decision is CertifiedLetterScopeDecision.IN_SCOPE:
            return {"scope_decision": decision.value}
        if decision is CertifiedLetterScopeDecision.NEEDS_INFORMATION:
            return {
                "scope_decision": decision.value,
                "outcome": decision.value,
                "missing_information": missing_information_labels(
                    classification.missing_fields
                ),
                "message": (
                    "내용증명 초안을 작성하려면 요청 목적이나 사실관계를 "
                    "조금 더 구체적으로 알려주세요."
                ),
            }
        return {
            "scope_decision": decision.value,
            "outcome": decision.value,
            "message": (
                "현재 알파 버전은 부동산 임대차와 매매계약의 이행, 종료, "
                "금전 지급, 인도·수선 관련 내용증명 초안을 지원합니다."
            ),
        }

    def check_required_information(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        request = state["normalized_request"]
        missing = []
        try:
            sender, recipient = resolve_document_parties(request)
        except ValueError:
            return {
                "missing_information": ("계약 당사자 정보",),
                "outcome": "needs_information",
                "message": "내용증명 초안을 작성하려면 계약 당사자 정보가 필요합니다.",
            }
        if not sender.name:
            missing.append("발신인 이름")
        if not recipient.name:
            missing.append("수신인 이름")
        if not missing:
            return {"missing_information": ()}
        return {
            "missing_information": tuple(missing),
            "outcome": "needs_information",
            "message": "내용증명 초안을 작성하려면 추가 정보가 필요합니다.",
        }

    def build_search_queries(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        request = state["normalized_request"]
        searchable_text = _redact_contract_pii(
            f"{request.purpose} {request.facts}",
            state,
        )
        contract_label = {
            "jeonse": "전세 임대차",
            "monthly_rent": "월세 임대차",
            "sale": "부동산 매매",
        }.get(request.contract.contract_type, "부동산 계약")
        context = " ".join(
            (
                contract_label,
                searchable_text,
            )
        ).strip()
        query = _limit_query(context)
        return {
            "legal_issues": (),
            "statute_queries": (query,),
            "precedent_queries": (query,),
        }
    def retrieve_statutes(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        candidates = self.dependencies.statute_search.search(
            state["statute_queries"],
            candidate_limit=STATUTE_CANDIDATE_LIMIT,
        )
        logger.info(
            "내용증명 법령 검색 후보: request_id=%s legal_issues=%s candidates=%s",
            state.get("request_id", "unknown"),
            state.get("legal_issues", ()),
            _statute_trace_entries(candidates),
        )
        return {"statute_candidates": tuple(candidates)}

    def rerank_statutes(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        query = " ".join(state["statute_queries"])
        candidates = tuple(
            candidate
            for candidate in state.get("statute_candidates", ())
            if candidate.similarity >= STATUTE_MIN_SIMILARITY
        )
        try:
            selected = self.dependencies.statute_reranker.rerank(
                query,
                candidates,
                top_k=STATUTE_RESULT_LIMIT,
            )
        except Exception:  # noqa: BLE001 - 리랭커 장애가 내용증명 생성을 막지 않게 한다.
            logger.warning(
                "내용증명 법령 리랭커 실패, dense 유사도 순서를 사용합니다.",
                exc_info=True,
            )
            selected = sorted(
                candidates,
                key=lambda candidate: candidate.similarity,
                reverse=True,
            )[:STATUTE_RESULT_LIMIT]
        logger.info(
            "내용증명 법령 리랭킹 결과: request_id=%s min_similarity=%s "
            "eligible_candidates=%s selected=%s",
            state.get("request_id", "unknown"),
            STATUTE_MIN_SIMILARITY,
            _statute_trace_entries(candidates),
            _statute_trace_entries(selected),
        )
        return {"selected_statutes": tuple(selected)}

    def retrieve_precedents(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        candidates = self.dependencies.precedent_search.search(
            state["precedent_queries"],
            candidate_limit=PRECEDENT_CANDIDATE_LIMIT,
        )
        logger.info(
            "내용증명 판례 검색 후보: request_id=%s candidates=%s",
            state.get("request_id", "unknown"),
            _precedent_trace_entries(candidates),
        )
        return {"precedent_candidates": tuple(candidates)}

    def rerank_precedents(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        query = " ".join(state["precedent_queries"])
        candidates = tuple(state.get("precedent_candidates", ()))
        try:
            selected = self.dependencies.precedent_reranker.rerank(
                query,
                candidates,
                top_k=PRECEDENT_RESULT_LIMIT,
            )
        except Exception:  # noqa: BLE001 - 리랭커 장애가 내용증명 생성을 막지 않게 한다.
            logger.warning(
                "내용증명 판례 리랭커 실패, dense 유사도 순서를 사용합니다.",
                exc_info=True,
            )
            selected = sorted(
                candidates,
                key=lambda candidate: candidate.similarity,
                reverse=True,
            )[:PRECEDENT_RESULT_LIMIT]
        logger.info(
            "내용증명 판례 리랭킹 결과: request_id=%s candidates=%s selected=%s",
            state.get("request_id", "unknown"),
            _precedent_trace_entries(candidates),
            _precedent_trace_entries(selected),
        )
        return {"selected_precedents": tuple(selected)}

    def check_evidence(
        self,
        _state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        return {}

    def generate_draft(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        request = state["normalized_request"]
        input_transformations = count_prompt_transformations(
            build_generation_prompt(request, state["selected_statutes"])
        )
        generated = self.dependencies.generator.generate(
            request,
            state["selected_statutes"],
        )
        normalized = normalize_draft(generated)
        trace = dict(state.get("trace", {}))
        trace.update(
            {
                "input_normalization_count": input_transformations,
                "output_normalization_count": normalized.transformations,
            }
        )
        return {"draft": normalized.draft, "trace": trace}

    def validate_draft(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        issues = self.dependencies.validator.validate(
            state["normalized_request"],
            state["selected_statutes"],
            state["draft"],
        )
        return {"validation_issues": tuple(issues)}

    def repair_draft(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        request = state["normalized_request"]
        input_transformations = count_prompt_transformations(
            build_repair_prompt(
                request,
                state["selected_statutes"],
                state["draft"],
                state["validation_issues"],
            )
        )
        repaired = self.dependencies.generator.repair(
            request,
            state["selected_statutes"],
            state["draft"],
            state["validation_issues"],
        )
        normalized = normalize_draft(repaired)
        trace = dict(state.get("trace", {}))
        trace["input_normalization_count"] = (
            int(trace.get("input_normalization_count", 0)) + input_transformations
        )
        trace["output_normalization_count"] = (
            int(trace.get("output_normalization_count", 0))
            + normalized.transformations
        )
        return {
            "draft": normalized.draft,
            "repair_attempts": state.get("repair_attempts", 0) + 1,
            "trace": trace,
        }

    def mark_validation_failure(
        self,
        _state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        return {
            "failure": WorkflowFailure(
                code="certified_letter_validation_failed",
                message="생성된 초안이 사실·법령 검증을 통과하지 못했습니다.",
            )
        }

    def finalize_result(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        if "failure" in state or "outcome" in state:
            return {}
        request = state["normalized_request"]
        missing_values = missing_fact_values(
            facts=request.facts,
            body=state["draft"].body,
        )
        cited_statutes = _cited_statute_trace_entries(
            state["draft"].body,
            state.get("selected_statutes", ()),
        )
        logger.info(
            "내용증명 법령 인용 결과: request_id=%s selected=%s cited=%s",
            state.get("request_id", "unknown"),
            _statute_trace_entries(state.get("selected_statutes", ())),
            cited_statutes,
        )
        logger.info(
            "내용증명 값 정규화 완료: request_id=%s input_transformations=%s "
            "output_transformations=%s missing_fact_values=%s missing_values=%s",
            state.get("request_id", "unknown"),
            state.get("trace", {}).get("input_normalization_count", 0),
            state.get("trace", {}).get("output_normalization_count", 0),
            bool(missing_values),
            missing_values,
        )
        return {"outcome": "draft_ready"}


def _redact_contract_pii(text: str, state: CertifiedLetterGraphState) -> str:
    contract = state["normalized_request"].contract
    redacted = text
    parties = (
        contract.lessor,
        contract.lessee,
        contract.seller,
        contract.buyer,
    )
    pii_values = [
        value
        for party in parties
        if party is not None
        for value in (
            party.name,
            party.address,
            party.detail_address,
            party.phone_number,
        )
    ]
    pii_values.extend((contract.property.address, contract.property.detail_address))
    for value in pii_values:
        if value:
            redacted = redacted.replace(value, " ")
    return " ".join(redacted.split())


def _limit_query(value: str) -> str:
    return value[:2000].strip()


def _statute_trace_entries(statutes: object) -> str:
    """법령 원문 없이 검색·리랭킹 결과를 추적 가능한 메타데이터로 남긴다."""

    return json.dumps(
        [
            {
                "statute_id": item.statute_id,
                "law_name": item.law_name,
                "article_label": item.article_label,
                "similarity": round(item.similarity, 4),
            }
            for item in statutes
        ],
        ensure_ascii=False,
        sort_keys=True,
    )


def _precedent_trace_entries(precedents: object) -> str:
    """판례 원문 없이 검색·리랭킹 결과를 추적 가능한 메타데이터로 남긴다."""

    return json.dumps(
        [
            {
                "precedent_id": item.precedent_id,
                "case_number": item.case_number,
                "case_name": item.case_name,
                "court_name": item.court_name,
                "decision_date": item.decision_date,
                "similarity": round(item.similarity, 4),
            }
            for item in precedents
        ],
        ensure_ascii=False,
        sort_keys=True,
    )


def _cited_statute_trace_entries(
    body: str,
    selected_statutes: object,
) -> str:
    """선정 법령 중 본문에 정형 인용된 항목만 로그 메타데이터로 만든다."""

    from ml.agent.certified_letter.validators import extract_statute_citations

    cited = extract_statute_citations(body)
    selected = [
        item
        for item in selected_statutes
        if (item.law_name.replace(" ", ""), item.article_label.replace(" ", ""))
        in cited
    ]
    return _statute_trace_entries(selected)
