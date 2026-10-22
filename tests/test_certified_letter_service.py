# test_certified_letter_service.py
"""내용증명 작업 접수, 중복 방지, 그래프 실행과 안전한 실패 상태를 검증한다."""

import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID

from api.models.certified_letters import (
    CertifiedLetterDraftRequest,
    CertifiedLetterJobStatus,
    CertifiedLetterOutcome,
)
from api.services.certified_letter_jobs import (
    CertifiedLetterIdempotencyConflictError,
    CertifiedLetterJobNotFoundError,
    CertifiedLetterJobStore,
)
from api.services.certified_letter_service import (
    CertifiedLetterGenerationUnavailableError,
    CertifiedLetterService,
)
from ml.agent.certified_letter.normalizer import normalize_request
from ml.agent.certified_letter.state import (
    DraftPartyData,
    LetterDraftData,
    PrecedentEvidenceData,
    StatuteEvidenceData,
    WorkflowFailure,
)


DRAFT_ID = UUID("12345678-1234-5678-1234-567812345678")


def make_request(*, purpose="계약 종료 후 보증금을 반환받고 싶습니다."):
    return CertifiedLetterDraftRequest.model_validate(
        {
            "purpose": purpose,
            "facts": "계약이 종료되었지만 보증금을 아직 반환받지 못했습니다.",
            "contract": {
                "contract_type": "jeonse",
                "property": {
                    "address": "서울특별시 중구 세종대로 1",
                    "detail_address": "101동 101호",
                },
                "period": {
                    "start_date": "2024-10-01",
                    "end_date": "2026-09-30",
                },
                "payment": {
                    "deposit": 100000000,
                    "monthly_rent": None,
                },
                "lessor": {
                    "name": "홍길동",
                    "address": "서울특별시 종로구 사직로 1",
                },
                "lessee": {
                    "name": "김민지",
                    "address": "서울특별시 중구 세종대로 1",
                    "detail_address": "101동 101호",
                },
            },
        }
    )


class AdvancingClock:
    def __init__(self):
        self.current = datetime(2026, 10, 22, 5, 30, tzinfo=timezone.utc)

    def __call__(self):
        value = self.current
        self.current += timedelta(seconds=1)
        return value


class ReadyGraph:
    def __init__(self):
        self.calls = []

    def invoke(self, input):
        self.calls.append(input)
        request = input["request"]
        normalized = normalize_request(request)
        return {
            "request": request,
            "normalized_request": normalized,
            "selected_statutes": (
                StatuteEvidenceData(
                    chunk_id="statute:001:4",
                    text="임대차 기간과 보증금 반환에 관한 조문",
                    similarity=0.91,
                    statute_id="001",
                    law_name="주택임대차보호법",
                    article_label="제4조",
                    effective_date="2026. 1. 1.",
                ),
            ),
            "selected_precedents": (
                PrecedentEvidenceData(
                    precedent_id="64441",
                    text="임대차 종료와 보증금 반환이 문제 된 판례",
                    similarity=0.88,
                    case_number="2006다62492",
                    decision_date="2008. 9. 25.",
                    summary="보증금 반환과 관련된 유사 판례입니다.",
                    source_url=(
                        "https://law.go.kr/LSW/precInfoP.do?mode=0&precSeq=64441"
                    ),
                ),
            ),
            "draft": LetterDraftData(
                title="임대차보증금 반환 촉구서",
                sender=DraftPartyData(
                    name="김민지",
                    address="서울특별시 중구 세종대로 1",
                    detail_address="101동 101호",
                ),
                recipient=DraftPartyData(
                    name="홍길동",
                    address="서울특별시 종로구 사직로 1",
                ),
                body=(
                    "「주택임대차보호법」 제4조를 근거로 이 내용증명을 받은 "
                    "날부터 7일 이내 보증금 반환을 요청합니다."
                ),
                strategy_summary="보증금 반환 의무와 이행 기한을 통지합니다.",
                follow_up_actions=("발송 전 계약 정보와 주소를 확인합니다.",),
                warnings=("이 문서는 법률 검토 전 초안입니다.",),
            ),
            "outcome": "draft_ready",
        }


class FailureGraph:
    def invoke(self, _input):
        return {
            "failure": WorkflowFailure(
                code="certified_letter_validation_failed",
                message="생성된 초안이 검증을 통과하지 못했습니다.",
            )
        }


