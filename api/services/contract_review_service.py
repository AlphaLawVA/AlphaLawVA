# contract_review_service.py
"""
Description: 사용자 확인 계약 정보의 누락 항목과 RAG 검토 대기 항목을 구성한다.
법령·판례 검색이나 법률 판단 없이 계약서 검토 API의 초기 응답을 제공한다.
Author: ooheunsu
Date: 2026-10-24
Before:
    - 계약서 검토 입력을 구조화하고 검토 대기 항목을 반환할 서비스가 없는 상태.
After:
    - 확인된 계약·등기 정보에서 입력 확인과 근거 검색 대기 항목을 반환할 수 있음.
"""

from __future__ import annotations

from uuid import uuid4

from api.models.contracts import (
    AnalysisStatus,
    ContractReviewAnalysisItem,
    ContractInputSummary,
    ContractReviewEvidenceItem,
    ContractReviewItem,
    ContractReviewRequest,
    ContractReviewResponse,
    EvidenceStatus,
    ReviewItemStatus,
)
from api.models.contract_snapshots import ConfirmedContractSnapshot, PartyRole
from api.models.registry_snapshots import ConfirmedRegistrySnapshot
from api.services.confirmed_snapshot_store import ConfirmedSnapshotRepository
from ml.agent.contract_review_graph import ContractReviewGraph
from ml.agent.contract_review_graph import ContractReviewAnalyzer
from ml.agent.contract_review_graph import ContractReviewEvidenceRetriever


