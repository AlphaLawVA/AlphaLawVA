# test_contract_workflow_api.py
"""
Description: 계약서 검토와 특약 생성 초기 API의 입력 경계와 응답 상태를 검증한다.
RAG·LLM 연결 전 법률 근거 또는 특약 문안이 생성되지 않는지 함께 확인한다.
Author: ooheunsu
Date: 2026-10-24
Before:
    - 계약서 검토와 특약 생성 API의 공개 동작을 검증하는 테스트가 없는 상태.
After:
    - 확인된 계약 정보와 자연어 특약 요청의 API 계약을 회귀 테스트할 수 있음.
"""

import unittest
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError

from api.models.contracts import ContractReviewRequest, SpecialClauseDraftRequest
from api.models.contract_snapshots import ConfirmedContractSnapshot
from api.models.registry_snapshots import ConfirmedRegistrySnapshot
from api.services.confirmed_snapshot_store import (
    ConfirmedSnapshotStore,
    SnapshotNotFoundError,
)
from api.services.contract_review_service import ContractReviewService
from api.services.special_clause_service import SpecialClauseService


class ContractWorkflowApiTests(unittest.TestCase):
    def _contract_snapshot(self) -> ConfirmedContractSnapshot:
        return ConfirmedContractSnapshot.model_validate(
            {
                "snapshot_id": str(uuid4()),
                "document_id": str(uuid4()),
                "confirmation": {
                    "confirmed_by_user": True,
                    "revision": 1,
                    "confirmed_at": datetime.now(UTC).isoformat(),
                },
                "contract": {
                    "contract_type": "jeonse",
                    "contract_date": "2026-10-25",
                    "property": {"address": "서울특별시 테스트구 테스트로 1"},
                    "parties": [
                        {"role": "lessor", "name": "임대인"},
                        {"role": "lessee", "name": "임차인"},
                    ],
                    "lease_deposit": {"amount": 200000000},
                    "start_date": "2026-11-01",
                    "end_date": "2028-10-31",
                    "special_terms": [
                        {
                            "term_id": "term-1",
                            "order": 1,
                            "original_text": "임대인은 계약 기간 중 추가 담보권을 설정하지 않는다.",
                            "source": {"page_number": 3},
                        }
                    ],
                },
            }
        )

    def _registry_snapshot(self) -> ConfirmedRegistrySnapshot:
        return ConfirmedRegistrySnapshot.model_validate(
            {
                "snapshot_id": str(uuid4()),
                "document_id": str(uuid4()),
                "confirmation": {
                    "confirmed_by_user": True,
                    "revision": 1,
                    "confirmed_at": datetime.now(UTC).isoformat(),
                },
                "issued_at": "2026-10-25T12:00:00+09:00",
                "address": "서울특별시 테스트구 테스트로 1",
                "encumbrances": [
                    {
                        "section": "eulgu",
                        "rank_number": "4",
                        "registration_purpose": "근저당권설정",
                        "maximum_claim_amount": 180000000,
                        "raw_text": "채권최고액 180,000,000원",
                        "source": {"page_number": 1},
                        "status": "active",
                    }
                ],
            }
        )

    def test_contract_review_returns_only_pending_evidence_items(self):
        store = ConfirmedSnapshotStore()
        contract = store.save_contract(self._contract_snapshot())
        registry = store.save_registry(self._registry_snapshot())
        request = ContractReviewRequest.model_validate(
            {
                "contract_snapshot_id": str(contract.snapshot_id),
                "registry_snapshot_id": str(registry.snapshot_id),
            },
        )
        response = ContractReviewService(store).review(request)

        self.assertEqual(response.evidence_status, "pending_retrieval")
        self.assertIn(
            "특약 1",
            [item.label for item in response.contract_items],
        )
        self.assertEqual(response.registry_items[0].status, "pending_evidence")
        self.assertEqual(response.input_summary.contract_snapshot_id, contract.snapshot_id)

    def test_contract_review_rejects_legacy_inline_contract(self):
        with self.assertRaises(ValidationError):
            ContractReviewRequest.model_validate(
                {
                    "confirmed_contract": {
                        "is_user_confirmed": False,
                        "contract_type": "jeonse",
                        "property_address": "서울특별시 테스트구 테스트로 1",
                    },
                }
            )

    def test_contract_review_requires_existing_snapshot(self):
        store = ConfirmedSnapshotStore()
        request = ContractReviewRequest.model_validate(
            {"contract_snapshot_id": str(uuid4())}
        )

        with self.assertRaises(SnapshotNotFoundError):
            ContractReviewService(store).review(request)

    def test_special_clause_request_accepts_only_natural_language_requests(self):
        request = SpecialClauseDraftRequest.model_validate(
            {
                "requests": [
                    "임대인이 계약 기간 중 추가 담보권을 설정하지 못하게 하고 싶어요."
                ],
            },
        )
        response = SpecialClauseService().create_draft(request)

        self.assertEqual(response.status, "pending_generation")
        self.assertEqual(response.items[0].request_index, 1)
        self.assertEqual(response.evidence_status, "pending_retrieval")

    def test_special_clause_request_rejects_contract_information(self):
        with self.assertRaises(ValidationError):
            SpecialClauseDraftRequest.model_validate(
                {
                    "requests": ["보증금 반환 기한을 명확히 하고 싶어요."],
                    "confirmed_contract": {"contract_type": "jeonse"},
                }
            )


if __name__ == "__main__":
    unittest.main()
