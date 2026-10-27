# contract_review_graph.py
"""
Description: 확정 계약서와 등본을 규칙으로 사전검증하고 RAG 검색 질의를 구성한다.
이 그래프는 법률 결론을 생성하지 않으며 이후 검색·생성 노드의 안전한 입력을 만든다.
Author: ooheunsu
Date: 2026-10-27
Before:
    - 계약서 검토 API가 특약과 등본 항목을 검색 대기 상태로만 반환하는 상태.
After:
    - 주소·소유자·지급금·등기 확인 규칙과 비식별 검색 질의를 상태 그래프로 구성함.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, TypedDict

from langgraph.graph import END, StateGraph

from api.models.contracts import ContractReviewItem, ReviewItemStatus
from api.models.contract_snapshots import ConfirmedContractSnapshot, PartyRole
from api.models.registry_snapshots import ConfirmedRegistrySnapshot, RegistryEntryStatus


@dataclass(frozen=True)
class ContractReviewRetrievalQuery:
    """후속 법령·판례 검색 노드에 전달할 비식별 질의다."""

    item_id: str
    category: str
    query: str


@dataclass(frozen=True)
class ContractReviewEvidence:
    """검색된 근거의 원문과 출처 메타데이터를 항목별로 보존한다."""

    item_id: str
    source_type: str
    source_id: str
    title: str
    text: str
    rank: int
    similarity: float
    metadata: dict[str, str | int | float | None]


class ContractReviewEvidenceRetriever(Protocol):
    """계약서 검토 질의를 법령·판례 근거로 변환하는 검색 경계다."""

    def retrieve(
        self,
        queries: list[ContractReviewRetrievalQuery],
    ) -> list[ContractReviewEvidence]: ...


@dataclass(frozen=True)
class ContractReviewPrecheckResult:
    """규칙 기반 검증과 검색 질의 구성 결과다."""

    items: list[ContractReviewItem]
    retrieval_queries: list[ContractReviewRetrievalQuery]
    evidence: list[ContractReviewEvidence]


class ContractReviewGraphState(TypedDict):
    """계약서 검토 초기 그래프에서 노드 간 전달하는 상태다."""

    contract_snapshot: ConfirmedContractSnapshot
    registry_snapshot: ConfirmedRegistrySnapshot | None
    precheck_items: list[ContractReviewItem]
    retrieval_queries: list[ContractReviewRetrievalQuery]
    evidence_retriever: ContractReviewEvidenceRetriever | None
    evidence: list[ContractReviewEvidence]


class ContractReviewGraph:
    """규칙 검증 후 RAG 입력을 준비하는 계약서 검토 LangGraph다."""

    def __init__(self) -> None:
        builder = StateGraph(ContractReviewGraphState)
        builder.add_node("run_prechecks", self._run_prechecks)
        builder.add_node("build_retrieval_queries", self._build_retrieval_queries)
        builder.add_node("retrieve_evidence", self._retrieve_evidence)
        builder.set_entry_point("run_prechecks")
        builder.add_edge("run_prechecks", "build_retrieval_queries")
        builder.add_edge("build_retrieval_queries", "retrieve_evidence")
        builder.add_edge("retrieve_evidence", END)
        self._graph = builder.compile()

    def prepare(
        self,
        contract_snapshot: ConfirmedContractSnapshot,
        registry_snapshot: ConfirmedRegistrySnapshot | None,
        *,
        evidence_retriever: ContractReviewEvidenceRetriever | None = None,
    ) -> ContractReviewPrecheckResult:
        """확정 스냅샷을 사전검증하고 후속 검색 질의를 만든다."""

        result = self._graph.invoke(
            {
                "contract_snapshot": contract_snapshot,
                "registry_snapshot": registry_snapshot,
                "precheck_items": [],
                "retrieval_queries": [],
                "evidence_retriever": evidence_retriever,
                "evidence": [],
            }
        )
        return ContractReviewPrecheckResult(
            items=result["precheck_items"],
            retrieval_queries=result["retrieval_queries"],
            evidence=result["evidence"],
        )

    @staticmethod
    def _run_prechecks(
        state: ContractReviewGraphState,
    ) -> dict[str, list[ContractReviewItem]]:
        contract = state["contract_snapshot"].contract
        registry = state["registry_snapshot"]
        items: list[ContractReviewItem] = []

        if registry is None:
            items.append(
                ContractReviewItem(
                    item_id="registry_document",
                    category="registry_field",
                    label="등기사항전부증명서",
                    status=ReviewItemStatus.NEEDS_INPUT,
                    message=(
                        "소유자와 권리부담을 교차검증하려면 사용자 확인 등본이 "
                        "필요합니다."
                    ),
                )
            )
        else:
            ContractReviewGraph._append_address_check(contract, registry, items)
            ContractReviewGraph._append_owner_check(contract, registry, items)

        ContractReviewGraph._append_payment_check(contract, items)
        ContractReviewGraph._append_registry_disclosure_check(contract, items)
        ContractReviewGraph._append_sale_transfer_check(contract, items)
        return {"precheck_items": items}

    @staticmethod
    def _build_retrieval_queries(
        state: ContractReviewGraphState,
    ) -> dict[str, list[ContractReviewRetrievalQuery]]:
        contract = state["contract_snapshot"].contract
        registry = state["registry_snapshot"]
        queries: list[ContractReviewRetrievalQuery] = []

        for item in state["precheck_items"]:
            query = ContractReviewGraph._query_for_precheck(
                contract.contract_type.value,
                item.item_id,
            )
            if query is not None:
                queries.append(
                    ContractReviewRetrievalQuery(
                        item_id=item.item_id,
                        category=item.category,
                        query=query,
                    )
                )

        for term in contract.special_terms:
            queries.append(
                ContractReviewRetrievalQuery(
                    item_id=term.term_id,
                    category="special_term",
                    query=(
                        f"{contract.contract_type.value} 계약 특약 검토: "
                        f"{ContractReviewGraph._redact_contract_text(term.original_text, contract)}"
                    ),
                )
            )

        if registry is not None:
            for encumbrance in registry.encumbrances:
                if encumbrance.status == RegistryEntryStatus.ACTIVE:
                    queries.append(
                        ContractReviewRetrievalQuery(
                            item_id=f"registry_{encumbrance.rank_number}",
                            category="registry_field",
                            query=(
                                f"{contract.contract_type.value} 계약에서 "
                                f"{encumbrance.registration_purpose}이 있는 "
                                "부동산의 확인 사항"
                            ),
                        )
                    )
        return {"retrieval_queries": queries}

    @staticmethod
    def _retrieve_evidence(
        state: ContractReviewGraphState,
    ) -> dict[str, list[ContractReviewEvidence]]:
        retriever = state["evidence_retriever"]
        if retriever is None:
            return {"evidence": []}
        return {"evidence": retriever.retrieve(state["retrieval_queries"])}

    @staticmethod
    def _append_address_check(contract, registry, items: list[ContractReviewItem]) -> None:
        if ContractReviewGraph._normalize_text(
            contract.property.address
        ) == ContractReviewGraph._normalize_text(registry.address):
            return
        items.append(
            ContractReviewItem(
                item_id="property_address_match",
                category="contract_registry_crosscheck",
                label="계약서·등본 목적물 주소",
                status=ReviewItemStatus.NEEDS_INPUT,
                message=(
                    "계약서와 등본의 주소 표기가 일치하지 않습니다. "
                    "동일 목적물인지 원문을 대조해 확인해 주세요."
                ),
            )
        )

    @staticmethod
    def _append_owner_check(contract, registry, items: list[ContractReviewItem]) -> None:
        owner_role = (
            PartyRole.SELLER
            if contract.contract_type.value == "sale"
            else PartyRole.LESSOR
        )
        owner_party = next(
            (party for party in contract.parties if party.role == owner_role),
            None,
        )
        active_owners = [
            owner
            for owner in registry.owners
            if owner.status == RegistryEntryStatus.ACTIVE
        ]
        if owner_party is None or not active_owners:
            return
        if any("*" in owner.name for owner in active_owners):
            items.append(
                ContractReviewItem(
                    item_id="registered_owner_match",
                    category="contract_registry_crosscheck",
                    label="계약 상대방·등본 소유자",
                    status=ReviewItemStatus.NEEDS_INPUT,
                    message=(
                        "등본 소유자 이름이 마스킹되어 자동 비교할 수 없습니다. "
                        "원본 등본과 계약 당사자를 직접 확인해 주세요."
                    ),
                )
            )
            return
        normalized_party = ContractReviewGraph._normalize_text(owner_party.name)
        if not any(
            ContractReviewGraph._normalize_text(owner.name) == normalized_party
            for owner in active_owners
        ):
            items.append(
                ContractReviewItem(
                    item_id="registered_owner_match",
                    category="contract_registry_crosscheck",
                    label="계약 상대방·등본 소유자",
                    status=ReviewItemStatus.NEEDS_INPUT,
                    message=(
                        "계약서의 임대인 또는 매도인과 등본의 현재 소유자 표기가 "
                        "다릅니다. 계약 권한을 확인해 주세요."
                    ),
                )
            )

    @staticmethod
    def _append_payment_check(contract, items: list[ContractReviewItem]) -> None:
        total = (
            contract.sale_price.amount
            if contract.contract_type.value == "sale"
            else contract.lease_deposit.amount
        )
        if contract.down_payment is None or contract.remaining_balance is None:
            return
        scheduled = (
            contract.down_payment.amount
            + sum(payment.amount.amount for payment in contract.intermediate_payments)
            + contract.remaining_balance.amount
        )
        if total != scheduled:
            items.append(
                ContractReviewItem(
                    item_id="payment_total_match",
                    category="contract_field",
                    label="계약금·중도금·잔금 합계",
                    status=ReviewItemStatus.NEEDS_INPUT,
                    message=(
                        "계약 총액과 확인된 지급 일정의 합계가 다릅니다. "
                        "금액 원문과 지급 일정을 다시 확인해 주세요."
                    ),
                )
            )

    @staticmethod
    def _append_registry_disclosure_check(contract, items: list[ContractReviewItem]) -> None:
        if contract.disclosures.registry_checked is True:
            return
        items.append(
            ContractReviewItem(
                item_id="registry_checked_disclosure",
                category="contract_field",
                label="등기사항전부증명서 확인 고지",
                status=ReviewItemStatus.NEEDS_INPUT,
                message="계약서에 등기 확인 여부가 확인되지 않았습니다.",
            )
        )

    @staticmethod
    def _append_sale_transfer_check(contract, items: list[ContractReviewItem]) -> None:
        if contract.contract_type.value != "sale" or contract.ownership_transfer_date:
            return
        items.append(
            ContractReviewItem(
                item_id="ownership_transfer_date",
                category="contract_field",
                label="소유권이전 예정일",
                status=ReviewItemStatus.NEEDS_INPUT,
                message="매매계약의 소유권이전 예정일을 확인해 주세요.",
            )
        )

    @staticmethod
    def _query_for_precheck(contract_type: str, item_id: str) -> str | None:
        queries = {
            "registry_document": "부동산 계약 전 등기사항전부증명서 확인 사항",
            "property_address_match": "계약서와 등기사항전부증명서의 목적물 주소 불일치 확인",
            "registered_owner_match": "부동산 계약 상대방과 등기부 소유자 불일치 확인",
            "payment_total_match": "부동산 계약 매매대금 또는 보증금 지급금 합계 불일치 확인",
            "registry_checked_disclosure": "부동산 계약 전 등기사항전부증명서 확인 필요성",
            "ownership_transfer_date": "매매계약 소유권이전 예정일 확인 사항",
        }
        base_query = queries.get(item_id)
        return f"{contract_type} {base_query}" if base_query else None

    @staticmethod
    def _redact_contract_text(text: str, contract) -> str:
        redacted = text
        for party in contract.parties:
            redacted = redacted.replace(party.name, "당사자")
        redacted = redacted.replace(contract.property.address, "목적물")
        if contract.property.detail_address:
            redacted = redacted.replace(contract.property.detail_address, "상세주소")
        redacted = re.sub(r"\d{2,3}-\d{3,4}-\d{4}", "연락처", redacted)
        redacted = re.sub(r"\d{6}-\d{7}", "식별번호", redacted)
        return redacted

    @staticmethod
    def _normalize_text(value: str) -> str:
        return re.sub(r"[^0-9A-Za-z가-힣]", "", value).lower()
