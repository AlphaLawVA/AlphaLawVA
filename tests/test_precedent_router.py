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

from api.models.precedents import (
    PrecedentFavoriteCreateRequest,
    PrecedentSearchRequest,
)
from api.routers.precedents import (
    create_precedent_favorite,
    delete_precedent_favorite,
    generate_favorite_similarity_explanation,
    generate_precedent_similarity_explanation,
    get_precedent_favorite,
    get_precedent_detail,
    get_precedent_full_text,
    get_precedent_search_service,
    list_precedent_favorites,
    router,
    search_precedents,
)


SEARCH_ID = UUID("12345678-1234-5678-1234-567812345678")
FAVORITE_ID = UUID("22345678-1234-5678-1234-567812345678")


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

    def generate_similarity_explanation(self, search_id, precedent_id):
        self.calls.append(("generate", search_id, precedent_id))
        return "explanation-response"

    def create_favorite(self, search_id, precedent_id):
        self.calls.append(("favorite", search_id, precedent_id))
        return "favorite-response"

    def list_favorites(self):
        self.calls.append(("favorite-list",))
        return "favorite-list-response"

    def get_favorite(self, favorite_id):
        self.calls.append(("favorite-detail", favorite_id))
        return "favorite-detail-response"

    def generate_favorite_similarity_explanation(self, favorite_id):
        self.calls.append(("favorite-generate", favorite_id))
        return "favorite-explanation-response"

    def delete_favorite(self, favorite_id):
        self.calls.append(("favorite-delete", favorite_id))


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
        explanation_route = routes[
            (
                "/api/v1/precedent-searches/{search_id}/results/{precedent_id}"
                "/similarity-explanation",
                frozenset({"POST"}),
            )
        ]
        favorite_route = routes[
            ("/api/v1/precedent-favorites", frozenset({"POST"}))
        ]
        favorite_list_route = routes[
            ("/api/v1/precedent-favorites", frozenset({"GET"}))
        ]

        self.assertEqual(search_route.response_model.__name__, "PrecedentSearchResponse")
        self.assertEqual(detail_route.response_model.__name__, "PrecedentDetailResponse")
        self.assertEqual(
            full_text_route.response_model.__name__,
            "PrecedentFullTextResponse",
        )
        self.assertEqual(
            explanation_route.response_model.__name__,
            "SimilarityExplanation",
        )
        self.assertEqual(
            favorite_route.response_model.__name__,
            "PrecedentFavoriteResponse",
        )
        self.assertEqual(
            favorite_list_route.response_model.__name__,
            "PrecedentFavoriteListResponse",
        )

    def test_route_functions_delegate_to_search_service(self):
        service = FakeSearchService()
        payload = PrecedentSearchRequest(query="보증금 반환")

        search_response = search_precedents(payload, service)
        detail_response = get_precedent_detail(SEARCH_ID, "64441", service)
        full_text_response = get_precedent_full_text("64441", service)
        explanation_response = generate_precedent_similarity_explanation(
            SEARCH_ID,
            "64441",
            service,
        )
        favorite_response = create_precedent_favorite(
            PrecedentFavoriteCreateRequest(
                search_id=SEARCH_ID,
                precedent_id="64441",
            ),
            service,
        )
        favorite_list_response = list_precedent_favorites(service)
        favorite_detail_response = get_precedent_favorite(FAVORITE_ID, service)
        favorite_explanation_response = (
            generate_favorite_similarity_explanation(FAVORITE_ID, service)
        )
        delete_response = delete_precedent_favorite(FAVORITE_ID, service)

        self.assertEqual(search_response, "search-response")
        self.assertEqual(detail_response, "detail-response")
        self.assertEqual(full_text_response, "full-text-response")
        self.assertEqual(explanation_response, "explanation-response")
        self.assertEqual(favorite_response, "favorite-response")
        self.assertEqual(favorite_list_response, "favorite-list-response")
        self.assertEqual(favorite_detail_response, "favorite-detail-response")
        self.assertEqual(
            favorite_explanation_response,
            "favorite-explanation-response",
        )
        self.assertEqual(delete_response.status_code, 204)
        self.assertEqual(
            service.calls,
            [
                ("search", payload),
                ("detail", SEARCH_ID, "64441"),
                ("full-text", "64441"),
                ("generate", SEARCH_ID, "64441"),
                ("favorite", SEARCH_ID, "64441"),
                ("favorite-list",),
                ("favorite-detail", FAVORITE_ID),
                ("favorite-generate", FAVORITE_ID),
                ("favorite-delete", FAVORITE_ID),
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
