# test_contract_review_registry_items.py
"""
Description: 말소 여부가 확정되지 않은 등본 권리부담이 "없음"으로 안내되지 않는지 확인한다.
Author: ooheunsu
Date: 2026-11-04
Before:
    - 상태가 active인 권리만 확인 대상이라 OCR로 읽은 unknown 권리가 "없음"으로 표시됨.
After:
    - 말소로 확정되지 않은 본 등기는 확인 항목으로 남고 부기 항목은 제외됨.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from uuid import uuid4

from api.models.registry_snapshots import ConfirmedRegistrySnapshot
from api.services.contract_review_service import ContractReviewService


def registry(*entries):
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
            "address": "서울특별시 성동구 성수동",
            "owners": [{"name": "홍길동", "status": "active"}],
            "encumbrances": [
                {
                    "section": "eulgu",
                    "rank_number": rank,
                    "registration_purpose": "근저당권설정",
                    "raw_text": f"{rank}번 근저당권",
                    "source": {"page_number": 1},
                    "status": status,
                }
                for rank, status in entries
            ],
        }
    )


class RegistryItemsTests(unittest.TestCase):
    def test_unknown_status_main_entry_stays_as_item_to_confirm(self):
        items = ContractReviewService._build_registry_items(
            registry(("4", "unknown"), ("4-1", "unknown")), set()
        )

        self.assertEqual([item.item_id for item in items], ["registry_4"])
        self.assertIn("말소 여부", items[0].message)

    def test_reports_absence_only_when_every_entry_is_cancelled(self):
        items = ContractReviewService._build_registry_items(
            registry(("4", "cancelled")), set()
        )

        self.assertEqual(items[0].item_id, "registry_active_encumbrances")


if __name__ == "__main__":
    unittest.main()
