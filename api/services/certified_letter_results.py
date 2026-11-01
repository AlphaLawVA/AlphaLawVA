"""사용자별 내용증명 생성 결과를 저장하고 조회한다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock, RLock
from typing import Callable, Protocol
from uuid import UUID, uuid4

import pymysql
from pymysql.cursors import DictCursor

from api.models.certified_letters import (
    CertifiedLetterDraftResult,
    CertifiedLetterJobStatus,
    CertifiedLetterOutcome,
)
from api.models.user_results import (
    CertifiedLetterResultDetail,
    CertifiedLetterResultList,
    CertifiedLetterResultSummary,
)
from api.services.certified_letter_service import CertifiedLetterService
from api.services.mysql_snapshot_store import MySqlSnapshotStoreSettings


class CertifiedLetterResultNotFoundError(Exception):
    """해당 사용자가 소유한 저장 결과가 없는 경우."""


class CertifiedLetterResultNotReadyError(Exception):
    """저장할 수 있는 완성 결과가 아닌 경우."""


@dataclass(frozen=True)
class CertifiedLetterResultRecord:
    result_id: UUID
    user_id: UUID
    draft_id: UUID
    title: str
    result: CertifiedLetterDraftResult
    created_at: datetime
    updated_at: datetime


class CertifiedLetterResultRepository(Protocol):
    def save(
        self, record: CertifiedLetterResultRecord
    ) -> CertifiedLetterResultRecord: ...
    def list_for_user(self, user_id: UUID) -> list[CertifiedLetterResultRecord]: ...
    def get_for_user(
        self, user_id: UUID, result_id: UUID
    ) -> CertifiedLetterResultRecord: ...


class InMemoryCertifiedLetterResultStore:
    def __init__(self) -> None:
        self._records: dict[UUID, CertifiedLetterResultRecord] = {}
        self._draft_index: dict[tuple[UUID, UUID], UUID] = {}
        self._lock = RLock()

    def save(self, record: CertifiedLetterResultRecord) -> CertifiedLetterResultRecord:
        with self._lock:
            existing_id = self._draft_index.get((record.user_id, record.draft_id))
            if existing_id is not None:
                return self._records[existing_id]
            self._records[record.result_id] = record
            self._draft_index[(record.user_id, record.draft_id)] = record.result_id
            return record

    def list_for_user(self, user_id: UUID) -> list[CertifiedLetterResultRecord]:
        with self._lock:
            return sorted(
                (
                    record
                    for record in self._records.values()
                    if record.user_id == user_id
                ),
                key=lambda record: record.created_at,
                reverse=True,
            )

    def get_for_user(
        self, user_id: UUID, result_id: UUID
    ) -> CertifiedLetterResultRecord:
        with self._lock:
            record = self._records.get(result_id)
            if record is None or record.user_id != user_id:
                raise CertifiedLetterResultNotFoundError(str(result_id))
            return record


class MySqlCertifiedLetterResultStore:
    def __init__(
        self, settings: MySqlSnapshotStoreSettings, *, initialize_schema: bool = True
    ) -> None:
        self._settings = settings
        self._schema_ready = False
        self._schema_lock = Lock()
        if initialize_schema:
            self.ensure_schema()

    def ensure_schema(self) -> None:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS certified_letter_results (
                        result_id CHAR(36) NOT NULL PRIMARY KEY,
                        user_id CHAR(36) NOT NULL,
                        draft_id CHAR(36) NOT NULL,
                        title VARCHAR(200) NOT NULL,
                        payload JSON NOT NULL,
                        created_at DATETIME(6) NOT NULL,
                        updated_at DATETIME(6) NOT NULL,
                        UNIQUE KEY uq_certified_letter_user_draft (user_id, draft_id),
                        INDEX idx_certified_letter_user_created (user_id, created_at)
                    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci
                    """
                )
            connection.commit()
            self._schema_ready = True
        finally:
            connection.close()

    def save(self, record: CertifiedLetterResultRecord) -> CertifiedLetterResultRecord:
        self._ensure_schema_ready()
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO certified_letter_results (
                        result_id, user_id, draft_id, title, payload, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE result_id = result_id
                    """,
                    (
                        str(record.result_id),
                        str(record.user_id),
                        str(record.draft_id),
                        record.title,
                        json.dumps(
                            record.result.model_dump(mode="json"), ensure_ascii=False
                        ),
                        _mysql_datetime(record.created_at),
                        _mysql_datetime(record.updated_at),
                    ),
                )
                cursor.execute(
                    """
                    SELECT * FROM certified_letter_results
                    WHERE user_id = %s AND draft_id = %s
                    """,
                    (str(record.user_id), str(record.draft_id)),
                )
                row = cursor.fetchone()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return _record_from_row(row)

    def list_for_user(self, user_id: UUID) -> list[CertifiedLetterResultRecord]:
        self._ensure_schema_ready()
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM certified_letter_results
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    """,
                    (str(user_id),),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()
        return [_record_from_row(row) for row in rows]

    def get_for_user(
        self, user_id: UUID, result_id: UUID
    ) -> CertifiedLetterResultRecord:
        self._ensure_schema_ready()
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM certified_letter_results
                    WHERE user_id = %s AND result_id = %s
                    """,
                    (str(user_id), str(result_id)),
                )
                row = cursor.fetchone()
        finally:
            connection.close()
        if row is None:
            raise CertifiedLetterResultNotFoundError(str(result_id))
        return _record_from_row(row)

    def _ensure_schema_ready(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if not self._schema_ready:
                self.ensure_schema()

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


class CertifiedLetterResultService:
    def __init__(
        self,
        drafts: CertifiedLetterService,
        repository: CertifiedLetterResultRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._drafts = drafts
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._uuid_factory = uuid_factory or uuid4

    def save(self, user_id: UUID, draft_id: UUID) -> CertifiedLetterResultDetail:
        job = self._drafts.get(draft_id)
        if (
            job.status is not CertifiedLetterJobStatus.COMPLETED
            or job.outcome is not CertifiedLetterOutcome.DRAFT_READY
            or job.result is None
        ):
            raise CertifiedLetterResultNotReadyError(str(draft_id))
        now = self._clock()
        record = self._repository.save(
            CertifiedLetterResultRecord(
                result_id=self._uuid_factory(),
                user_id=user_id,
                draft_id=draft_id,
                title=job.result.document.title,
                result=job.result,
                created_at=now,
                updated_at=now,
            )
        )
        return _to_detail(record)

    def list(self, user_id: UUID) -> CertifiedLetterResultList:
        return CertifiedLetterResultList(
            items=[
                _to_summary(record)
                for record in self._repository.list_for_user(user_id)
            ]
        )

    def get(self, user_id: UUID, result_id: UUID) -> CertifiedLetterResultDetail:
        return _to_detail(self._repository.get_for_user(user_id, result_id))


def create_certified_letter_result_store_from_env() -> CertifiedLetterResultRepository:
    settings = MySqlSnapshotStoreSettings.from_env()
    if settings is None:
        return InMemoryCertifiedLetterResultStore()
    return MySqlCertifiedLetterResultStore(settings, initialize_schema=False)


def _record_from_row(row: dict) -> CertifiedLetterResultRecord:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return CertifiedLetterResultRecord(
        result_id=UUID(row["result_id"]),
        user_id=UUID(row["user_id"]),
        draft_id=UUID(row["draft_id"]),
        title=row["title"],
        result=CertifiedLetterDraftResult.model_validate(payload),
        created_at=_utc_datetime(row["created_at"]),
        updated_at=_utc_datetime(row["updated_at"]),
    )


def _mysql_datetime(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_datetime(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _to_summary(record: CertifiedLetterResultRecord) -> CertifiedLetterResultSummary:
    return CertifiedLetterResultSummary(
        result_id=record.result_id,
        draft_id=record.draft_id,
        title=record.title,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _to_detail(record: CertifiedLetterResultRecord) -> CertifiedLetterResultDetail:
    return CertifiedLetterResultDetail(
        **_to_summary(record).model_dump(), result=record.result
    )
