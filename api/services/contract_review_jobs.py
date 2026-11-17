# contract_review_jobs.py
"""
Description: 계약서 검토 작업의 요청·단계·결과를 공통 JobStore 생명주기에 연결한다.
기능 전용 데이터와 공개 예외를 유지하면서 공통 저장소를 재사용한다.
Author: ooheunsu
Date: 2026-11-17
Before:
    - 계약서 검토가 동기 응답이라 작업 상태와 중복 요청을 추적할 수 없는 상태.
After:
    - queued부터 completed 또는 failed까지와 처리 단계를 조회할 수 있음.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID

from api.models.contracts import (
    ContractJobError,
    ContractJobStatus,
    ContractReviewRequest,
    ContractReviewResponse,
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
class ContractReviewJobData:
    request: ContractReviewRequest | None = None
    stage: str | None = None
    result: ContractReviewResponse | None = None
    error: ContractJobError | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class ContractReviewJob:
    review_id: UUID
    status: ContractJobStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    request: ContractReviewRequest | None = None
    stage: str | None = None
    result: ContractReviewResponse | None = None
    error: ContractJobError | None = None
    completed_at: datetime | None = None


class ContractReviewJobNotFoundError(JobNotFoundError):
    """요청한 계약서 검토 작업이 없거나 만료된 경우."""


class ContractReviewIdempotencyConflictError(IdempotencyConflictError):
    """계약서 검토 중복 방지 키를 다른 요청에 사용한 경우."""


class ContractReviewJobTransitionError(JobTransitionError):
    """허용되지 않은 계약서 검토 작업 상태 전이를 시도한 경우."""


class ContractReviewJobStore:
    def __init__(self, *, ttl: timedelta = timedelta(hours=1)) -> None:
        self.ttl = ttl
        self._store = JobStore[ContractReviewJobData](ttl=ttl)

    def create(
        self,
        *,
        review_id: UUID,
        request: ContractReviewRequest,
        request_fingerprint: str,
        idempotency_key: str | None,
        now: datetime,
    ) -> tuple[ContractReviewJob, bool]:
        try:
            job, is_new = self._store.create(
                job_id=review_id,
                request_fingerprint=request_fingerprint,
                idempotency_key=idempotency_key,
                data=ContractReviewJobData(request=request),
                now=now,
            )
        except IdempotencyConflictError as error:
            raise ContractReviewIdempotencyConflictError(*error.args) from error
        return _to_job(job), is_new

    def get(self, review_id: UUID, *, now: datetime) -> ContractReviewJob:
        try:
            return _to_job(self._store.get(review_id, now=now))
        except JobNotFoundError as error:
            raise ContractReviewJobNotFoundError(*error.args) from error

    def mark_processing(self, review_id: UUID, *, now: datetime) -> ContractReviewJob:
        return _to_job(
            self._transition(
                review_id,
                now=now,
                allowed={JobStatus.QUEUED},
                status=JobStatus.PROCESSING,
            )
        )

    def set_stage(
        self, review_id: UUID, stage: str, *, now: datetime
    ) -> ContractReviewJob:
        return _to_job(
            self._transition(
                review_id,
                now=now,
                allowed={JobStatus.PROCESSING},
                status=JobStatus.PROCESSING,
                update_data=lambda data: replace(data, stage=stage),
            )
        )

    def mark_completed(
        self,
        review_id: UUID,
        *,
        result: ContractReviewResponse,
        now: datetime,
    ) -> ContractReviewJob:
        return _to_job(
            self._transition(
                review_id,
                now=now,
                allowed={JobStatus.PROCESSING},
                status=JobStatus.COMPLETED,
                update_data=lambda data: replace(
                    data, request=None, stage=None, result=result, completed_at=now
                ),
            )
        )

    def mark_failed(
        self,
        review_id: UUID,
        *,
        error: ContractJobError,
        now: datetime,
    ) -> ContractReviewJob:
        return _to_job(
            self._transition(
                review_id,
                now=now,
                allowed={JobStatus.QUEUED, JobStatus.PROCESSING},
                status=JobStatus.FAILED,
                update_data=lambda data: replace(
                    data, request=None, stage=None, error=error, completed_at=now
                ),
            )
        )

    def _transition(
        self,
        review_id: UUID,
        *,
        allowed: set[JobStatus],
        status: JobStatus,
        now: datetime,
        update_data: Callable[[ContractReviewJobData], ContractReviewJobData]
        | None = None,
    ) -> JobRecord[ContractReviewJobData]:
        try:
            return self._store.transition(
                review_id,
                allowed=allowed,
                status=status,
                now=now,
                update_data=update_data,
            )
        except JobNotFoundError as error:
            raise ContractReviewJobNotFoundError(*error.args) from error
        except JobTransitionError as error:
            raise ContractReviewJobTransitionError(*error.args) from error


def _to_job(job: JobRecord[ContractReviewJobData]) -> ContractReviewJob:
    return ContractReviewJob(
        review_id=job.job_id,
        status=ContractJobStatus(job.status.value),
        created_at=job.created_at,
        updated_at=job.updated_at,
        expires_at=job.expires_at,
        request=job.data.request,
        stage=job.data.stage,
        result=job.data.result,
        error=job.data.error,
        completed_at=job.data.completed_at,
    )
