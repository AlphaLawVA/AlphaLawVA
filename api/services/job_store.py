# job_store.py
"""
Description: 비동기 생성 작업의 공통 생명주기와 중복 요청 방지를 메모리에서 관리한다.
기능별 결과를 불투명한 데이터로 보관하여 서비스별 타입과 공통 상태 관리를 분리한다.
Author: choeminju
Date: 2026-11-17
Before:
    - 기능마다 UUID, TTL, 상태 전이, 중복 방지 키와 잠금 로직을 별도로 구현해야 하는 상태.
After:
    - 기능별 결과 타입을 유지하면서 공통 작업 저장소와 요청 fingerprint 도구를 재사용할 수 있음.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from threading import RLock
from typing import Generic, TypeVar
from uuid import UUID, uuid4


JobData = TypeVar("JobData")


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class JobRecord(Generic[JobData]):
    job_id: UUID
    request_fingerprint: str
    idempotency_key: str | None
    status: JobStatus
    data: JobData
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class JobNotFoundError(Exception):
    """요청한 작업이 없거나 만료된 경우."""


class IdempotencyConflictError(Exception):
    """같은 중복 방지 키를 다른 요청에 사용한 경우."""


class JobTransitionError(Exception):
    """현재 상태에서 허용되지 않는 작업 전이를 시도한 경우."""


class JobStore(Generic[JobData]):
    def __init__(
        self,
        *,
        ttl: timedelta = timedelta(hours=1),
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("ttl은 0보다 커야 합니다.")
        self.ttl = ttl
        self._uuid_factory = uuid_factory or uuid4
        self._jobs: dict[UUID, JobRecord[JobData]] = {}
        self._idempotency_keys: dict[str, UUID] = {}
        self._lock = RLock()

    def create(
        self,
        *,
        request_fingerprint: str,
        idempotency_key: str | None,
        data: JobData,
        now: datetime,
        job_id: UUID | None = None,
    ) -> tuple[JobRecord[JobData], bool]:
        with self._lock:
            self._remove_expired(now)
            normalized_key = normalize_idempotency_key(idempotency_key)
            if normalized_key is not None:
                existing_id = self._idempotency_keys.get(normalized_key)
                if existing_id is not None:
                    existing = self._jobs[existing_id]
                    if existing.request_fingerprint != request_fingerprint:
                        raise IdempotencyConflictError(
                            "같은 Idempotency-Key를 다른 요청에 사용할 수 없습니다."
                        )
                    return existing, False

            resolved_job_id = job_id or self._uuid_factory()
            job = JobRecord(
                job_id=resolved_job_id,
                request_fingerprint=request_fingerprint,
                idempotency_key=normalized_key,
                status=JobStatus.QUEUED,
                data=data,
                created_at=now,
                updated_at=now,
                expires_at=now + self.ttl,
            )
            self._jobs[resolved_job_id] = job
            if normalized_key is not None:
                self._idempotency_keys[normalized_key] = resolved_job_id
            return job, True

    def get(self, job_id: UUID, *, now: datetime) -> JobRecord[JobData]:
        with self._lock:
            self._remove_expired(now)
            return self._get(job_id)

    def transition(
        self,
        job_id: UUID,
        *,
        allowed: Collection[JobStatus],
        status: JobStatus,
        now: datetime,
        update_data: Callable[[JobData], JobData] | None = None,
    ) -> JobRecord[JobData]:
        with self._lock:
            self._remove_expired(now)
            job = self._get(job_id)
            if job.status not in allowed:
                raise JobTransitionError(
                    f"허용되지 않은 작업 상태 전이: {job.status.value} -> {status.value}"
                )
            updated = replace(
                job,
                status=status,
                data=update_data(job.data) if update_data is not None else job.data,
                updated_at=now,
                expires_at=now + self.ttl,
            )
            self._jobs[job_id] = updated
            return updated

    def _get(self, job_id: UUID) -> JobRecord[JobData]:
        try:
            return self._jobs[job_id]
        except KeyError as error:
            raise JobNotFoundError(str(job_id)) from error

    def _remove_expired(self, now: datetime) -> None:
        expired = [
            job_id for job_id, job in self._jobs.items() if job.expires_at <= now
        ]
        for job_id in expired:
            job = self._jobs.pop(job_id)
            if (
                job.idempotency_key is not None
                and self._idempotency_keys.get(job.idempotency_key) == job_id
            ):
                self._idempotency_keys.pop(job.idempotency_key, None)


def build_request_fingerprint(payload: Mapping[str, object]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def normalize_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


__all__ = [
    "IdempotencyConflictError",
    "JobNotFoundError",
    "JobRecord",
    "JobStatus",
    "JobStore",
    "JobTransitionError",
    "build_request_fingerprint",
    "normalize_idempotency_key",
]
