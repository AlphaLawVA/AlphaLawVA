# test_registry_snapshots.py
"""
Description: 확인 완료 등본 스냅샷의 소유자와 권리부담 구조를 검증한다.
필수 문서 정보와 권리부담 금액 형식의 회귀를 방지한다.
Author: ooheunsu
Date: 2026-10-25
Before:
    - 사용자 확인이 끝난 등본 데이터의 타입 검증이 없는 상태.
After:
    - 등본 스냅샷의 필수값과 권리부담 구조를 자동 검증할 수 있음.
"""

import unittest
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError

from api.models.registry_snapshots import ConfirmedRegistrySnapshot


class RegistrySnapshotTests(unittest.TestCase):
    def _base_payload(self) -> dict:
        return {
            "snapshot_id": str(uuid4()),
            "document_id": str(uuid4()),
            "confirmation": {
                "confirmed_by_user": True,
                "revision": 1,
                "confirmed_at": datetime.now(UTC).isoformat(),
            },
            "issued_at": "2026-06-26T14:17:29+09:00",
            "address": "경기도 광주시 장지동",
        }

    def test_accepts_confirmed_registry(self):
        payload = self._base_payload()
        payload["owners"] = [{"name": "장*5", "status": "active"}]
        payload["encumbrances"] = [
            {
                "section": "eulgu",
                "rank_number": "4",
                "registration_purpose": "근저당권설정",
                "registration_date": "2015-08-20",
                "maximum_claim_amount": 300_000_000,
                "raw_text": "채권최고액 금300,000,000원",
                "source": {"page_number": 1},
                "status": "active",
            }
        ]

        snapshot = ConfirmedRegistrySnapshot.model_validate(payload)

        self.assertEqual(snapshot.encumbrances[0].maximum_claim_amount, 300_000_000)

    def test_rejects_negative_claim_amount(self):
        payload = self._base_payload()
        payload["encumbrances"] = [
            {
                "section": "eulgu",
                "rank_number": "4",
                "registration_purpose": "근저당권설정",
                "maximum_claim_amount": -1,
                "raw_text": "채권최고액 금액 오류",
                "source": {"page_number": 1},
                "status": "active",
            }
        ]

        with self.assertRaises(ValidationError):
            ConfirmedRegistrySnapshot.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
