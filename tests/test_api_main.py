# test_api_main.py
"""
Description: FastAPI 앱의 판례 라우터·서비스 의존성과 오류 처리 등록을 검증한다.
기본 앱 생성이 BGE-M3 모델을 즉시 로드하지 않는 것도 함께 확인한다.
Author: choeminju
Date: 2026-10-14
Before:
    - 판례 라우터와 도메인 오류 처리기가 FastAPI 앱에 연결된 상태.
After:
    - 앱 생성 시 경로·서비스 상태·오류 처리 등록과 지연 로딩이 검증됨.
"""

import os
import unittest
from unittest.mock import patch

from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from api.main import create_app
from api.services.precedent_case_repository import (
    InvalidPrecedentIdError,
    PrecedentCaseDataError,
    PrecedentCaseNotFoundError,
)
from api.services.gemini_similarity_explainer import GeminiSimilarityExplainer
from api.services.precedent_favorites import PrecedentFavoriteNotFoundError
from api.services.precedent_search_service import (
    PrecedentSearchBackendUnavailableError,
    PrecedentSearchService,
    SimilarityExplanationUnavailableError,
)
from api.services.precedent_search_sessions import (
    SearchResultNotFoundError,
    SearchSessionNotFoundError,
    SimilarityExplanationInProgressError,
)


class ApiMainTests(unittest.TestCase):
    def test_create_app_injects_service_and_registers_routes(self):
        service = object()
        app = create_app(service)
        paths = set(app.openapi()["paths"])

        self.assertIs(app.state.precedent_search_service, service)
        self.assertIn("/health", paths)
        self.assertIn("/api/v1/precedent-searches", paths)
        self.assertIn(
            "/api/v1/precedent-searches/{search_id}/results/{precedent_id}",
            paths,
        )
        self.assertIn("/api/v1/precedent-favorites", paths)
        self.assertIn("/api/v1/precedents/{precedent_id}/full-text", paths)

    def test_default_service_keeps_embedding_model_lazy(self):
        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "test-key",
                "PRECEDENT_SIMILARITY_GEMINI_MODEL": "gemini-3.5-flash-lite",
            },
        ):
            app = create_app()
        service = app.state.precedent_search_service

        self.assertIsInstance(service, PrecedentSearchService)
        self.assertFalse(service.retriever.is_loaded)
        self.assertIsInstance(
            service.similarity_explainer,
            GeminiSimilarityExplainer,
        )

    def test_registers_public_error_handlers(self):
        app = create_app(object())

        for error_type in (
            RequestValidationError,
            InvalidPrecedentIdError,
            PrecedentCaseNotFoundError,
            SearchSessionNotFoundError,
            SearchResultNotFoundError,
            PrecedentFavoriteNotFoundError,
            SimilarityExplanationInProgressError,
            SimilarityExplanationUnavailableError,
            PrecedentCaseDataError,
            PrecedentSearchBackendUnavailableError,
        ):
            with self.subTest(error_type=error_type):
                self.assertIn(error_type, app.exception_handlers)

    def test_serves_precedent_search_demo(self):
        response = TestClient(create_app(object())).get("/demo/precedent-search")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        self.assertIn("AlphaLawVA 판례 검색", response.text)
        self.assertIn("/api/v1/precedent-searches", response.text)
        self.assertIn('/demo/certified-letter', response.text)

    def test_serves_certified_letter_demo(self):
        response = TestClient(create_app(object())).get("/demo/certified-letter")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        self.assertIn("AlphaLawVA 내용증명 생성", response.text)
        self.assertIn("/api/v1/certified-letter-drafts", response.text)
        self.assertIn('/demo/precedent-search', response.text)


if __name__ == "__main__":
    unittest.main()
