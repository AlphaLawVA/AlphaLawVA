"""
Description: 계약서 검토·특약 생성 결과를 사용자가 원할 때 저장하는 서비스를 검증한다.
최근 생성 결과 보관, 저장의 멱등성, 소유자 한정 조회, 만료와 저장 불가 결과를 확인한다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 계약서 검토·특약 생성 결과를 내 보관함에 저장하는 기능이 없는 상태.
After:
    - 생성 직후 결과를 잠시 보관하고 사용자가 저장하면 내 보관함에 남김.
"""

import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from api.models.generation_results import GenerationResultKind
from api.services.generation_results import (
    GenerationResultNotFoundError,
    GenerationResultNotReadyError,
    GenerationResultService,
    GenerationResultSourceNotFoundError,
    InMemoryGenerationResultStore,
)

USER_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


def _service(clock=None, **kwargs):
    return GenerationResultService(
        InMemoryGenerationResultStore(), clock=clock or Clock(), **kwargs
    )


def _record(service, kind=GenerationResultKind.SPECIAL_CLAUSE, saveable=True):
    source_id = uuid4()
    service.record(
        kind=kind,
        source_id=source_id,
        title="특약 초안 · 권리 변동 금지",
        payload={"items": [{"status": "draft_ready"}]},
        saveable=saveable,
    )
    return source_id


class GenerationResultServiceTests(unittest.TestCase):
    def test_save_returns_detail_with_payload(self) -> None:
        service = _service()
        source_id = _record(service)

        detail = service.save(USER_A, GenerationResultKind.SPECIAL_CLAUSE, source_id)

        self.assertEqual(detail.kind, GenerationResultKind.SPECIAL_CLAUSE)
        self.assertEqual(detail.source_id, source_id)
        self.assertEqual(detail.payload["items"][0]["status"], "draft_ready")

    def test_save_is_idempotent_per_user_and_source(self) -> None:
        service = _service()
        source_id = _record(service)

        first = service.save(USER_A, GenerationResultKind.SPECIAL_CLAUSE, source_id)
        second = service.save(USER_A, GenerationResultKind.SPECIAL_CLAUSE, source_id)

        self.assertEqual(first.result_id, second.result_id)
        self.assertEqual(len(service.list(USER_A).items), 1)

    def test_list_and_detail_are_scoped_to_owner(self) -> None:
        service = _service()
        source_id = _record(service)
        saved = service.save(USER_A, GenerationResultKind.SPECIAL_CLAUSE, source_id)

        self.assertEqual(service.list(USER_B).items, [])
        with self.assertRaises(GenerationResultNotFoundError):
            service.get(USER_B, saved.result_id)
        self.assertEqual(service.get(USER_A, saved.result_id).result_id, saved.result_id)

    def test_other_user_can_save_same_source_separately(self) -> None:
        service = _service()
        source_id = _record(service)

        a = service.save(USER_A, GenerationResultKind.SPECIAL_CLAUSE, source_id)
        b = service.save(USER_B, GenerationResultKind.SPECIAL_CLAUSE, source_id)

        self.assertNotEqual(a.result_id, b.result_id)

    def test_unknown_source_is_not_found(self) -> None:
        with self.assertRaises(GenerationResultSourceNotFoundError):
            _service().save(USER_A, GenerationResultKind.CONTRACT_REVIEW, uuid4())

    def test_kind_must_match_recorded_kind(self) -> None:
        service = _service()
        source_id = _record(service, kind=GenerationResultKind.SPECIAL_CLAUSE)

        with self.assertRaises(GenerationResultSourceNotFoundError):
            service.save(USER_A, GenerationResultKind.CONTRACT_REVIEW, source_id)

    def test_recent_result_expires(self) -> None:
        clock = Clock()
        service = _service(clock, ttl=timedelta(minutes=30))
        source_id = _record(service)

        clock.now += timedelta(minutes=31)

        with self.assertRaises(GenerationResultSourceNotFoundError):
            service.save(USER_A, GenerationResultKind.SPECIAL_CLAUSE, source_id)

    def test_saved_result_survives_cache_expiry(self) -> None:
        clock = Clock()
        service = _service(clock, ttl=timedelta(minutes=30))
        source_id = _record(service)
        saved = service.save(USER_A, GenerationResultKind.SPECIAL_CLAUSE, source_id)

        clock.now += timedelta(hours=5)

        self.assertEqual(service.get(USER_A, saved.result_id).result_id, saved.result_id)

    def test_unsaveable_result_is_not_ready(self) -> None:
        service = _service()
        source_id = _record(service, saveable=False)

        with self.assertRaises(GenerationResultNotReadyError):
            service.save(USER_A, GenerationResultKind.SPECIAL_CLAUSE, source_id)

    def test_cache_is_bounded(self) -> None:
        service = _service(max_recent=2)
        first = _record(service)
        _record(service)
        _record(service)

        with self.assertRaises(GenerationResultSourceNotFoundError):
            service.save(USER_A, GenerationResultKind.SPECIAL_CLAUSE, first)


if __name__ == "__main__":
    unittest.main()
