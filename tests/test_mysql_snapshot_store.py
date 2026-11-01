# test_mysql_snapshot_store.py
"""
Description: MySQL 확정 스냅샷 저장소의 환경 설정 선택과 직렬화 규칙을 검증한다.
실제 MySQL 연결은 로컬 환경변수가 준비된 경우의 수동 통합 검증으로 분리한다.
Author: ooheunsu
Date: 2026-10-26
Before:
    - MySQL 스냅샷 저장소 선택 조건을 회귀 검증하는 테스트가 없는 상태.
After:
    - 메모리 기본값과 MySQL 설정 검증 규칙을 자동 테스트할 수 있음.
"""

import os
import unittest
from unittest.mock import patch

from api.services.mysql_snapshot_store import MySqlSnapshotStoreSettings


class MySqlSnapshotStoreSettingsTests(unittest.TestCase):
    def test_memory_backend_does_not_create_mysql_settings(self):
        with patch.dict(os.environ, {"SNAPSHOT_STORE_BACKEND": "memory"}):
            self.assertIsNone(MySqlSnapshotStoreSettings.from_env())

    def test_mysql_backend_reads_required_settings(self):
        with patch.dict(
            os.environ,
            {
                "SNAPSHOT_STORE_BACKEND": "mysql",
                "MYSQL_HOST": "127.0.0.1",
                "MYSQL_PORT": "3306",
                "MYSQL_DATABASE": "alphalawva",
                "MYSQL_USER": "alphalawva_app",
                "MYSQL_PASSWORD": "test-password",
            },
        ):
            settings = MySqlSnapshotStoreSettings.from_env()

        self.assertIsNotNone(settings)
        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 3306)
        self.assertEqual(settings.database, "alphalawva")
        self.assertEqual(settings.user, "alphalawva_app")

    def test_mysql_backend_rejects_empty_password(self):
        with patch.dict(
            os.environ,
            {
                "SNAPSHOT_STORE_BACKEND": "mysql",
                "MYSQL_HOST": "127.0.0.1",
                "MYSQL_PORT": "3306",
                "MYSQL_DATABASE": "alphalawva",
                "MYSQL_USER": "alphalawva_app",
                "MYSQL_PASSWORD": "",
            },
        ):
            with self.assertRaisesRegex(ValueError, "MYSQL_PASSWORD"):
                MySqlSnapshotStoreSettings.from_env()


if __name__ == "__main__":
    unittest.main()
