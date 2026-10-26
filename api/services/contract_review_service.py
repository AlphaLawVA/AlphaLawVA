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
    ContractInputSummary,
    ContractReviewItem,
    ContractReviewRequest,
    ContractReviewResponse,
    EvidenceStatus,
    ReviewItemStatus,
)
from api.models.contract_snapshots import ConfirmedContractSnapshot, PartyRole
from api.models.registry_snapshots import ConfirmedRegistrySnapshot
from api.services.confirmed_snapshot_store import ConfirmedSnapshotRepository


class ContractReviewService:
    """RAG 연결 전 계약서 검토 입력 상태를 구성한다."""

    def __init__(self, snapshot_store: ConfirmedSnapshotRepository) -> None:
        self._snapshot_store = snapshot_store

    def review(self, payload: ContractReviewRequest) -> ContractReviewResponse:
        contract_snapshot = self._snapshot_store.get_contract(
            payload.contract_snapshot_id
        )
        registry_snapshot = (
            self._snapshot_store.get_registry(payload.registry_snapshot_id)
            if payload.registry_snapshot_id is not None
            else None
        )
        contract_items = self._build_contract_items(contract_snapshot)
        registry_items = self._build_registry_items(registry_snapshot)
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
            evidence_status=EvidenceStatus.PENDING_RETRIEVAL,
            message=(
                "확정된 계약 정보의 입력 상태를 확인했습니다. "
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
            items.append(
                ContractReviewItem(
                    item_id=term.term_id,
                    category="special_term",
                    label=f"특약 {term.order}",
                    original_text=term.original_text,
                    status=ReviewItemStatus.PENDING_EVIDENCE,
                    message="법령·판례 근거 검색 전 검토 대기 항목입니다.",
                )
            )
        return items

    @staticmethod
    def _build_registry_items(
        registry: ConfirmedRegistrySnapshot | None,
    ) -> list[ContractReviewItem]:
        if registry is None:
            return []

        active_encumbrances = [
            encumbrance
            for encumbrance in registry.encumbrances
            if encumbrance.status.value == "active"
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
                status=ReviewItemStatus.PENDING_EVIDENCE,
                message="법령·판례 근거 검색 전 검토 대기 항목입니다.",
            )
            for encumbrance in active_encumbrances
        ]
