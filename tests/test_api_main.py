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

import unittest

from fastapi.exceptions import RequestValidationError

from api.main import create_app
from api.services.precedent_case_repository import (
    InvalidPrecedentIdError,
    PrecedentCaseDataError,
    PrecedentCaseNotFoundError,
)
from api.services.ollama_similarity_explainer import OllamaSimilarityExplainer
from api.services.precedent_search_service import (
    PrecedentSearchBackendUnavailableError,
    PrecedentSearchService,
)
from api.services.precedent_search_sessions import (
    SearchResultNotFoundError,
    SearchSessionNotFoundError,
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
        self.assertIn("/api/v1/precedents/{precedent_id}/full-text", paths)

    def test_default_service_keeps_embedding_model_lazy(self):
        app = create_app()
        service = app.state.precedent_search_service

        self.assertIsInstance(service, PrecedentSearchService)
        self.assertFalse(service.retriever.is_loaded)
        self.assertIsInstance(
            service.similarity_explainer,
            OllamaSimilarityExplainer,
        )

    def test_registers_public_error_handlers(self):
        app = create_app(object())

        for error_type in (
            RequestValidationError,
            InvalidPrecedentIdError,
            PrecedentCaseNotFoundError,
            SearchSessionNotFoundError,
            SearchResultNotFoundError,
            PrecedentCaseDataError,
            PrecedentSearchBackendUnavailableError,
        ):
            with self.subTest(error_type=error_type):
                self.assertIn(error_type, app.exception_handlers)


if __name__ == "__main__":
    unittest.main()
