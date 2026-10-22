# test_precedent_api_integration.py
"""
Description: 판례 검색·상세·내부 원문 API를 실제 HTTP 요청과 OpenAPI로 검증한다.
임시 판례 원본과 가짜 검색기를 사용하여 모델 다운로드 없이 전체 API 흐름을 확인한다.
Author: choeminju
Date: 2026-10-14
Before:
    - 판례 서비스와 라우터 및 공통 오류 처리가 FastAPI 앱에 연결된 상태.
After:
    - 정상 검색 흐름과 400·404·422·503 오류 및 OpenAPI 계약이 통합 검증됨.
"""

import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from api.main import create_app
from api.models.precedents import SimilarityExplanationMethod
from api.services.precedent_case_repository import PrecedentCaseRepository
from api.services.precedent_search_service import PrecedentSearchService
from ml.rag.precedent_retriever import (
    PrecedentChunkMatch,
    PrecedentSearchResult,
)


class FakeRetriever:
    def __init__(self):
        self.calls = []

    def search(self, query, *, top_k=10):
        self.calls.append((query, top_k))
        chunk = PrecedentChunkMatch(
            rank=1,
            chunk_id="precedent:64441:reason:0001",
            distance=0.08,
            similarity=0.92,
            text="임대인이 근저당권을 실행하여 임의경매를 신청하였다.",
            chunk_type="이유",
            section="이유",
            section_chunk_index=0,
            metadata={"precedent_id": "64441"},
        )
        return [
            PrecedentSearchResult(
                rank=1,
                precedent_id="64441",
                distance=0.08,
                similarity=0.92,
                text=chunk.text,
                case_no="2006다62492,62508",
                case_no_list="2006다62492,2006다62508",
                case_name="임대차보증금등·지료등",
                court_name="대법원",
                decision_date="2008-09-25",
                decision_year=2008,
                case_type="민사",
                judgment_type="판결",
                source_path=None,
                matched_chunks=[chunk],
                metadata={},
            )
        ]


class FailingRetriever:
    def search(self, _query, *, top_k=10):
        raise FileNotFoundError(f"missing vector DB for top_k={top_k}")


class FakeExplainer:
    model_name = "local-test-model"
    method = SimilarityExplanationMethod.GEMINI

    def __init__(self):
        self.calls = 0

    def explain(self, **_kwargs):
        self.calls += 1
        return "사용자 상황과 이 판례는 임대차 종료가 문제 된 점에서 유사합니다."


class PrecedentApiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.cases_dir = Path(self.temporary_directory.name)
        case_data = {
            "판례일련번호": "64441",
            "사건번호": "2006다62492,62508",
            "사건번호목록": ["2006다62492", "2006다62508"],
            "사건명": "임대차보증금등·지료등",
            "법원명": "대법원",
            "선고일자": "2008-09-25",
            "사건종류명": "민사",
            "판결유형": "판결",
            "판시사항": "계약해지 효과가 발생하는지 여부",
            "판결요지": "",
            "참조조문": "민법 제543조",
            "참조판례": "",
            "주문": "사건을 원심법원에 환송한다.",
            "청구취지": "",
            "원심판결": "청주지방법원 판결",
            "이유": "임대차 종료 시점을 판단한다.",
            "생성요약": "임대차계약 종료 여부를 판단한 사건이다.",
            "쉬운요약": "경매 신청으로 임대차가 끝났다고 본 사건입니다.",
        }
        (self.cases_dir / "64441.json").write_text(
            json.dumps(case_data, ensure_ascii=False),
            encoding="utf-8",
        )
        self.repository = PrecedentCaseRepository(self.cases_dir)
        self.retriever = FakeRetriever()
        self.explainer = FakeExplainer()
        service = PrecedentSearchService(
            retriever=self.retriever,
            repository=self.repository,
            similarity_explainer=self.explainer,
        )
        self.client = TestClient(create_app(service))

    def tearDown(self):
        self.client.close()
        self.temporary_directory.cleanup()

    def test_search_detail_and_full_text_http_flow(self):
        search_response = self.client.post(
            "/api/v1/precedent-searches",
            json={"query": "임대인이 경매를 신청했습니다.", "limit": 5},
        )

        self.assertEqual(search_response.status_code, 200)
        search_payload = search_response.json()
        self.assertEqual(search_payload["status"], "completed")
        self.assertEqual(search_payload["result_count"], 1)
        self.assertEqual(
            search_payload["results"][0]["preview"]["source"],
            "easy_summary",
        )
        self.assertEqual(
            search_payload["results"][0]["matched_chunks"][0]["chunk_id"],
            "precedent:64441:reason:0001",
        )
        self.assertEqual(
            self.retriever.calls,
            [("임대인이 경매를 신청했습니다.", 5)],
        )

        detail_response = self.client.get(
            search_payload["results"][0]["links"]["detail"]
        )
        self.assertEqual(detail_response.status_code, 200)
        detail_payload = detail_response.json()
        self.assertEqual(
            detail_payload["similarity_explanation"]["status"],
            "not_generated",
        )
        self.assertIsNone(
            detail_payload["similarity_explanation"]["matched_reason"]
        )
        self.assertEqual(
            detail_payload["similarity_explanation"]["matched_chunks"][0][
                "chunk_id"
            ],
            "precedent:64441:reason:0001",
        )

        full_text_response = self.client.get(
            search_payload["results"][0]["links"]["full_text"]
        )
        self.assertEqual(full_text_response.status_code, 200)
        full_text_payload = full_text_response.json()
        self.assertEqual(
            [section["key"] for section in full_text_payload["sections"]],
            ["order", "lower_court_decision", "reason"],
        )
        self.assertTrue(
            full_text_payload["links"]["official_source"].endswith(
                "precSeq=64441"
            )
        )

    def test_favorite_can_generate_and_reuse_similarity_explanation(self):
        search_payload = self.client.post(
            "/api/v1/precedent-searches",
            json={"query": "임대인이 경매를 신청했습니다."},
        ).json()
        search_id = search_payload["search_id"]

        favorite_response = self.client.post(
            "/api/v1/precedent-favorites",
            json={"search_id": search_id, "precedent_id": "64441"},
        )
        self.assertEqual(favorite_response.status_code, 200)
        favorite_payload = favorite_response.json()
        favorite_id = favorite_payload["favorite_id"]
        self.assertEqual(
            favorite_payload["similarity_explanation"]["status"],
            "not_generated",
        )

        generated_response = self.client.post(
            f"/api/v1/precedent-favorites/{favorite_id}"
            "/similarity-explanation"
        )
        self.assertEqual(generated_response.status_code, 200)
        generated_payload = generated_response.json()
        self.assertEqual(generated_payload["status"], "generated")

        repeated_response = self.client.post(
            f"/api/v1/precedent-searches/{search_id}/results/64441"
            "/similarity-explanation"
        )
        self.assertEqual(repeated_response.status_code, 200)
        self.assertEqual(
            repeated_response.json()["matched_reason"],
            generated_payload["matched_reason"],
        )
        self.assertEqual(self.explainer.calls, 1)

        detail_payload = self.client.get(
            f"/api/v1/precedent-searches/{search_id}/results/64441"
        ).json()
        favorite_list = self.client.get("/api/v1/precedent-favorites").json()
        self.assertTrue(detail_payload["is_favorited"])
        self.assertEqual(favorite_list["favorite_count"], 1)
        self.assertEqual(
            favorite_list["favorites"][0]["similarity_explanation"][
                "matched_reason"
            ],
            generated_payload["matched_reason"],
        )

        deleted = self.client.delete(
            f"/api/v1/precedent-favorites/{favorite_id}"
        )
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(
            self.client.get("/api/v1/precedent-favorites").json()[
                "favorite_count"
            ],
            0,
        )

    def test_validation_error_uses_public_error_shape(self):
        response = self.client.post(
            "/api/v1/precedent-searches",
            json={"query": "가", "unknown": True},
        )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "invalid_request")
        self.assertGreaterEqual(len(payload["error"]["field_errors"]), 1)
        self.assertNotIn("input", payload["error"]["field_errors"][0])

    def test_lookup_errors_use_400_and_404_codes(self):
        invalid_id = self.client.get("/api/v1/precedents/abc/full-text")
        missing_case = self.client.get("/api/v1/precedents/99999/full-text")
        missing_session = self.client.get(
            f"/api/v1/precedent-searches/{uuid4()}/results/64441"
        )
        missing_favorite = self.client.get(
            f"/api/v1/precedent-favorites/{uuid4()}"
        )
        search_payload = self.client.post(
            "/api/v1/precedent-searches",
            json={"query": "보증금 반환"},
        ).json()
        missing_result = self.client.get(
            f"/api/v1/precedent-searches/{search_payload['search_id']}"
            "/results/99999"
        )

        self.assertEqual(invalid_id.status_code, 400)
        self.assertEqual(
            invalid_id.json()["error"]["code"],
            "invalid_precedent_id",
        )
        self.assertEqual(missing_case.status_code, 404)
        self.assertEqual(
            missing_case.json()["error"]["code"],
            "precedent_not_found",
        )
        self.assertEqual(missing_session.status_code, 404)
        self.assertEqual(
            missing_session.json()["error"]["code"],
            "search_session_not_found",
        )
        self.assertEqual(missing_result.status_code, 404)
        self.assertEqual(
            missing_result.json()["error"]["code"],
            "search_result_not_found",
        )
        self.assertEqual(missing_favorite.status_code, 404)
        self.assertEqual(
            missing_favorite.json()["error"]["code"],
            "precedent_favorite_not_found",
        )

    def test_similarity_generation_requires_configured_provider(self):
        service = PrecedentSearchService(
            retriever=FakeRetriever(),
            repository=self.repository,
        )
        with TestClient(create_app(service)) as client:
            search_payload = client.post(
                "/api/v1/precedent-searches",
                json={"query": "보증금 반환"},
            ).json()
            response = client.post(
                f"/api/v1/precedent-searches/{search_payload['search_id']}"
                "/results/64441/similarity-explanation"
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"]["code"],
            "similarity_explanation_unavailable",
        )

    def test_damaged_case_data_returns_503_without_internal_details(self):
        (self.cases_dir / "777.json").write_text("{broken", encoding="utf-8")

        response = self.client.get("/api/v1/precedents/777/full-text")

        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertEqual(
            payload["error"]["code"],
            "precedent_data_unavailable",
        )
        self.assertNotIn(str(self.cases_dir), response.text)

    def test_unavailable_search_backend_returns_503(self):
        service = PrecedentSearchService(
            retriever=FailingRetriever(),
            repository=self.repository,
        )
        with TestClient(create_app(service)) as client:
            response = client.post(
                "/api/v1/precedent-searches",
                json={"query": "보증금 반환"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"]["code"],
            "search_backend_unavailable",
        )

    def test_openapi_documents_public_contracts(self):
        response = self.client.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        schema = response.json()
        paths = schema["paths"]
        self.assertIn("/api/v1/precedent-searches", paths)
        self.assertIn(
            "/api/v1/precedent-searches/{search_id}/results/{precedent_id}",
            paths,
        )
        self.assertIn(
            "/api/v1/precedent-searches/{search_id}/results/{precedent_id}"
            "/similarity-explanation",
            paths,
        )
        self.assertIn("/api/v1/precedent-favorites", paths)
        self.assertIn("/api/v1/precedent-favorites/{favorite_id}", paths)
        self.assertIn(
            "/api/v1/precedent-favorites/{favorite_id}/similarity-explanation",
            paths,
        )
        self.assertIn("/api/v1/precedents/{precedent_id}/full-text", paths)
        self.assertIn("PrecedentSearchRequest", schema["components"]["schemas"])
        self.assertIn("ApiErrorResponse", schema["components"]["schemas"])
        search_responses = paths["/api/v1/precedent-searches"]["post"]["responses"]
        self.assertTrue(
            search_responses["503"]["content"]["application/json"]["schema"][
                "$ref"
            ].endswith("/ApiErrorResponse")
        )


if __name__ == "__main__":
    unittest.main()
