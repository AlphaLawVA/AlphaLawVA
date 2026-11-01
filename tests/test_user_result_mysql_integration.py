"""사용자 내용증명 결과와 판례 즐겨찾기의 실제 MySQL 왕복을 검증한다."""

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from api.services.certified_letter_results import (
    CertifiedLetterResultService,
    MySqlCertifiedLetterResultStore,
)
from api.services.mysql_snapshot_store import MySqlSnapshotStoreSettings
from api.services.precedent_favorites import MySqlPrecedentFavoriteStore
from api.services.precedent_search_sessions import SearchResultInteractionState
from tests.test_certified_letter_results import DRAFT_ID, FakeDraftService
from tests.test_precedent_favorites import make_result


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


@unittest.skipUnless(
    os.getenv("RUN_MYSQL_INTEGRATION_TESTS") == "1",
    "RUN_MYSQL_INTEGRATION_TESTS=1에서만 로컬 MySQL 통합 테스트를 실행합니다.",
)
class UserResultMySqlIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.settings = MySqlSnapshotStoreSettings.from_env()
        if cls.settings is None:
            raise unittest.SkipTest("MySQL 저장소가 활성화되지 않았습니다.")

    def test_certified_letter_result_round_trip_and_user_isolation(self):
        store = MySqlCertifiedLetterResultStore(self.settings)
        user_id = uuid4()
        other_user_id = uuid4()
        result_id = uuid4()
        service = CertifiedLetterResultService(
            FakeDraftService(),
            store,
            clock=lambda: datetime.now(UTC),
            uuid_factory=lambda: result_id,
        )

        try:
            saved = service.save(user_id, DRAFT_ID)
            self.assertEqual(service.get(user_id, saved.result_id).result_id, result_id)
            self.assertEqual(len(service.list(user_id).items), 1)
            self.assertEqual(service.list(other_user_id).items, [])
        finally:
            self._delete_rows(store, "certified_letter_results", user_id)

    def test_precedent_favorite_round_trip_and_user_isolation(self):
        store = MySqlPrecedentFavoriteStore(self.settings)
        user_id = uuid4()
        other_user_id = uuid4()
        search_id = uuid4()

        try:
            favorite = store.create(
                search_id=search_id,
                query="계약 종료 후 보증금을 돌려받지 못했습니다.",
                result=make_result("integration-test-precedent"),
                interaction=SearchResultInteractionState(),
                user_id=user_id,
            )
            self.assertEqual(
                store.get(favorite.favorite_id, user_id).query, favorite.query
            )
            self.assertEqual(len(store.list(user_id)), 1)
            self.assertEqual(store.list(other_user_id), [])
            store.delete(favorite.favorite_id, user_id)
            self.assertEqual(store.list(user_id), [])
        finally:
            self._delete_rows(store, "precedent_favorites", user_id)

    @staticmethod
    def _delete_rows(store, table_name: str, user_id) -> None:
        connection = store._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"DELETE FROM {table_name} WHERE user_id = %s",
                    (str(user_id),),
                )
            connection.commit()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
