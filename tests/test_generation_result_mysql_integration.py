"""계약서 검토·특약 생성 저장 결과의 실제 MySQL 왕복과 소유자 분리를 검증한다."""

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from api.models.generation_results import GenerationResultKind
from api.services.generation_results import (
    GenerationResultNotFoundError,
    GenerationResultService,
    MySqlGenerationResultStore,
)
from api.services.mysql_snapshot_store import MySqlSnapshotStoreSettings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


@unittest.skipUnless(
    os.getenv("RUN_MYSQL_INTEGRATION_TESTS") == "1",
    "RUN_MYSQL_INTEGRATION_TESTS=1에서만 로컬 MySQL 통합 테스트를 실행합니다.",
)
class GenerationResultMySqlIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.settings = MySqlSnapshotStoreSettings.from_env()
        if cls.settings is None:
            raise unittest.SkipTest("MySQL 저장소가 활성화되지 않았습니다.")

    def test_round_trip_idempotency_and_user_isolation(self):
        store = MySqlGenerationResultStore(self.settings)
        service = GenerationResultService(store, clock=lambda: datetime.now(UTC))
        user_id = uuid4()
        other_user_id = uuid4()
        source_id = uuid4()
        payload = {"items": [{"status": "draft_ready", "draft_text": "한글 특약 초안"}]}
        service.record(
            kind=GenerationResultKind.SPECIAL_CLAUSE,
            source_id=source_id,
            title="특약 초안 · 통합 시험",
            payload=payload,
            saveable=True,
        )

        try:
            first = service.save(user_id, GenerationResultKind.SPECIAL_CLAUSE, source_id)
            second = service.save(user_id, GenerationResultKind.SPECIAL_CLAUSE, source_id)
            self.assertEqual(first.result_id, second.result_id)
            self.assertEqual(len(service.list(user_id).items), 1)
            detail = service.get(user_id, first.result_id)
            self.assertEqual(detail.payload, payload)
            self.assertEqual(detail.title, "특약 초안 · 통합 시험")
            self.assertEqual(service.list(other_user_id).items, [])
            with self.assertRaises(GenerationResultNotFoundError):
                service.get(other_user_id, first.result_id)
        finally:
            self._delete_rows(store, user_id)

    @staticmethod
    def _delete_rows(store, user_id) -> None:
        connection = store._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM generation_results WHERE user_id = %s",
                    (str(user_id),),
                )
            connection.commit()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
