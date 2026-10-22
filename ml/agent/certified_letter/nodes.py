# nodes.py
"""
Description: 내용증명 LangGraph의 결정적 상태 전이와 외부 포트 호출을 구현한다.
판례 검색 결과와 본문 생성 입력을 분리하고 재작성 횟수를 한 번으로 제한한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 내용증명 생성 단계와 조기 종료 규칙이 실행 가능한 노드로 없는 상태.
After:
    - 정규화, 범위 확인, 검색, 리랭크, 생성과 검증 노드를 조립할 수 있음.
"""

from __future__ import annotations

from dataclasses import dataclass

from ml.agent.certified_letter.normalizer import normalize_request
from ml.agent.certified_letter.ports import (
    DraftValidatorPort,
    LetterDraftGeneratorPort,
    PrecedentRerankerPort,
    PrecedentSearchPort,
    StatuteRerankerPort,
    StatuteSearchPort,
)
from ml.agent.certified_letter.state import (
    CertifiedLetterGraphState,
    WorkflowFailure,
)


SUPPORTED_SCOPE_TERMS = (
    "임대차",
    "임대인",
    "임차인",
    "집주인",
    "세입자",
    "전세",
    "월세",
    "보증금",
    "계약 종료",
    "계약 해지",
    "관리비",
    "수리",
    "하자",
)

ISSUE_KEYWORDS = {
    "임대차보증금 반환": ("보증금", "전세금", "반환"),
    "임대차계약 종료 또는 해지": ("종료", "해지", "갱신", "퇴거"),
    "임대인의 수선 의무": ("수리", "하자", "누수", "곰팡이"),
    "차임 또는 관리비": ("월세", "차임", "관리비", "연체"),
}

STATUTE_CANDIDATE_LIMIT = 20
STATUTE_RESULT_LIMIT = 5
PRECEDENT_CANDIDATE_LIMIT = 20
PRECEDENT_RESULT_LIMIT = 5


@dataclass(frozen=True)
class CertifiedLetterNodeDependencies:
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
        text = f"{request.purpose} {request.facts}"
        if any(term in text for term in SUPPORTED_SCOPE_TERMS):
            return {}
        return {
            "outcome": "out_of_scope",
            "message": (
                "현재 알파 버전은 임대차보증금, 계약 종료·해지, 수선, "
                "월세·관리비 관련 내용증명 초안을 지원합니다."
            ),
        }

    def check_required_information(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        request = state["normalized_request"]
        missing = []
        if not request.contract.lessor.name:
            missing.append("임대인 이름")
        if not request.contract.lessor.address:
            missing.append("임대인의 송달 주소")
        if not request.contract.lessee.name:
            missing.append("임차인 이름")
        if not request.contract.lessee.address:
            missing.append("임차인 주소")
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
        issues = tuple(
            issue
            for issue, keywords in ISSUE_KEYWORDS.items()
            if any(keyword in searchable_text for keyword in keywords)
        ) or ("임대차계약상 의무",)
        contract_label = (
            "전세" if request.contract.contract_type == "jeonse" else "월세"
        )
        contract = request.contract
        payment_context = f"보증금 {contract.payment.deposit}원"
        if contract.payment.monthly_rent is not None:
            payment_context += f" 월세 {contract.payment.monthly_rent}원"
        period_context = (
            f"계약기간 {contract.period.start_date.isoformat()}부터 "
            f"{contract.period.end_date.isoformat()}까지"
        )
        context = " ".join(
            (
                f"{contract_label} 임대차",
                period_context,
                payment_context,
                searchable_text,
            )
        ).strip()
        statute_queries = tuple(
            _limit_query(f"{issue} 관련 법령 {context}") for issue in issues
        )
        precedent_queries = tuple(
            _limit_query(f"{issue} 유사 판례 {context}") for issue in issues
        )
        return {
            "legal_issues": issues,
            "statute_queries": statute_queries,
            "precedent_queries": precedent_queries,
        }

    def retrieve_statutes(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        candidates = self.dependencies.statute_search.search(
            state["statute_queries"],
            candidate_limit=STATUTE_CANDIDATE_LIMIT,
        )
        return {"statute_candidates": tuple(candidates)}

    def rerank_statutes(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        query = " ".join(state["statute_queries"])
        selected = self.dependencies.statute_reranker.rerank(
            query,
            state.get("statute_candidates", ()),
            top_k=STATUTE_RESULT_LIMIT,
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
        return {"precedent_candidates": tuple(candidates)}

    def rerank_precedents(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        query = " ".join(state["precedent_queries"])
        selected = self.dependencies.precedent_reranker.rerank(
            query,
            state.get("precedent_candidates", ()),
            top_k=PRECEDENT_RESULT_LIMIT,
        )
        return {"selected_precedents": tuple(selected)}

    def check_evidence(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        if state.get("selected_statutes"):
            return {}
        return {
            "outcome": "insufficient_evidence",
            "message": (
                "내용증명 본문을 뒷받침할 충분한 법령 근거를 찾지 못했습니다. "
                "사실관계를 보완한 뒤 다시 시도해 주세요."
            ),
        }

    def generate_draft(
        self,
        state: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState:
        draft = self.dependencies.generator.generate(
            state["normalized_request"],
            state["selected_statutes"],
        )
        return {"draft": draft}

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
        repaired = self.dependencies.generator.repair(
            state["normalized_request"],
            state["selected_statutes"],
            state["draft"],
            state["validation_issues"],
        )
        return {
            "draft": repaired,
            "repair_attempts": state.get("repair_attempts", 0) + 1,
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
        return {"outcome": "draft_ready"}


def _redact_contract_pii(text: str, state: CertifiedLetterGraphState) -> str:
    contract = state["normalized_request"].contract
    redacted = text
    pii_values = (
        contract.lessor.name,
        contract.lessor.address,
        contract.lessor.detail_address,
        contract.lessor.phone_number,
        contract.lessee.name,
        contract.lessee.address,
        contract.lessee.detail_address,
        contract.lessee.phone_number,
        contract.property.address,
        contract.property.detail_address,
    )
    for value in pii_values:
        if value:
            redacted = redacted.replace(value, " ")
    return " ".join(redacted.split())


def _limit_query(value: str) -> str:
    return value[:2000].strip()
