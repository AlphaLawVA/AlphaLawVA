"""
Description: 특약 생성의 202 접수와 GET 조회 비동기 흐름을 검증한다.
접수·단계 기록·완료·실패·중복 방지 키·만료 오류와 단계 알림 콜백을 확인한다.
Author: ooheunsu
Date: 2026-11-17
Before:
    - 특약 생성이 동기 응답 하나로만 검증되던 상태.
After:
    - 접수 응답, 작업 상태 조회, 실패 처리와 단계 기록이 테스트로 고정됨.
"""

from __future__ import annotations

import logging
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from api.main import create_app
from api.models.contracts import SpecialClauseDraftRequest
from api.services.generation_results import InMemoryGenerationResultStore
from api.services.special_clause_jobs import SpecialClauseJobNotFoundError
from api.services.special_clause_service import SpecialClauseService
from ml.agent.contract_review_graph import ContractReviewEvidence
from ml.agent.special_clause_graph import SpecialClauseGeneration, SpecialClauseGraph
from ml.agent.stage_tracking import staged_node


class FakeRetriever:
    def retrieve(self, queries):
        return [
            ContractReviewEvidence(
                item_id=query.item_id,
                source_type="statute",
                source_id=f"law:{query.item_id}",
                title="민법 제356조",
                text="조문: 제356조(저당권의 내용)",
                rank=1,
                similarity=0.9,
                metadata={},
            )
            for query in queries
        ]


class FakeGenerator:
    def generate(self, queries, evidence, feedback=None):
        return [
            SpecialClauseGeneration(
                item_id=query.item_id,
                status="draft_ready",
                draft_text="임대인은 잔금 다음 날까지 권리 변동을 일으키지 않는다.",
                title="권리변동 금지",
                reason="이유입니다.",
                strategy="전략입니다.",
                needs_input=[],
                evidence_source_ids=[f"law:{query.item_id}"],
            )
            for query in queries
        ]


class BrokenGenerator:
    def generate(self, queries, evidence, feedback=None):
        raise KeyError("예상하지 못한 오류")


def _client(generator) -> tuple[TestClient, SpecialClauseService]:
    service = SpecialClauseService(evidence_retriever=FakeRetriever(), generator=generator)
    app = create_app(
        precedent_search_service=object(),
        certified_letter_service=object(),
        special_clause_service=service,
        generation_result_store=InMemoryGenerationResultStore(),
    )
    return TestClient(app), service


BODY = {"requests": ["권리 변동을 막는 특약"]}


