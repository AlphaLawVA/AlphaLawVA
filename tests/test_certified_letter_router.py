# test_certified_letter_router.py
"""내용증명 라우터의 경로, 서비스 위임과 백그라운드 작업 예약을 검증한다."""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from fastapi import BackgroundTasks
from fastapi.routing import APIRoute

from api.models.certified_letters import (
    CertifiedLetterDraftAccepted,
    CertifiedLetterDraftRequest,
)
from api.routers.certified_letters import (
    create_certified_letter_draft,
    get_certified_letter_draft,
    get_certified_letter_service,
    router,
)
from api.services.certified_letter_service import CertifiedLetterSubmission


DRAFT_ID = UUID("12345678-1234-5678-1234-567812345678")


def make_request():
    return CertifiedLetterDraftRequest.model_validate(
        {
            "purpose": "보증금을 반환받고 싶습니다.",
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
    )


class FakeCertifiedLetterService:
    def __init__(self, *, is_new=True):
        self.is_new = is_new
        self.calls = []

    def submit(self, payload, *, idempotency_key=None):
        self.calls.append(("submit", payload, idempotency_key))
        return CertifiedLetterSubmission(
            response=CertifiedLetterDraftAccepted(
                draft_id=DRAFT_ID,
                created_at=datetime(2026, 10, 22, 5, 30, tzinfo=timezone.utc),
            ),
            is_new=self.is_new,
        )

    def process(self, draft_id):
        self.calls.append(("process", draft_id))

    def get(self, draft_id):
        self.calls.append(("get", draft_id))
        return "job-response"


class CertifiedLetterRouterTests(unittest.TestCase):
    def test_declares_create_and_get_routes(self):
        routes = {
            (route.path, frozenset(route.methods)): route
            for route in router.routes
            if isinstance(route, APIRoute)
        }

        create_route = routes[
            ("/api/v1/certified-letter-drafts", frozenset({"POST"}))
        ]
        get_route = routes[
            (
                "/api/v1/certified-letter-drafts/{draft_id}",
                frozenset({"GET"}),
            )
        ]

        self.assertEqual(create_route.status_code, 202)
        self.assertEqual(
            create_route.response_model.__name__,
            "CertifiedLetterDraftAccepted",
        )
        self.assertEqual(
            get_route.response_model.__name__,
            "CertifiedLetterDraftJobResponse",
        )

    def test_create_delegates_and_schedules_only_new_job(self):
        payload = make_request()
        service = FakeCertifiedLetterService(is_new=True)
        background = BackgroundTasks()

        response = create_certified_letter_draft(
            payload,
            background,
            service,
            idempotency_key="request-1",
        )

        self.assertEqual(response.draft_id, DRAFT_ID)
        self.assertEqual(service.calls, [("submit", payload, "request-1")])
        self.assertEqual(len(background.tasks), 1)

        existing_service = FakeCertifiedLetterService(is_new=False)
        existing_background = BackgroundTasks()
        create_certified_letter_draft(
            payload,
            existing_background,
            existing_service,
            idempotency_key="request-1",
        )
        self.assertEqual(existing_background.tasks, [])

    def test_get_delegates_to_service(self):
        service = FakeCertifiedLetterService()

        response = get_certified_letter_draft(DRAFT_ID, service)

        self.assertEqual(response, "job-response")
        self.assertEqual(service.calls, [("get", DRAFT_ID)])

    def test_dependency_reads_service_from_application_state(self):
        service = FakeCertifiedLetterService()
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(certified_letter_service=service)
            )
        )

        self.assertIs(get_certified_letter_service(request), service)

        with self.assertRaises(RuntimeError):
            get_certified_letter_service(
                SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
            )


if __name__ == "__main__":
    unittest.main()
