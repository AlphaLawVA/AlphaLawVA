# test_mysql_snapshot_store_integration.py
"""
Description: 로컬 MySQL에 확정 계약서 스냅샷을 저장·조회하는 통합 동작을 검증한다.
명시적으로 활성화한 경우에만 실행하고 생성한 테스트 행은 항상 삭제한다.
Author: ooheunsu
Date: 2026-10-27
Before:
    - MySQL 스냅샷 저장소의 실제 연결과 JSON 왕복을 확인하는 검증이 없는 상태.
After:
    - 로컬 MySQL 환경에서 스냅샷 저장·조회·정리 동작을 수동 검증할 수 있음.
"""

import os
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from api.models.contract_snapshots import ConfirmedContractSnapshot
from api.services.mysql_snapshot_store import create_mysql_snapshot_store_from_env


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


@unittest.skipUnless(
    os.getenv("RUN_MYSQL_INTEGRATION_TESTS") == "1",
    "RUN_MYSQL_INTEGRATION_TESTS=1에서만 로컬 MySQL 통합 테스트를 실행합니다.",
)
class MySqlSnapshotStoreIntegrationTests(unittest.TestCase):
    def test_saves_and_loads_contract_snapshot(self):
        store = create_mysql_snapshot_store_from_env()
        self.assertIsNotNone(store)

        snapshot = ConfirmedContractSnapshot.model_validate(
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
                    "property": {"address": "서울특별시 테스트구 테스트로 1"},
                    "parties": [
                        {"role": "lessor", "name": "임대인"},
                        {"role": "lessee", "name": "임차인"},
                    ],
                    "lease_deposit": {"amount": 200000000},
                    "start_date": "2026-11-01",
                    "end_date": "2028-10-31",
                },
            }
        )

        try:
            store.save_contract(snapshot)
            loaded = store.get_contract(snapshot.snapshot_id)
            self.assertEqual(loaded, snapshot)
        finally:
            self._delete_contract_snapshot(store, snapshot.snapshot_id)

    @staticmethod
    def _delete_contract_snapshot(store, snapshot_id):
        connection = store._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM confirmed_contract_snapshots WHERE snapshot_id = %s",
                    (str(snapshot_id),),
                )
            connection.commit()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
