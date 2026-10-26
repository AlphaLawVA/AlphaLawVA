# mysql_snapshot_store.py
"""
Description: 사용자 확인 계약서·등본 스냅샷을 MySQL에 JSON 원문과 조회 메타데이터로 저장한다.
환경 설정이 명시된 경우에만 영속 저장소를 만들고, 기존 메모리 저장소와 같은 인터페이스를 제공한다.
Author: ooheunsu
Date: 2026-10-26
Before:
    - 확인 완료 스냅샷이 서버 프로세스 메모리에만 저장되는 상태.
After:
    - MySQL 설정이 있는 로컬 환경에서 스냅샷을 서버 재시작 후에도 조회할 수 있음.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import UUID

import pymysql
from dotenv import load_dotenv
from pymysql.cursors import DictCursor

from api.models.contract_snapshots import ConfirmedContractSnapshot
from api.models.registry_snapshots import ConfirmedRegistrySnapshot
from api.services.confirmed_snapshot_store import SnapshotNotFoundError


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class MySqlSnapshotStoreSettings:
    """확정 스냅샷 저장소의 MySQL 연결 설정이다."""

    host: str
    port: int
    database: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> MySqlSnapshotStoreSettings | None:
        """명시된 저장소 백엔드에 맞는 연결 설정을 읽는다."""

        load_dotenv(PROJECT_ROOT / ".env")
        backend = os.getenv("SNAPSHOT_STORE_BACKEND", "memory").strip().lower()
        if backend == "memory":
            return None
        if backend != "mysql":
            raise ValueError(
                "SNAPSHOT_STORE_BACKEND는 memory 또는 mysql이어야 합니다."
            )

        values = {
            "MYSQL_HOST": os.getenv("MYSQL_HOST"),
            "MYSQL_PORT": os.getenv("MYSQL_PORT", "3306"),
            "MYSQL_DATABASE": os.getenv("MYSQL_DATABASE"),
            "MYSQL_USER": os.getenv("MYSQL_USER"),
            "MYSQL_PASSWORD": os.getenv("MYSQL_PASSWORD"),
        }
        missing = [
            name
            for name, value in values.items()
            if value is None or not value.strip()
        ]
        if missing:
            raise ValueError(
                "MySQL 스냅샷 저장소 설정이 없습니다: " + ", ".join(missing)
            )

        try:
            port = int(values["MYSQL_PORT"])
        except ValueError as error:
            raise ValueError("MYSQL_PORT는 정수여야 합니다.") from error

        return cls(
            host=values["MYSQL_HOST"].strip(),
            port=port,
            database=values["MYSQL_DATABASE"].strip(),
            user=values["MYSQL_USER"].strip(),
            password=values["MYSQL_PASSWORD"],
        )


class MySqlConfirmedSnapshotStore:
    """사용자 확인 완료 스냅샷을 MySQL에 영속화한다."""

    def __init__(
        self,
        settings: MySqlSnapshotStoreSettings,
        *,
        initialize_schema: bool = True,
    ) -> None:
        self._settings = settings
        self._schema_ready = False
        self._schema_lock = Lock()
        if initialize_schema:
            self.ensure_schema()

    def save_contract(
        self,
        snapshot: ConfirmedContractSnapshot,
    ) -> ConfirmedContractSnapshot:
        self._ensure_schema_ready()
        payload = self._serialize_snapshot(snapshot)
        self._execute_write(
            """
            INSERT INTO confirmed_contract_snapshots (
                snapshot_id,
                document_id,
                schema_version,
                contract_type,
                confirmed_at,
                payload
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                document_id = VALUES(document_id),
                schema_version = VALUES(schema_version),
                contract_type = VALUES(contract_type),
                confirmed_at = VALUES(confirmed_at),
                payload = VALUES(payload),
                updated_at = CURRENT_TIMESTAMP(6)
            """,
            (
                str(snapshot.snapshot_id),
                str(snapshot.document_id),
                snapshot.schema_version,
                snapshot.contract.contract_type.value,
                snapshot.confirmation.confirmed_at.replace(tzinfo=None),
                payload,
            ),
        )
        return snapshot

    def save_registry(
        self,
        snapshot: ConfirmedRegistrySnapshot,
    ) -> ConfirmedRegistrySnapshot:
        self._ensure_schema_ready()
        payload = self._serialize_snapshot(snapshot)
        self._execute_write(
            """
            INSERT INTO confirmed_registry_snapshots (
                snapshot_id,
                document_id,
                schema_version,
                issued_at,
                confirmed_at,
                address,
                payload
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                document_id = VALUES(document_id),
                schema_version = VALUES(schema_version),
                issued_at = VALUES(issued_at),
                confirmed_at = VALUES(confirmed_at),
                address = VALUES(address),
                payload = VALUES(payload),
                updated_at = CURRENT_TIMESTAMP(6)
            """,
            (
                str(snapshot.snapshot_id),
                str(snapshot.document_id),
                snapshot.schema_version,
                snapshot.issued_at.replace(tzinfo=None),
                snapshot.confirmation.confirmed_at.replace(tzinfo=None),
                snapshot.address,
                payload,
            ),
        )
        return snapshot

    def get_contract(self, snapshot_id: UUID) -> ConfirmedContractSnapshot:
        self._ensure_schema_ready()
        payload = self._get_payload("confirmed_contract_snapshots", snapshot_id)
        return ConfirmedContractSnapshot.model_validate(payload)

    def get_registry(self, snapshot_id: UUID) -> ConfirmedRegistrySnapshot:
        self._ensure_schema_ready()
        payload = self._get_payload("confirmed_registry_snapshots", snapshot_id)
        return ConfirmedRegistrySnapshot.model_validate(payload)

    def ensure_schema(self) -> None:
        """로컬 개발용 스냅샷 테이블이 없으면 생성한다."""

        statements = (
            """
            CREATE TABLE IF NOT EXISTS confirmed_contract_snapshots (
                snapshot_id CHAR(36) NOT NULL PRIMARY KEY,
                document_id CHAR(36) NOT NULL,
                schema_version VARCHAR(80) NOT NULL,
                contract_type VARCHAR(30) NOT NULL,
                confirmed_at DATETIME(6) NOT NULL,
                payload JSON NOT NULL,
                created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                    ON UPDATE CURRENT_TIMESTAMP(6),
                INDEX idx_contract_snapshot_document_id (document_id),
                INDEX idx_contract_snapshot_type (contract_type)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS confirmed_registry_snapshots (
                snapshot_id CHAR(36) NOT NULL PRIMARY KEY,
                document_id CHAR(36) NOT NULL,
                schema_version VARCHAR(80) NOT NULL,
                issued_at DATETIME(6) NOT NULL,
                confirmed_at DATETIME(6) NOT NULL,
                address VARCHAR(500) NOT NULL,
                payload JSON NOT NULL,
                created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                    ON UPDATE CURRENT_TIMESTAMP(6),
                INDEX idx_registry_snapshot_document_id (document_id)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci
            """,
        )
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
            connection.commit()
            self._schema_ready = True
        finally:
            connection.close()

    def _ensure_schema_ready(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if not self._schema_ready:
                self.ensure_schema()

    def _get_payload(self, table_name: str, snapshot_id: UUID) -> dict[str, Any]:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT payload FROM {table_name} WHERE snapshot_id = %s",
                    (str(snapshot_id),),
                )
                row = cursor.fetchone()
        finally:
            connection.close()
        if row is None:
            raise SnapshotNotFoundError(f"확정 스냅샷이 없습니다: {snapshot_id}")

        payload = row["payload"]
        return json.loads(payload) if isinstance(payload, str) else payload

    def _execute_write(self, statement: str, values: tuple[Any, ...]) -> None:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(statement, values)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self) -> pymysql.connections.Connection:
        return pymysql.connect(
            host=self._settings.host,
            port=self._settings.port,
            user=self._settings.user,
            password=self._settings.password,
            database=self._settings.database,
            charset="utf8mb4",
            cursorclass=DictCursor,
        )

    @staticmethod
    def _serialize_snapshot(
        snapshot: ConfirmedContractSnapshot | ConfirmedRegistrySnapshot,
    ) -> str:
        return json.dumps(
            snapshot.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )


def create_mysql_snapshot_store_from_env() -> MySqlConfirmedSnapshotStore | None:
    """환경변수에서 MySQL 스냅샷 저장소를 선택적으로 생성한다."""

    settings = MySqlSnapshotStoreSettings.from_env()
    return (
        MySqlConfirmedSnapshotStore(settings, initialize_schema=False)
        if settings is not None
        else None
    )
