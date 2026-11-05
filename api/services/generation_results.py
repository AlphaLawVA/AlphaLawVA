# generation_results.py
"""
Description: 계약서 검토·특약 생성 결과를 잠시 보관했다가 사용자가 원하면 내 보관함에 저장한다.
저장은 소유자 기준으로 조회하며 같은 결과를 다시 저장해도 한 건만 남긴다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 생성 결과가 응답으로만 전달되고 사용자가 다시 볼 수 있게 저장하는 방법이 없는 상태.
After:
    - 최근 생성 결과를 시간·개수 제한으로 보관하고 요청 시 MySQL 또는 메모리에 저장함.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock, RLock
from typing import Any, Callable, Protocol
from uuid import UUID, uuid4

import pymysql
from pymysql.cursors import DictCursor

from api.models.generation_results import (
    GenerationResultDetail,
    GenerationResultKind,
    GenerationResultList,
    GenerationResultSummary,
)
from api.services.mysql_snapshot_store import MySqlSnapshotStoreSettings

DEFAULT_RECENT_TTL = timedelta(hours=1)
DEFAULT_MAX_RECENT = 200


class GenerationResultNotFoundError(Exception):
    """해당 사용자가 소유한 저장 결과가 없는 경우."""


class GenerationResultSourceNotFoundError(Exception):
    """저장하려는 생성 결과가 없거나 보관 시간이 지난 경우."""


class GenerationResultNotReadyError(Exception):
    """저장할 수 있는 완성 결과가 아닌 경우."""


@dataclass(frozen=True)
class GenerationResultRecord:
    result_id: UUID
    user_id: UUID
    kind: GenerationResultKind
    source_id: UUID
    title: str
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class GenerationResultRepository(Protocol):
    def save(self, record: GenerationResultRecord) -> GenerationResultRecord: ...
    def list_for_user(self, user_id: UUID) -> list[GenerationResultRecord]: ...
    def get_for_user(self, user_id: UUID, result_id: UUID) -> GenerationResultRecord: ...


class InMemoryGenerationResultStore:
    def __init__(self) -> None:
        self._records: dict[UUID, GenerationResultRecord] = {}
        self._lock = RLock()

    def save(self, record: GenerationResultRecord) -> GenerationResultRecord:
        with self._lock:
            for existing in self._records.values():
                if (
                    existing.user_id == record.user_id
                    and existing.kind == record.kind
                    and existing.source_id == record.source_id
                ):
                    return existing
            self._records[record.result_id] = record
            return record

    def list_for_user(self, user_id: UUID) -> list[GenerationResultRecord]:
        with self._lock:
            records = [r for r in self._records.values() if r.user_id == user_id]
        return sorted(records, key=lambda record: record.created_at, reverse=True)

    def get_for_user(self, user_id: UUID, result_id: UUID) -> GenerationResultRecord:
        with self._lock:
            record = self._records.get(result_id)
        if record is None or record.user_id != user_id:
            raise GenerationResultNotFoundError(str(result_id))
        return record


class MySqlGenerationResultStore:
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
                    CREATE TABLE IF NOT EXISTS generation_results (
                        result_id CHAR(36) NOT NULL PRIMARY KEY,
                        user_id CHAR(36) NOT NULL,
                        kind VARCHAR(40) NOT NULL,
                        source_id CHAR(36) NOT NULL,
                        title VARCHAR(200) NOT NULL,
                        payload JSON NOT NULL,
                        created_at DATETIME(6) NOT NULL,
                        updated_at DATETIME(6) NOT NULL,
                        UNIQUE KEY uq_generation_user_source (user_id, kind, source_id),
                        INDEX idx_generation_user_created (user_id, created_at)
                    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci
                    """
                )
            connection.commit()
            self._schema_ready = True
        finally:
            connection.close()

    def save(self, record: GenerationResultRecord) -> GenerationResultRecord:
        self._ensure_schema_ready()
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO generation_results (
                        result_id, user_id, kind, source_id, title, payload,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE result_id = result_id
                    """,
                    (
                        str(record.result_id),
                        str(record.user_id),
                        record.kind.value,
                        str(record.source_id),
                        record.title,
                        json.dumps(record.payload, ensure_ascii=False),
                        _mysql_datetime(record.created_at),
                        _mysql_datetime(record.updated_at),
                    ),
                )
                cursor.execute(
                    """
                    SELECT * FROM generation_results
                    WHERE user_id = %s AND kind = %s AND source_id = %s
                    """,
                    (str(record.user_id), record.kind.value, str(record.source_id)),
                )
                row = cursor.fetchone()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return _record_from_row(row)

    def list_for_user(self, user_id: UUID) -> list[GenerationResultRecord]:
        self._ensure_schema_ready()
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM generation_results
                    WHERE user_id = %s ORDER BY created_at DESC
                    """,
                    (str(user_id),),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()
        return [_record_from_row(row) for row in rows]

    def get_for_user(self, user_id: UUID, result_id: UUID) -> GenerationResultRecord:
        self._ensure_schema_ready()
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM generation_results
                    WHERE user_id = %s AND result_id = %s
                    """,
                    (str(user_id), str(result_id)),
                )
                row = cursor.fetchone()
        finally:
            connection.close()
        if row is None:
            raise GenerationResultNotFoundError(str(result_id))
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


@dataclass(frozen=True)
class _RecentResult:
    kind: GenerationResultKind
    title: str
    payload: dict[str, Any]
    saveable: bool
    recorded_at: datetime


class GenerationResultService:
    """최근 생성 결과를 잠시 보관하고 사용자의 요청으로 내 보관함에 저장한다."""

    def __init__(
        self,
        repository: GenerationResultRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
        ttl: timedelta = DEFAULT_RECENT_TTL,
        max_recent: int = DEFAULT_MAX_RECENT,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._uuid_factory = uuid_factory or uuid4
        self._ttl = ttl
        self._max_recent = max_recent
        self._recent: OrderedDict[UUID, _RecentResult] = OrderedDict()
        self._lock = Lock()

    def record(
        self,
        *,
        kind: GenerationResultKind,
        source_id: UUID,
        title: str,
        payload: dict[str, Any],
        saveable: bool,
    ) -> None:
        """방금 만든 생성 결과를 저장 요청에 대비해 잠시 보관한다."""

        with self._lock:
            self._recent[source_id] = _RecentResult(
                kind=kind,
                title=title[:200],
                payload=payload,
                saveable=saveable,
                recorded_at=self._clock(),
            )
            self._recent.move_to_end(source_id)
            while len(self._recent) > self._max_recent:
                self._recent.popitem(last=False)

    def save(
        self, user_id: UUID, kind: GenerationResultKind, source_id: UUID
    ) -> GenerationResultDetail:
        now = self._clock()
        with self._lock:
            recent = self._recent.get(source_id)
            if recent is not None and now - recent.recorded_at > self._ttl:
                del self._recent[source_id]
                recent = None
        if recent is None or recent.kind != kind:
            raise GenerationResultSourceNotFoundError(str(source_id))
        if not recent.saveable:
            raise GenerationResultNotReadyError(str(source_id))
        record = self._repository.save(
            GenerationResultRecord(
                result_id=self._uuid_factory(),
                user_id=user_id,
                kind=kind,
                source_id=source_id,
                title=recent.title,
                payload=recent.payload,
                created_at=now,
                updated_at=now,
            )
        )
        return _to_detail(record)

    def list(self, user_id: UUID) -> GenerationResultList:
        return GenerationResultList(
            items=[_to_summary(r) for r in self._repository.list_for_user(user_id)]
        )

    def get(self, user_id: UUID, result_id: UUID) -> GenerationResultDetail:
        return _to_detail(self._repository.get_for_user(user_id, result_id))


def create_generation_result_store_from_env() -> GenerationResultRepository:
    settings = MySqlSnapshotStoreSettings.from_env()
    if settings is None:
        return InMemoryGenerationResultStore()
    return MySqlGenerationResultStore(settings, initialize_schema=False)


def _record_from_row(row: dict) -> GenerationResultRecord:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return GenerationResultRecord(
        result_id=UUID(row["result_id"]),
        user_id=UUID(row["user_id"]),
        kind=GenerationResultKind(row["kind"]),
        source_id=UUID(row["source_id"]),
        title=row["title"],
        payload=payload,
        created_at=_utc_datetime(row["created_at"]),
        updated_at=_utc_datetime(row["updated_at"]),
    )


def _mysql_datetime(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _to_summary(record: GenerationResultRecord) -> GenerationResultSummary:
    return GenerationResultSummary(
        result_id=record.result_id,
        kind=record.kind,
        source_id=record.source_id,
        title=record.title,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _to_detail(record: GenerationResultRecord) -> GenerationResultDetail:
    return GenerationResultDetail(
        **_to_summary(record).model_dump(), payload=record.payload
    )
