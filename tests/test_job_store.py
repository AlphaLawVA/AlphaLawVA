# test_job_store.py
"""
Description: 공통 비동기 작업 저장소의 상태 전이, TTL과 중복 요청 방지를 검증한다.
동시 접수에서도 하나의 작업만 생성되는지 확인한다.
Author: choeminju
Date: 2026-11-17
Before:
    - 공통 JobStore의 생명주기와 동시성 동작을 검증하는 테스트가 없는 상태.
After:
    - 기능별 서비스가 공통 저장소를 안전하게 재사용할 수 있는 핵심 동작이 자동 검증됨.
"""

import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import UUID

from api.services.job_store import (
    IdempotencyConflictError,
    JobNotFoundError,
    JobStatus,
    JobStore,
    JobTransitionError,
    build_request_fingerprint,
    normalize_idempotency_key,
)


JOB_ID = UUID("12345678-1234-5678-1234-567812345678")
OTHER_JOB_ID = UUID("22345678-1234-5678-1234-567812345678")
NOW = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)


class JobStoreTests(unittest.TestCase):
    def test_returns_existing_job_for_same_key_and_fingerprint(self):
        identifiers = iter((JOB_ID, OTHER_JOB_ID))
        store = JobStore[dict[str, str]](uuid_factory=lambda: next(identifiers))

        first, first_is_new = store.create(
            request_fingerprint="same",
            idempotency_key=" request-1 ",
            data={"feature": "certified-letter"},
            now=NOW,
        )
        second, second_is_new = store.create(
            request_fingerprint="same",
            idempotency_key="request-1",
            data={"feature": "ignored"},
            now=NOW,
        )

        self.assertTrue(first_is_new)
        self.assertFalse(second_is_new)
        self.assertEqual(first.job_id, second.job_id)
        self.assertEqual(second.data["feature"], "certified-letter")

    def test_rejects_same_key_for_different_fingerprint(self):
        store = JobStore[None](uuid_factory=lambda: JOB_ID)
        store.create(
            request_fingerprint="first",
            idempotency_key="request-1",
            data=None,
            now=NOW,
        )

        with self.assertRaises(IdempotencyConflictError):
            store.create(
                request_fingerprint="second",
                idempotency_key="request-1",
                data=None,
                now=NOW,
            )

    def test_transitions_status_and_updates_feature_data_atomically(self):
        store = JobStore[int](uuid_factory=lambda: JOB_ID)
        store.create(
            request_fingerprint="request",
            idempotency_key=None,
            data=1,
            now=NOW,
        )

        processing = store.transition(
            JOB_ID,
            allowed={JobStatus.QUEUED},
            status=JobStatus.PROCESSING,
            update_data=lambda value: value + 1,
            now=NOW + timedelta(seconds=1),
        )

        self.assertEqual(processing.status, JobStatus.PROCESSING)
        self.assertEqual(processing.data, 2)
        with self.assertRaises(JobTransitionError):
            store.transition(
                JOB_ID,
                allowed={JobStatus.QUEUED},
                status=JobStatus.PROCESSING,
                now=NOW + timedelta(seconds=2),
            )

    def test_expiration_removes_job_and_releases_idempotency_key(self):
        identifiers = iter((JOB_ID, OTHER_JOB_ID))
        store = JobStore[None](
            ttl=timedelta(seconds=1),
            uuid_factory=lambda: next(identifiers),
        )
        store.create(
            request_fingerprint="first",
            idempotency_key="request-1",
            data=None,
            now=NOW,
        )

        with self.assertRaises(JobNotFoundError):
            store.get(JOB_ID, now=NOW + timedelta(seconds=1))
        replacement, is_new = store.create(
            request_fingerprint="second",
            idempotency_key="request-1",
            data=None,
            now=NOW + timedelta(seconds=2),
        )

        self.assertTrue(is_new)
        self.assertEqual(replacement.job_id, OTHER_JOB_ID)

    def test_concurrent_duplicate_submission_creates_one_job(self):
        store = JobStore[None](uuid_factory=lambda: JOB_ID)

        def create_job():
            return store.create(
                request_fingerprint="same",
                idempotency_key="request-1",
                data=None,
                now=NOW,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: create_job(), range(2)))

        self.assertEqual([is_new for _job, is_new in results].count(True), 1)
        self.assertEqual({job.job_id for job, _is_new in results}, {JOB_ID})

    def test_fingerprint_is_stable_and_idempotency_key_is_normalized(self):
        first = build_request_fingerprint({"purpose": "반환", "amount": 10})
        second = build_request_fingerprint({"amount": 10, "purpose": "반환"})

        self.assertEqual(first, second)
        self.assertEqual(normalize_idempotency_key(" request-1 "), "request-1")
        self.assertIsNone(normalize_idempotency_key("   "))


if __name__ == "__main__":
    unittest.main()
