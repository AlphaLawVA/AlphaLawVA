# test_certified_letter_api_integration.py
"""내용증명 생성 접수·백그라운드 처리·상태 조회와 공통 오류 응답을 검증한다."""

import unittest
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import create_app
from api.services.certified_letter_service import CertifiedLetterService


DRAFT_ID = UUID("12345678-1234-5678-1234-567812345678")


def make_payload(*, purpose="계약 종료 후 보증금을 반환받고 싶습니다."):
    return {
        "purpose": purpose,
        "facts": "계약이 종료되었지만 보증금을 아직 반환받지 못했습니다.",
        "contract": {
            "contract_type": "jeonse",
            "property": {"address": "서울특별시 중구 세종대로 1"},
            "period": {
                "start_date": "2024-10-01",
                "end_date": "2026-09-30",
            },
            "payment": {"deposit": 100000000, "monthly_rent": None},
            "lessor": {
                "name": "홍길동",
                "address": "서울특별시 종로구 사직로 1",
            },
            "lessee": {
                "name": "김민지",
                "address": "서울특별시 중구 세종대로 1",
            },
        },
    }


class AdvancingClock:
    def __init__(self):
        self.current = datetime(2026, 10, 22, 5, 30, tzinfo=timezone.utc)

    def __call__(self):
        value = self.current
        self.current += timedelta(seconds=1)
        return value


class OutOfScopeGraph:
    def invoke(self, input):
        return {
            "request": input["request"],
            "outcome": "out_of_scope",
            "message": "현재 알파 버전의 지원 범위를 벗어난 요청입니다.",
        }


class CertifiedLetterApiIntegrationTests(unittest.TestCase):
    def make_client(self):
        service = CertifiedLetterService(
            graph=OutOfScopeGraph(),
            clock=AdvancingClock(),
            uuid_factory=lambda: DRAFT_ID,
        )
        return TestClient(
            create_app(
                precedent_search_service=object(),
                certified_letter_service=service,
            )
        )

    def test_accepts_and_completes_background_job(self):
        client = self.make_client()

        created = client.post(
            "/api/v1/certified-letter-drafts",
            headers={"Idempotency-Key": "request-1"},
            json=make_payload(),
        )
        fetched = client.get(
            f"/api/v1/certified-letter-drafts/{created.json()['draft_id']}"
        )

        self.assertEqual(created.status_code, 202)
        self.assertEqual(created.json()["status"], "queued")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["status"], "completed")
        self.assertEqual(fetched.json()["outcome"], "out_of_scope")
        self.assertIsNone(fetched.json()["result"])

    def test_returns_same_job_for_repeated_request(self):
        client = self.make_client()

        first = client.post(
            "/api/v1/certified-letter-drafts",
            headers={"Idempotency-Key": "request-1"},
            json=make_payload(),
        )
        second = client.post(
            "/api/v1/certified-letter-drafts",
            headers={"Idempotency-Key": "request-1"},
            json=make_payload(),
        )

        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["draft_id"], second.json()["draft_id"])

    def test_returns_conflict_for_reused_key_with_different_request(self):
        client = self.make_client()
        client.post(
            "/api/v1/certified-letter-drafts",
            headers={"Idempotency-Key": "request-1"},
            json=make_payload(),
        )

        response = client.post(
            "/api/v1/certified-letter-drafts",
            headers={"Idempotency-Key": "request-1"},
            json=make_payload(purpose="임대차계약 해지를 통보하고 싶습니다."),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["error"]["code"],
            "certified_letter_idempotency_conflict",
        )

    def test_returns_common_errors_for_missing_job_and_invalid_input(self):
        client = self.make_client()
        missing = client.get(
            "/api/v1/certified-letter-drafts/22345678-1234-5678-1234-567812345678"
        )
        invalid_payload = make_payload()
        invalid_payload["user_id"] = "legacy-user"
        invalid = client.post(
            "/api/v1/certified-letter-drafts",
            json=invalid_payload,
        )

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(
            missing.json()["error"]["code"],
            "certified_letter_not_found",
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["error"]["code"], "invalid_request")

    def test_default_app_refuses_generation_without_model_configuration(self):
        with (
            patch(
                "api.services.certified_letter_configuration.load_env_file"
            ),
            patch.dict(os.environ, {}, clear=True),
        ):
            client = TestClient(create_app(precedent_search_service=object()))

        response = client.post(
            "/api/v1/certified-letter-drafts",
            json=make_payload(),
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"]["code"],
            "generation_service_unavailable",
        )


if __name__ == "__main__":
    unittest.main()
