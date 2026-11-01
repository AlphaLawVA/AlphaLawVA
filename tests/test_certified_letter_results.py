"""내용증명 결과 저장 서비스와 사용자별 조회 격리를 검증한다."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from uuid import UUID

from api.models.certified_letters import CertifiedLetterDraftJobResponse
from api.services.certified_letter_results import (
    CertifiedLetterResultNotFoundError,
    CertifiedLetterResultService,
    InMemoryCertifiedLetterResultStore,
)


DRAFT_ID = UUID("11111111-1111-1111-1111-111111111111")
RESULT_ID = UUID("22222222-2222-2222-2222-222222222222")
USER_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
NOW = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)


def completed_job() -> CertifiedLetterDraftJobResponse:
    return CertifiedLetterDraftJobResponse.model_validate(
        {
            "draft_id": str(DRAFT_ID),
            "status": "completed",
            "outcome": "draft_ready",
            "result": {
                "document": {
                    "title": "임대차보증금 반환 요청의 내용증명",
                    "sender": {"name": "김민지", "address": "서울시 중구"},
                    "recipient": {"name": "홍길동", "address": "서울시 종로구"},
                    "body": "임대차보증금을 반환해 주시기 바랍니다.",
                },
                "strategy_summary": "계약 종료와 반환 의무를 명확히 알립니다.",
                "follow_up_actions": [
                    {"order": 1, "action": "발송 기록을 보관합니다."}
                ],
                "statute_evidence": [],
                "similar_precedents": [],
                "warnings": ["발송 전 실제 정보를 확인하세요."],
            },
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
            "completed_at": NOW.isoformat(),
        }
    )


class FakeDraftService:
    def get(self, draft_id: UUID) -> CertifiedLetterDraftJobResponse:
        if draft_id != DRAFT_ID:
            raise AssertionError("예상하지 못한 draft_id")
        return completed_job()


class CertifiedLetterResultServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = CertifiedLetterResultService(
            FakeDraftService(),
            InMemoryCertifiedLetterResultStore(),
            clock=lambda: NOW,
            uuid_factory=lambda: RESULT_ID,
        )

    def test_save_is_idempotent_and_listed_for_owner(self):
        first = self.service.save(USER_A, DRAFT_ID)
        second = self.service.save(USER_A, DRAFT_ID)

        self.assertEqual(first.result_id, RESULT_ID)
        self.assertEqual(second.result_id, RESULT_ID)
        self.assertEqual(len(self.service.list(USER_A).items), 1)
        self.assertEqual(self.service.list(USER_B).items, [])

    def test_detail_query_is_scoped_to_owner(self):
        saved = self.service.save(USER_A, DRAFT_ID)

        self.assertEqual(
            self.service.get(USER_A, saved.result_id).result.document.title,
            "임대차보증금 반환 요청의 내용증명",
        )
        with self.assertRaises(CertifiedLetterResultNotFoundError):
            self.service.get(USER_B, saved.result_id)


if __name__ == "__main__":
    unittest.main()
