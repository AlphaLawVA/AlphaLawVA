# certified_letter_jobs.py
"""
Description: 내용증명 비동기 생성 작업을 TTL이 있는 메모리 저장소에 보관한다.
중복 생성 키와 상태 전이를 잠금으로 보호하고 영구 저장소 교체 경계를 제공한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 장시간 내용증명 생성의 상태와 중복 요청을 관리할 저장소가 없는 상태.
After:
    - 알파 API에서 queued부터 completed 또는 failed까지 작업을 추적할 수 있음.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from threading import RLock
from uuid import UUID

from api.models.certified_letters import (
    CertifiedLetterDraftResult,
    CertifiedLetterJobError,
    CertifiedLetterJobStatus,
    CertifiedLetterOutcome,
)


@dataclass(frozen=True)
class CertifiedLetterJob:
    draft_id: UUID
    request_fingerprint: str
    idempotency_key: str | None
    status: CertifiedLetterJobStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    outcome: CertifiedLetterOutcome | None = None
    result: CertifiedLetterDraftResult | None = None
    missing_information: tuple[str, ...] = ()
    message: str | None = None
    error: CertifiedLetterJobError | None = None
    completed_at: datetime | None = None


class CertifiedLetterJobNotFoundError(Exception):
    """요청한 작업이 없거나 만료된 경우."""


class CertifiedLetterIdempotencyConflictError(Exception):
    """같은 중복 방지 키를 다른 요청에 사용한 경우."""


class CertifiedLetterJobStore:
    def __init__(self, *, ttl: timedelta = timedelta(hours=1)) -> None:
        if ttl <= timedelta(0):
            raise ValueError("ttl은 0보다 커야 합니다.")
        self.ttl = ttl
        self._jobs: dict[UUID, CertifiedLetterJob] = {}
        self._idempotency_keys: dict[str, UUID] = {}
        self._lock = RLock()

    def create(
        self,
        *,
        draft_id: UUID,
        request_fingerprint: str,
        idempotency_key: str | None,
        now: datetime,
    ) -> tuple[CertifiedLetterJob, bool]:
        with self._lock:
            self._remove_expired(now)
            if idempotency_key is not None:
                existing_id = self._idempotency_keys.get(idempotency_key)
                if existing_id is not None:
                    existing = self._jobs[existing_id]
                    if existing.request_fingerprint != request_fingerprint:
                        raise CertifiedLetterIdempotencyConflictError(
                            "같은 Idempotency-Key를 다른 요청에 사용할 수 없습니다."
                        )
                    return existing, False

            job = CertifiedLetterJob(
                draft_id=draft_id,
                request_fingerprint=request_fingerprint,
                idempotency_key=idempotency_key,
                status=CertifiedLetterJobStatus.QUEUED,
                created_at=now,
                updated_at=now,
                expires_at=now + self.ttl,
            )
            self._jobs[draft_id] = job
            if idempotency_key is not None:
                self._idempotency_keys[idempotency_key] = draft_id
            return job, True

    def get(self, draft_id: UUID, *, now: datetime) -> CertifiedLetterJob:
        with self._lock:
            self._remove_expired(now)
            try:
                return self._jobs[draft_id]
            except KeyError as error:
                raise CertifiedLetterJobNotFoundError(str(draft_id)) from error

    def mark_processing(
        self,
        draft_id: UUID,
        *,
        now: datetime,
    ) -> CertifiedLetterJob:
        return self._transition(
            draft_id,
            now=now,
            allowed={CertifiedLetterJobStatus.QUEUED},
            status=CertifiedLetterJobStatus.PROCESSING,
        )

    def mark_completed(
        self,
        draft_id: UUID,
        *,
        outcome: CertifiedLetterOutcome,
        result: CertifiedLetterDraftResult | None,
        missing_information: tuple[str, ...],
        message: str | None,
        now: datetime,
    ) -> CertifiedLetterJob:
        return self._transition(
            draft_id,
            now=now,
            allowed={CertifiedLetterJobStatus.PROCESSING},
            status=CertifiedLetterJobStatus.COMPLETED,
            outcome=outcome,
            result=result,
            missing_information=missing_information,
            message=message,
            completed_at=now,
        )

    def mark_failed(
        self,
        draft_id: UUID,
        *,
        error: CertifiedLetterJobError,
        now: datetime,
    ) -> CertifiedLetterJob:
        return self._transition(
            draft_id,
            now=now,
            allowed={
                CertifiedLetterJobStatus.QUEUED,
                CertifiedLetterJobStatus.PROCESSING,
            },
            status=CertifiedLetterJobStatus.FAILED,
            error=error,
            completed_at=now,
        )

    def _transition(
        self,
        draft_id: UUID,
        *,
        now: datetime,
        allowed: set[CertifiedLetterJobStatus],
        status: CertifiedLetterJobStatus,
        **changes,
    ) -> CertifiedLetterJob:
        with self._lock:
            job = self.get(draft_id, now=now)
            if job.status not in allowed:
                raise RuntimeError(
                    f"허용되지 않은 내용증명 작업 상태 전이: {job.status} -> {status}"
                )
            updated = replace(
                job,
                status=status,
                updated_at=now,
                expires_at=now + self.ttl,
                **changes,
            )
            self._jobs[draft_id] = updated
            return updated

    def _remove_expired(self, now: datetime) -> None:
        expired = [
            draft_id
            for draft_id, job in self._jobs.items()
            if job.expires_at <= now
        ]
        for draft_id in expired:
            job = self._jobs.pop(draft_id)
            if job.idempotency_key is not None:
                self._idempotency_keys.pop(job.idempotency_key, None)
