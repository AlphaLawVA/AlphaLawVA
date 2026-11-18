# certified_letter_jobs.py
"""
Description: 내용증명 작업의 결과·오류 상태를 공통 JobStore 생명주기에 연결한다.
기능 전용 타입과 공개 예외를 유지하면서 공통 저장소 교체 경계를 제공한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 장시간 내용증명 생성의 상태와 중복 요청을 관리할 저장소가 없는 상태.
After:
    - 알파 API에서 queued부터 completed 또는 failed까지 작업을 추적할 수 있음.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID

from api.models.certified_letters import (
    CertifiedLetterDraftResult,
    CertifiedLetterJobError,
    CertifiedLetterJobStatus,
    CertifiedLetterOutcome,
)
from api.services.job_store import (
    IdempotencyConflictError,
    JobNotFoundError,
    JobRecord,
    JobStatus,
    JobStore,
    JobTransitionError,
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


@dataclass(frozen=True)
class CertifiedLetterJobData:
    outcome: CertifiedLetterOutcome | None = None
    result: CertifiedLetterDraftResult | None = None
    missing_information: tuple[str, ...] = ()
    message: str | None = None
    error: CertifiedLetterJobError | None = None
    completed_at: datetime | None = None


class CertifiedLetterJobNotFoundError(JobNotFoundError):
    """요청한 내용증명 작업이 없거나 만료된 경우."""


class CertifiedLetterIdempotencyConflictError(IdempotencyConflictError):
    """내용증명 중복 방지 키를 다른 요청에 사용한 경우."""


class CertifiedLetterJobTransitionError(JobTransitionError):
    """허용되지 않은 내용증명 작업 상태 전이를 시도한 경우."""


class CertifiedLetterJobStore:
    def __init__(self, *, ttl: timedelta = timedelta(hours=1)) -> None:
        if ttl <= timedelta(0):
            raise ValueError("ttl은 0보다 커야 합니다.")
        self.ttl = ttl
        self._store = JobStore[CertifiedLetterJobData](ttl=ttl)

    def create(
        self,
        *,
        draft_id: UUID,
        request_fingerprint: str,
        idempotency_key: str | None,
        now: datetime,
    ) -> tuple[CertifiedLetterJob, bool]:
        try:
            job, is_new = self._store.create(
                job_id=draft_id,
                request_fingerprint=request_fingerprint,
                idempotency_key=idempotency_key,
                data=CertifiedLetterJobData(),
                now=now,
            )
        except IdempotencyConflictError as error:
            raise CertifiedLetterIdempotencyConflictError(*error.args) from error
        return _to_certified_letter_job(job), is_new

    def get(self, draft_id: UUID, *, now: datetime) -> CertifiedLetterJob:
        try:
            job = self._store.get(draft_id, now=now)
        except JobNotFoundError as error:
            raise CertifiedLetterJobNotFoundError(*error.args) from error
        return _to_certified_letter_job(job)

    def mark_processing(
        self,
        draft_id: UUID,
        *,
        now: datetime,
    ) -> CertifiedLetterJob:
        return _to_certified_letter_job(
            self._transition(
                draft_id,
                now=now,
                allowed={JobStatus.QUEUED},
                status=JobStatus.PROCESSING,
            )
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
        return _to_certified_letter_job(
            self._transition(
                draft_id,
                now=now,
                allowed={JobStatus.PROCESSING},
                status=JobStatus.COMPLETED,
                update_data=lambda data: replace(
                    data,
                    outcome=outcome,
                    result=result,
                    missing_information=missing_information,
                    message=message,
                    completed_at=now,
                ),
            )
        )

    def mark_failed(
        self,
        draft_id: UUID,
        *,
        error: CertifiedLetterJobError,
        now: datetime,
    ) -> CertifiedLetterJob:
        return _to_certified_letter_job(
            self._transition(
                draft_id,
                now=now,
                allowed={
                    JobStatus.QUEUED,
                    JobStatus.PROCESSING,
                },
                status=JobStatus.FAILED,
                update_data=lambda data: replace(
                    data,
                    error=error,
                    completed_at=now,
                ),
            )
        )

    def _transition(
        self,
        draft_id: UUID,
        *,
        allowed: set[JobStatus],
        status: JobStatus,
        now: datetime,
        update_data: (
            Callable[[CertifiedLetterJobData], CertifiedLetterJobData] | None
        ) = None,
    ) -> JobRecord[CertifiedLetterJobData]:
        try:
            return self._store.transition(
                draft_id,
                allowed=allowed,
                status=status,
                now=now,
                update_data=update_data,
            )
        except JobNotFoundError as error:
            raise CertifiedLetterJobNotFoundError(*error.args) from error
        except JobTransitionError as error:
            raise CertifiedLetterJobTransitionError(*error.args) from error


def _to_certified_letter_job(
    job: JobRecord[CertifiedLetterJobData],
) -> CertifiedLetterJob:
    return CertifiedLetterJob(
        draft_id=job.job_id,
        request_fingerprint=job.request_fingerprint,
        idempotency_key=job.idempotency_key,
        status=CertifiedLetterJobStatus(job.status.value),
        created_at=job.created_at,
        updated_at=job.updated_at,
        expires_at=job.expires_at,
        outcome=job.data.outcome,
        result=job.data.result,
        missing_information=job.data.missing_information,
        message=job.data.message,
        error=job.data.error,
        completed_at=job.data.completed_at,
    )
