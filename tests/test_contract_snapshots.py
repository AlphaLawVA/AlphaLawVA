# test_contract_snapshots.py
"""
Description: 전세·월세·매매 계약 스냅샷과 기능별 입력 스키마를 검증한다.
계약 유형 분기, 사용자 확인, 기능별 자연어 입력 경계를 회귀 테스트한다.
Author: ooheunsu
Date: 2026-10-25
Before:
    - 기능들이 공유할 확인 완료 계약 데이터의 타입 검증이 없는 상태.
After:
    - 계약 유형별 필수값과 계약서 검토·내용증명 입력 차이를 검증할 수 있음.
"""

import unittest
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError

from api.models.contract_snapshots import (
    CertifiedLetterWorkflowInput,
    ConfirmedContractSnapshot,
    ContractReviewWorkflowInput,
)


class ContractSnapshotTests(unittest.TestCase):
    def _base_snapshot(self, contract_type: str) -> dict:
        owner_role = "seller" if contract_type == "sale" else "lessor"
        counterparty_role = "buyer" if contract_type == "sale" else "lessee"
        return {
            "schema_version": "confirmed_contract_snapshot.v0.1",
            "snapshot_id": str(uuid4()),
            "document_id": str(uuid4()),
            "confirmation": {
                "confirmed_by_user": True,
                "revision": 1,
                "confirmed_at": datetime.now(UTC).isoformat(),
            },
            "contract": {
                "contract_type": contract_type,
                "contract_date": "2026-10-25",
                "property": {"address": "서울특별시 테스트구 테스트로 1"},
                "parties": [
                    {"role": owner_role, "name": "소유자"},
                    {"role": counterparty_role, "name": "상대방"},
                ],
            },
        }

    def test_monthly_rent_requires_monthly_payment(self):
        payload = self._base_snapshot("monthly_rent")
        payload["contract"].update(
            {
                "lease_deposit": {"amount": 20_000_000},
                "start_date": "2026-11-01",
                "end_date": "2028-10-31",
            }
        )

        with self.assertRaises(ValidationError):
            ConfirmedContractSnapshot.model_validate(payload)

    def test_sale_snapshot_accepts_sale_specific_payments(self):
        payload = self._base_snapshot("sale")
        payload["contract"].update(
            {
                "sale_price": {
                    "amount": 640_000_000,
                    "korean_text": "육억사천만원정",
                },
                "remaining_balance": {"amount": 310_000_000},
                "remaining_balance_date": "2027-03-15",
                "ownership_transfer_date": "2027-03-15",
            }
        )

        snapshot = ConfirmedContractSnapshot.model_validate(payload)

        self.assertEqual(snapshot.contract.contract_type, "sale")
        self.assertEqual(snapshot.contract.sale_price.amount, 640_000_000)

    def test_lease_rejects_reversed_period(self):
        payload = self._base_snapshot("jeonse")
        payload["contract"].update(
            {
                "lease_deposit": {"amount": 200_000_000},
                "start_date": "2028-10-31",
                "end_date": "2026-11-01",
            }
        )

        with self.assertRaises(ValidationError):
            ConfirmedContractSnapshot.model_validate(payload)

    def test_sale_requires_seller_and_buyer(self):
        payload = self._base_snapshot("sale")
        payload["contract"].update(
            {
                "sale_price": {"amount": 640_000_000},
                "parties": [
                    {"role": "lessor", "name": "잘못된 역할"},
                    {"role": "buyer", "name": "매수인"},
                ],
            }
        )

        with self.assertRaises(ValidationError):
            ConfirmedContractSnapshot.model_validate(payload)

    def test_contract_review_has_no_user_query(self):
        with self.assertRaises(ValidationError):
            ContractReviewWorkflowInput.model_validate(
                {
                    "contract_snapshot_id": str(uuid4()),
                    "user_query": "계약서 전체를 검토해 주세요.",
                }
            )

    def test_certified_letter_requires_purpose_and_facts(self):
        with self.assertRaises(ValidationError):
            CertifiedLetterWorkflowInput.model_validate(
                {
                    "contract_snapshot_id": str(uuid4()),
                    "purpose": "보증금 반환 요구",
                    "sender": {"name": "발신인", "address": "서울시 테스트구"},
                    "receiver": {"name": "수신인", "address": "서울시 예시구"},
                }
            )

    def test_certified_letter_rejects_legacy_user_query(self):
        with self.assertRaises(ValidationError):
            CertifiedLetterWorkflowInput.model_validate(
                {
                    "contract_snapshot_id": str(uuid4()),
                    "user_query": "보증금을 반환해 주세요.",
                    "purpose": "보증금 반환 요구",
                    "facts": "계약이 끝났지만 보증금을 반환받지 못했습니다.",
                    "sender": {"name": "발신인", "address": "서울시 테스트구"},
                    "receiver": {"name": "수신인", "address": "서울시 예시구"},
                }
            )


if __name__ == "__main__":
    unittest.main()