class SpecialClauseAsyncApiTests(unittest.TestCase):
    def test_post_returns_202_and_get_returns_completed_result(self) -> None:
        client, _ = _client(FakeGenerator())

        accepted = client.post("/api/v1/special-clause-drafts", json=BODY)

        self.assertEqual(accepted.status_code, 202, accepted.text)
        self.assertEqual(accepted.json()["status"], "queued")
        draft_id = accepted.json()["draft_id"]
        job = client.get(f"/api/v1/special-clause-drafts/{draft_id}")
        self.assertEqual(job.status_code, 200, job.text)
        body = job.json()
        self.assertEqual(body["status"], "completed")
        self.assertIsNone(body["stage"])
        self.assertIsNone(body["error"])
        self.assertEqual(body["result"]["draft_id"], draft_id)
        self.assertEqual(body["result"]["items"][0]["status"], "draft_ready")
        self.assertIsNotNone(body["completed_at"])

    def test_unexpected_failure_marks_job_failed_without_result(self) -> None:
        client, _ = _client(BrokenGenerator())

        accepted = client.post("/api/v1/special-clause-drafts", json=BODY)
        job = client.get(f"/api/v1/special-clause-drafts/{accepted.json()['draft_id']}")

        body = job.json()
        self.assertEqual(body["status"], "failed")
        self.assertIsNone(body["result"])
        self.assertEqual(body["error"]["code"], "special_clause_generation_failed")

    def test_unknown_job_is_404(self) -> None:
        client, _ = _client(FakeGenerator())

        response = client.get(f"/api/v1/special-clause-drafts/{uuid4()}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "special_clause_job_not_found")

    def test_same_idempotency_key_reuses_job_and_conflicts_on_other_body(self) -> None:
        client, _ = _client(FakeGenerator())
        headers = {"Idempotency-Key": "key-1"}

        first = client.post("/api/v1/special-clause-drafts", json=BODY, headers=headers)
        second = client.post("/api/v1/special-clause-drafts", json=BODY, headers=headers)
        other = client.post(
            "/api/v1/special-clause-drafts",
            json={"requests": ["다른 내용의 특약 요청"]},
            headers=headers,
        )

        self.assertEqual(first.json()["draft_id"], second.json()["draft_id"])
        self.assertEqual(other.status_code, 409)
        self.assertEqual(
            other.json()["error"]["code"], "special_clause_idempotency_conflict"
        )

    def test_invalid_body_is_still_422(self) -> None:
        client, _ = _client(FakeGenerator())

        response = client.post("/api/v1/special-clause-drafts", json={"requests": []})

        self.assertEqual(response.status_code, 422)


class SpecialClauseServiceJobTests(unittest.TestCase):
    def test_submitted_job_is_queued_until_processed_and_request_is_dropped_after(self) -> None:
        service = SpecialClauseService(
            evidence_retriever=FakeRetriever(), generator=FakeGenerator()
        )
        submission = service.submit(SpecialClauseDraftRequest(**BODY))
        draft_id = submission.response.draft_id

        queued = service.get(draft_id)
        self.assertEqual(queued.status.value, "queued")
        self.assertIsNone(queued.result)

        service.process(draft_id)

        done = service.get(draft_id)
        self.assertEqual(done.status.value, "completed")
        self.assertEqual(done.result.draft_id, draft_id)
        # 처리가 끝난 작업은 사용자 요청 원문을 더 들고 있지 않는다.
        self.assertIsNone(service._jobs.get(draft_id, now=service._clock()).request)

    def test_process_skips_job_that_is_not_queued(self) -> None:
        service = SpecialClauseService(
            evidence_retriever=FakeRetriever(), generator=FakeGenerator()
        )
        draft_id = service.submit(SpecialClauseDraftRequest(**BODY)).response.draft_id
        service.process(draft_id)

        self.assertIsNone(service.process(draft_id))

    def test_stage_is_recorded_while_processing(self) -> None:
        seen: list[str | None] = []

        class RecordingRetriever(FakeRetriever):
            def retrieve(inner, queries):  # noqa: N805
                seen.append(service.get(draft_id).stage)
                return super().retrieve(queries)

        service = SpecialClauseService(
            evidence_retriever=RecordingRetriever(), generator=FakeGenerator()
        )
        draft_id = service.submit(SpecialClauseDraftRequest(**BODY)).response.draft_id

        service.process(draft_id)

        self.assertEqual(seen, ["retrieve"])
        self.assertEqual(service.get(draft_id).status.value, "completed")

    def test_expired_job_is_not_found(self) -> None:
        now = [datetime(2026, 11, 17, tzinfo=timezone.utc)]
        service = SpecialClauseService(clock=lambda: now[0])
        draft_id = service.submit(SpecialClauseDraftRequest(**BODY)).response.draft_id
        now[0] = now[0] + timedelta(hours=2)

        with self.assertRaises(SpecialClauseJobNotFoundError):
            service.get(draft_id)


class StageTrackingTests(unittest.TestCase):
    def test_staged_node_notifies_stage_and_returns_node_result(self) -> None:
        calls: list[str] = []
        node = staged_node("retrieve", lambda state: {"value": 1}, name="demo")

        result = node({"on_stage": calls.append})

        self.assertEqual(result, {"value": 1})
        self.assertEqual(calls, ["retrieve"])

    def test_failing_callback_does_not_stop_node(self) -> None:
        def broken(_stage: str) -> None:
            raise RuntimeError("콜백 오류")

        node = staged_node("scope", lambda state: {"ok": True}, name="demo")

        self.assertEqual(node({"on_stage": broken}), {"ok": True})

    def test_graph_prepare_reports_stages_in_order(self) -> None:
        stages: list[str] = []

        SpecialClauseGraph().prepare(
            ["권리 변동을 막는 특약"],
            evidence_retriever=FakeRetriever(),
            generator=FakeGenerator(),
            on_stage=stages.append,
        )

        self.assertEqual(stages, ["scope", "retrieve", "retrieve", "generate", "validate"])

    def test_graph_prepare_works_without_callback(self) -> None:
        result = SpecialClauseGraph().prepare(
            ["권리 변동을 막는 특약"],
            evidence_retriever=FakeRetriever(),
            generator=FakeGenerator(),
        )

        self.assertEqual(len(result.generations), 1)


class StageTimingLogTests(unittest.TestCase):
    def test_create_app_enables_stage_timing_log_once(self) -> None:
        logger = logging.getLogger("ml.agent.stage_tracking")
        _client(FakeGenerator())
        _client(FakeGenerator())

        marked = [h for h in logger.handlers if getattr(h, "_alphalawva_stage_timing", False)]
        self.assertEqual(len(marked), 1)
        self.assertTrue(logger.isEnabledFor(logging.INFO))

    def test_timing_is_logged_for_each_node(self) -> None:
        _client(FakeGenerator())

        with self.assertLogs("ml.agent.stage_tracking", level="INFO") as captured:
            SpecialClauseGraph().prepare(
                ["권리 변동을 막는 특약"],
                evidence_retriever=FakeRetriever(),
                generator=FakeGenerator(),
            )

        self.assertTrue(any("stage=generate" in line for line in captured.output))
        self.assertTrue(all("elapsed=" in line for line in captured.output))


if __name__ == "__main__":
    unittest.main()