class ExplodingGraph:
    def invoke(self, _input):
        raise RuntimeError("provider raw error must not be public")


class CertifiedLetterServiceTests(unittest.TestCase):
    def make_service(self, graph):
        return CertifiedLetterService(
            graph=graph,
            clock=AdvancingClock(),
            uuid_factory=lambda: DRAFT_ID,
        )

    def test_submits_processes_and_reads_completed_draft(self):
        graph = ReadyGraph()
        service = self.make_service(graph)
        submission = service.submit(make_request(), idempotency_key="request-1")

        self.assertTrue(submission.is_new)
        self.assertEqual(submission.response.status, CertifiedLetterJobStatus.QUEUED)

        state = service.process(submission.response.draft_id)
        response = service.get(submission.response.draft_id)

        self.assertEqual(response.status, CertifiedLetterJobStatus.COMPLETED)
        self.assertEqual(response.outcome, CertifiedLetterOutcome.DRAFT_READY)
        self.assertEqual(response.result.document.sender.name, "김민지")
        self.assertEqual(response.result.statute_evidence[0].effective_date.isoformat(), "2026-01-01")
        self.assertEqual(response.result.similar_precedents[0].decision_date.isoformat(), "2008-09-25")
        self.assertIn("수령 후 7일 이내", " ".join(response.result.warnings))
        self.assertEqual(len(graph.calls), 1)
        self.assertEqual(state["outcome"], "draft_ready")

    def test_returns_existing_job_for_same_idempotency_key_and_request(self):
        service = self.make_service(ReadyGraph())

        first = service.submit(make_request(), idempotency_key=" request-1 ")
        second = service.submit(make_request(), idempotency_key="request-1")

        self.assertTrue(first.is_new)
        self.assertFalse(second.is_new)
        self.assertEqual(first.response.draft_id, second.response.draft_id)

    def test_rejects_same_idempotency_key_for_different_request(self):
        service = self.make_service(ReadyGraph())
        service.submit(make_request(), idempotency_key="request-1")

        with self.assertRaises(CertifiedLetterIdempotencyConflictError):
            service.submit(
                make_request(purpose="임대차계약 해지를 통보하고 싶습니다."),
                idempotency_key="request-1",
            )

    def test_records_validation_failure_without_draft(self):
        service = self.make_service(FailureGraph())
        submission = service.submit(make_request())

        service.process(submission.response.draft_id)
        response = service.get(submission.response.draft_id)

        self.assertEqual(response.status, CertifiedLetterJobStatus.FAILED)
        self.assertEqual(
            response.error.code,
            "certified_letter_validation_failed",
        )
        self.assertIsNone(response.result)

    def test_hides_provider_exception_in_safe_failed_state(self):
        service = self.make_service(ExplodingGraph())
        submission = service.submit(make_request())

        with self.assertLogs(
            "api.services.certified_letter_service",
            level="ERROR",
        ):
            service.process(submission.response.draft_id)
        response = service.get(submission.response.draft_id)

        self.assertEqual(response.status, CertifiedLetterJobStatus.FAILED)
        self.assertEqual(response.error.code, "certified_letter_generation_failed")
        self.assertNotIn("provider raw error", response.error.message)

    def test_rejects_submission_when_graph_is_unconfigured(self):
        with self.assertRaises(CertifiedLetterGenerationUnavailableError):
            CertifiedLetterService().submit(make_request())


class CertifiedLetterJobStoreTests(unittest.TestCase):
    def test_expires_job_and_idempotency_key(self):
        store = CertifiedLetterJobStore(ttl=timedelta(seconds=1))
        now = datetime(2026, 10, 22, 5, 30, tzinfo=timezone.utc)
        store.create(
            draft_id=DRAFT_ID,
            request_fingerprint="fingerprint",
            idempotency_key="request-1",
            now=now,
        )

        with self.assertRaises(CertifiedLetterJobNotFoundError):
            store.get(DRAFT_ID, now=now + timedelta(seconds=1))

        new_job, is_new = store.create(
            draft_id=UUID("22345678-1234-5678-1234-567812345678"),
            request_fingerprint="other",
            idempotency_key="request-1",
            now=now + timedelta(seconds=2),
        )
        self.assertTrue(is_new)
        self.assertNotEqual(new_job.draft_id, DRAFT_ID)


if __name__ == "__main__":
    unittest.main()
