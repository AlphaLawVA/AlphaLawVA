"""
Description: 계약서 검토의 202 접수와 GET 조회 비동기 흐름을 검증한다.
스냅샷 사전 확인, 완료·실패, 중복 방지 키, 만료, 단계 기록과 결과 저장 연동을 확인한다.
Author: ooheunsu
Date: 2026-11-17
Before:
    - 계약서 검토가 동기 응답 하나로만 검증되던 상태.
After:
    - 접수 응답, 작업 상태 조회, 실패 처리와 단계 기록이 테스트로 고정됨.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from api.main import create_app
from api.models.contracts import ContractReviewRequest
from api.services.confirmed_snapshot_store import ConfirmedSnapshotStore
from api.services.contract_review_jobs import ContractReviewJobNotFoundError
from api.services.contract_review_service import ContractReviewService
from api.services.generation_results import InMemoryGenerationResultStore
from ml.agent.contract_review_graph import ContractReviewGraph
from tests import test_contract_workflow_api as workflow

FakeAnalyzer = workflow.FakeAnalyzer
FakeEvidenceRetriever = workflow.FakeEvidenceRetriever


class BrokenAnalyzer:
    def analyze(self, *_args, **_kwargs):
        raise KeyError("예상하지 못한 오류")


def _snapshot():
    return workflow.ContractWorkflowApiTests(
        "test_contract_review_requires_existing_snapshot"
    )._contract_snapshot()


def _build(analyzer=None, retriever=None):
    store = ConfirmedSnapshotStore()
    contract = store.save_contract(_snapshot())
    service = ContractReviewService(
        store,
        evidence_retriever=retriever or FakeEvidenceRetriever(),
        analyzer=analyzer or FakeAnalyzer(),
    )
    app = create_app(
        precedent_search_service=object(),
        certified_letter_service=object(),
        contract_review_service=service,
        confirmed_snapshot_store=store,
        generation_result_store=InMemoryGenerationResultStore(),
    )
    return TestClient(app), service, str(contract.snapshot_id)


class ContractReviewAsyncApiTests(unittest.TestCase):
    def test_post_returns_202_and_get_returns_completed_result(self) -> None:
        client, _, snapshot_id = _build()

        accepted = client.post(
            "/api/v1/contract-reviews", json={"contract_snapshot_id": snapshot_id}
        )

        self.assertEqual(accepted.status_code, 202, accepted.text)
        self.assertEqual(accepted.json()["status"], "queued")
        review_id = accepted.json()["review_id"]
        job = client.get(f"/api/v1/contract-reviews/{review_id}")
        self.assertEqual(job.status_code, 200, job.text)
        body = job.json()
        self.assertEqual(body["status"], "completed")
        self.assertIsNone(body["stage"])
        self.assertEqual(body["result"]["review_id"], review_id)
        self.assertEqual(body["result"]["analysis_status"], "analyzed")

    def test_missing_snapshot_is_404_without_creating_a_job(self) -> None:
        client, _, _ = _build()

        response = client.post(
            "/api/v1/contract-reviews", json={"contract_snapshot_id": str(uuid4())}
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "snapshot_not_found")

    def test_unexpected_failure_marks_job_failed_without_result(self) -> None:
        client, _, snapshot_id = _build(analyzer=BrokenAnalyzer())

        accepted = client.post(
            "/api/v1/contract-reviews", json={"contract_snapshot_id": snapshot_id}
        )
        body = client.get(
            f"/api/v1/contract-reviews/{accepted.json()['review_id']}"
        ).json()

        self.assertEqual(body["status"], "failed")
        self.assertIsNone(body["result"])
        self.assertEqual(body["error"]["code"], "contract_review_failed")

    def test_unknown_job_is_404(self) -> None:
        client, _, _ = _build()

        response = client.get(f"/api/v1/contract-reviews/{uuid4()}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "contract_review_job_not_found")

    def test_same_idempotency_key_reuses_job_and_conflicts_on_other_body(self) -> None:
        client, _, snapshot_id = _build()
        headers = {"Idempotency-Key": "key-1"}
        body = {"contract_snapshot_id": snapshot_id}

        first = client.post("/api/v1/contract-reviews", json=body, headers=headers)
        second = client.post("/api/v1/contract-reviews", json=body, headers=headers)
        # 다른 요청 본문은 다른 스냅샷 ID로 만든다.
        service_store = client.app.state.confirmed_snapshot_store
        another = service_store.save_contract(_snapshot())
        other = client.post(
            "/api/v1/contract-reviews",
            json={"contract_snapshot_id": str(another.snapshot_id)},
            headers=headers,
        )

        self.assertEqual(first.json()["review_id"], second.json()["review_id"])
        self.assertEqual(other.status_code, 409)
        self.assertEqual(
            other.json()["error"]["code"], "contract_review_idempotency_conflict"
        )

    def test_completed_review_can_be_saved_with_the_job_id(self) -> None:
        client, service, snapshot_id = _build()
        recorded = []
        service.set_result_recorder(lambda **kwargs: recorded.append(kwargs))

        accepted = client.post(
            "/api/v1/contract-reviews", json={"contract_snapshot_id": snapshot_id}
        )

        self.assertEqual(len(recorded), 1)
        self.assertEqual(str(recorded[0]["source_id"]), accepted.json()["review_id"])


class ContractReviewServiceJobTests(unittest.TestCase):
    def test_submitted_job_is_queued_until_processed_and_request_is_dropped_after(self) -> None:
        _, service, snapshot_id = _build()
        request = ContractReviewRequest.model_validate({"contract_snapshot_id": snapshot_id})
        review_id = service.submit(request).response.review_id

        self.assertEqual(service.get(review_id).status.value, "queued")
        service.process(review_id)

        done = service.get(review_id)
        self.assertEqual(done.status.value, "completed")
        self.assertEqual(done.result.review_id, review_id)
        self.assertIsNone(service._jobs.get(review_id, now=service._clock()).request)

    def test_stages_are_recorded_while_processing(self) -> None:
        seen: list[str | None] = []

        class RecordingRetriever(FakeEvidenceRetriever):
            def retrieve(inner, queries):  # noqa: N805
                seen.append(service.get(review_id).stage)
                return super().retrieve(queries)

        _, service, snapshot_id = _build(retriever=RecordingRetriever())
        request = ContractReviewRequest.model_validate({"contract_snapshot_id": snapshot_id})
        review_id = service.submit(request).response.review_id

        service.process(review_id)

        self.assertEqual(seen, ["retrieve"])

    def test_expired_job_is_not_found(self) -> None:
        now = [datetime(2026, 11, 17, tzinfo=timezone.utc)]
        store = ConfirmedSnapshotStore()
        contract = store.save_contract(_snapshot())
        service = ContractReviewService(store, clock=lambda: now[0])
        review_id = service.submit(
            ContractReviewRequest(contract_snapshot_id=contract.snapshot_id)
        ).response.review_id
        now[0] += timedelta(hours=2)

        with self.assertRaises(ContractReviewJobNotFoundError):
            service.get(review_id)


class ContractReviewGraphStageTests(unittest.TestCase):
    def test_prepare_reports_stages_in_order(self) -> None:
        stages: list[str] = []

        ContractReviewGraph().prepare(
            _snapshot(),
            None,
            evidence_retriever=FakeEvidenceRetriever(),
            analyzer=FakeAnalyzer(),
            on_stage=stages.append,
        )

        self.assertEqual(
            stages, ["precheck", "retrieve", "retrieve", "analyze", "validate"]
        )


if __name__ == "__main__":
    unittest.main()
