# test_precedent_router.py
"""
Description: 판례 API 라우터의 공개 경로와 검색 서비스 위임을 검증한다.
실제 검색 모델을 로드하지 않고 HTTP 계층의 책임과 응답 모델 연결을 확인한다.
Author: choeminju
Date: 2026-10-14
Before:
    - 판례 검색·상세·내부 원문 API 라우터가 정의된 상태.
After:
    - 세 공개 경로와 서비스 의존성 위임이 단위 테스트로 검증됨.
"""

import unittest
from types import SimpleNamespace
from uuid import UUID

from fastapi.routing import APIRoute

from api.models.precedents import PrecedentSearchRequest
from api.routers.precedents import (
    get_precedent_detail,
    get_precedent_full_text,
    get_precedent_search_service,
    router,
    search_precedents,
)


SEARCH_ID = UUID("12345678-1234-5678-1234-567812345678")


class FakeSearchService:
    def __init__(self):
        self.calls = []

    def search(self, payload):
        self.calls.append(("search", payload))
        return "search-response"

    def get_detail(self, search_id, precedent_id):
        self.calls.append(("detail", search_id, precedent_id))
        return "detail-response"

    def get_full_text(self, precedent_id):
        self.calls.append(("full-text", precedent_id))
        return "full-text-response"


class PrecedentRouterTests(unittest.TestCase):
    def test_declares_expected_paths_methods_and_response_models(self):
        routes = {
            (route.path, frozenset(route.methods)): route
            for route in router.routes
            if isinstance(route, APIRoute)
        }

        search_route = routes[("/api/v1/precedent-searches", frozenset({"POST"}))]
        detail_route = routes[
            (
                "/api/v1/precedent-searches/{search_id}/results/{precedent_id}",
                frozenset({"GET"}),
            )
        ]
        full_text_route = routes[
            (
                "/api/v1/precedents/{precedent_id}/full-text",
                frozenset({"GET"}),
            )
        ]

        self.assertEqual(search_route.response_model.__name__, "PrecedentSearchResponse")
        self.assertEqual(detail_route.response_model.__name__, "PrecedentDetailResponse")
        self.assertEqual(
            full_text_route.response_model.__name__,
            "PrecedentFullTextResponse",
        )

    def test_route_functions_delegate_to_search_service(self):
        service = FakeSearchService()
        payload = PrecedentSearchRequest(query="보증금 반환")

        search_response = search_precedents(payload, service)
        detail_response = get_precedent_detail(SEARCH_ID, "64441", service)
        full_text_response = get_precedent_full_text("64441", service)

        self.assertEqual(search_response, "search-response")
        self.assertEqual(detail_response, "detail-response")
        self.assertEqual(full_text_response, "full-text-response")
        self.assertEqual(
            service.calls,
            [
                ("search", payload),
                ("detail", SEARCH_ID, "64441"),
                ("full-text", "64441"),
            ],
        )

    def test_dependency_reads_service_from_application_state(self):
        service = FakeSearchService()
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(precedent_search_service=service)
            )
        )

        self.assertIs(get_precedent_search_service(request), service)

    def test_dependency_rejects_unconfigured_application(self):
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        with self.assertRaises(RuntimeError):
            get_precedent_search_service(request)


if __name__ == "__main__":
    unittest.main()