class ContractReviewService:
    """RAG 연결 전 계약서 검토 입력 상태를 구성한다."""

    def __init__(
        self,
        snapshot_store: ConfirmedSnapshotRepository,
        review_graph: ContractReviewGraph | None = None,
        evidence_retriever: ContractReviewEvidenceRetriever | None = None,
        analyzer: ContractReviewAnalyzer | None = None,
    ) -> None:
        self._snapshot_store = snapshot_store
        self._review_graph = review_graph or ContractReviewGraph()
        self._evidence_retriever = evidence_retriever
        self._analyzer = analyzer

    def review(self, payload: ContractReviewRequest) -> ContractReviewResponse:
        contract_snapshot = self._snapshot_store.get_contract(
            payload.contract_snapshot_id
        )
        registry_snapshot = (
            self._snapshot_store.get_registry(payload.registry_snapshot_id)
            if payload.registry_snapshot_id is not None
            else None
        )
        evidence_status = EvidenceStatus.PENDING_RETRIEVAL
        evidence: list[ContractReviewEvidenceItem] = []
        analysis_status = AnalysisStatus.PENDING
        analysis: list[ContractReviewAnalysisItem] = []
        try:
            precheck_result = self._review_graph.prepare(
                contract_snapshot,
                registry_snapshot,
                evidence_retriever=self._evidence_retriever,
                analyzer=self._analyzer,
            )
        except (FileNotFoundError, RuntimeError, ValueError):
            precheck_result = self._review_graph.prepare(
                contract_snapshot,
                registry_snapshot,
            )
            evidence_status = EvidenceStatus.UNAVAILABLE
            if self._analyzer is not None:
                analysis_status = AnalysisStatus.UNAVAILABLE
        else:
            if self._evidence_retriever is not None:
                evidence_status = EvidenceStatus.RETRIEVED
                evidence = [
                    ContractReviewEvidenceItem(
                        item_id=item.item_id,
                        source_type=item.source_type,
                        source_id=item.source_id,
                        title=item.title,
                        text=item.text,
                        rank=item.rank,
                        similarity=item.similarity,
                        metadata=item.metadata,
                    )
                    for item in precheck_result.evidence
                ]
            if self._analyzer is not None and precheck_result.evidence:
                analysis_status = (
                    AnalysisStatus.UNAVAILABLE
                    if precheck_result.analysis_unavailable
                    else AnalysisStatus.ANALYZED
                )
                analysis = [
                    ContractReviewAnalysisItem(
                        item_id=item.item_id,
                        summary=item.summary,
                        review_status=item.review_status,
                        revision=item.revision,
                        negotiation=item.negotiation,
                        evidence_source_ids=item.evidence_source_ids,
                    )
                    for item in precheck_result.analyses
                ]
        evidence_item_ids = {item.item_id for item in precheck_result.evidence}
        contract_items = self._build_contract_items(
            contract_snapshot,
            evidence_item_ids,
        )
        registry_items = self._build_registry_items(
            registry_snapshot,
            evidence_item_ids,
        )
        return ContractReviewResponse(
            review_id=uuid4(),
            status="completed",
            input_summary=ContractInputSummary(
                contract_snapshot_id=payload.contract_snapshot_id,
                registry_snapshot_id=payload.registry_snapshot_id,
                contract_type=contract_snapshot.contract.contract_type,
                property_address=contract_snapshot.contract.property.address,
                has_registry=registry_snapshot is not None,
                special_term_count=len(contract_snapshot.contract.special_terms),
            ),
            contract_items=contract_items,
            registry_items=registry_items,
            precheck_items=precheck_result.items,
            evidence=evidence,
            evidence_status=evidence_status,
            analysis=analysis,
            analysis_status=analysis_status,
            message=(
                "확정된 계약 정보와 검색 근거의 확인 항목을 구성했습니다."
                if analysis_status == AnalysisStatus.ANALYZED
                else "검색 근거를 찾았습니다. 근거 기반 확인 요약은 아직 생성되지 않았습니다."
                if evidence_status == EvidenceStatus.RETRIEVED
                else "확정된 계약 정보의 입력 상태를 확인했습니다. "
                "법령·판례 근거 검토는 아직 연결되지 않았습니다."
            ),
            disclaimer=(
                "이 결과는 법률 자문이나 최종 계약서가 아니며, "
                "근거 검색과 사용자 검토를 거쳐야 합니다."
            ),
        )

    @staticmethod
    def _build_contract_items(
        contract_snapshot: ConfirmedContractSnapshot,
        evidence_item_ids: set[str],
    ) -> list[ContractReviewItem]:
        contract = contract_snapshot.contract
        items: list[ContractReviewItem] = []
        owner_role = (
            PartyRole.SELLER
            if contract.contract_type.value == "sale"
            else PartyRole.LESSOR
        )
        counterparty_role = (
            PartyRole.BUYER
            if contract.contract_type.value == "sale"
            else PartyRole.LESSEE
        )
        party_names = {party.role: party.name for party in contract.parties}
        optional_fields = (
            (
                "owner_party",
                "임대인 또는 매도인",
                party_names.get(owner_role),
            ),
            (
                "counterparty",
                "임차인 또는 매수인",
                party_names.get(counterparty_role),
            ),
            ("contract_date", "계약 체결일", contract.contract_date),
        )
        for item_id, label, value in optional_fields:
            if value is None:
                items.append(
                    ContractReviewItem(
                        item_id=item_id,
                        category="contract_field",
                        label=label,
                        status=ReviewItemStatus.NEEDS_INPUT,
                        message="근거 검토 전에 확인하거나 입력해 주세요.",
                    )
                )

        for term in contract.special_terms:
            is_review_ready = term.term_id in evidence_item_ids
            items.append(
                ContractReviewItem(
                    item_id=term.term_id,
                    category="special_term",
                    label=f"특약 {term.order}",
                    original_text=term.original_text,
                    status=(
                        ReviewItemStatus.REVIEW_READY
                        if is_review_ready
                        else ReviewItemStatus.PENDING_EVIDENCE
                    ),
                    message=(
                        "법령·판례 근거를 연결했습니다. 원문과 함께 확인해 주세요."
                        if is_review_ready
                        else "법령·판례 근거 검색 전 검토 대기 항목입니다."
                    ),
                )
            )
        return items

    @staticmethod
    def _build_registry_items(
        registry: ConfirmedRegistrySnapshot | None,
        evidence_item_ids: set[str],
    ) -> list[ContractReviewItem]:
        if registry is None:
            return []

        # OCR은 말소선을 구분하지 못해 상태가 unknown인 권리가 많다. 말소로 확정되지 않은
        # 본 등기(4-1 같은 변경·말소 부기는 제외)는 모두 확인 대상으로 남긴다.
        active_encumbrances = [
            encumbrance
            for encumbrance in registry.encumbrances
            if encumbrance.status.value != "cancelled"
            and "-" not in encumbrance.rank_number
        ]
        if not active_encumbrances:
            return [
                ContractReviewItem(
                    item_id="registry_active_encumbrances",
                    category="registry_field",
                    label="유효 권리부담",
                    status=ReviewItemStatus.PENDING_EVIDENCE,
                    message="유효 권리부담이 없는 것으로 확인되었습니다.",
                )
            ]

        return [
            ContractReviewItem(
                item_id=f"registry_{encumbrance.rank_number}",
                category="registry_field",
                label=f"{encumbrance.rank_number}순위 {encumbrance.registration_purpose}",
                original_text=encumbrance.raw_text,
                status=(
                    ReviewItemStatus.REVIEW_READY
                    if f"registry_{encumbrance.rank_number}" in evidence_item_ids
                    else ReviewItemStatus.PENDING_EVIDENCE
                ),
                message=(
                    "법령·판례 근거를 연결했습니다. 원문과 함께 확인해 주세요."
                    if f"registry_{encumbrance.rank_number}" in evidence_item_ids
                    else (
                        "말소 여부가 확정되지 않은 권리입니다. 원문에서 말소 여부와 "
                        "변경 내역을 확인해 주세요."
                        if encumbrance.status.value == "unknown"
                        else "법령·판례 근거 검색 전 검토 대기 항목입니다."
                    )
                ),
            )
            for encumbrance in active_encumbrances
        ]
