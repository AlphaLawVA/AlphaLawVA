"""
Description: 로그인 사용자의 특약 생성·계약서 검토 결과 저장·목록·상세 API를 검증한다.
생성 직후 결과를 저장 요청 시 다시 쓸 수 있는지, 소유자 한정 조회와 오류 코드를 확인한다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 계약서 검토·특약 생성 결과를 사용자가 저장하는 API가 없는 상태.
After:
    - 저장은 로그인 필요, 같은 결과는 한 건, 다른 사용자는 조회할 수 없음.
"""

from __future__ import annotations

import unittest
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from api.main import create_app
from api.services.generation_results import InMemoryGenerationResultStore
from api.services.special_clause_service import SpecialClauseService
from api.services.supabase_auth import AuthenticatedUser, AuthenticationRequiredError
from ml.agent.contract_review_graph import ContractReviewEvidence
from ml.agent.special_clause_graph import SpecialClauseGeneration

USER_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
AUTH_A = {"Authorization": "Bearer token-a"}
AUTH_B = {"Authorization": "Bearer token-b"}


class FakeAuthVerifier:
    def verify(self, access_token: str) -> AuthenticatedUser:
        users = {
            "token-a": AuthenticatedUser(USER_A, "a@example.com"),
            "token-b": AuthenticatedUser(USER_B, "b@example.com"),
        }
        try:
            return users[access_token]
        except KeyError as error:
            raise AuthenticationRequiredError from error


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


class GenerationResultApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app = create_app(
            precedent_search_service=object(),
            certified_letter_service=object(),
            special_clause_service=SpecialClauseService(
                evidence_retriever=FakeRetriever(), generator=FakeGenerator()
            ),
            generation_result_store=InMemoryGenerationResultStore(),
            auth_verifier=FakeAuthVerifier(),
        )
        self.client = TestClient(app)

    def _draft(self, requests=("권리 변동을 막는 특약",)):
        response = self.client.post(
            "/api/v1/special-clause-drafts", json={"requests": list(requests)}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_requires_login(self) -> None:
        response = self.client.get("/api/v1/generation-results")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "authentication_required")

    def test_save_list_and_detail_are_user_scoped(self) -> None:
        draft = self._draft()
        body = {"kind": "special_clause", "source_id": draft["draft_id"]}

        saved = self.client.post("/api/v1/generation-results", headers=AUTH_A, json=body)
        self.assertEqual(saved.status_code, 201, saved.text)
        again = self.client.post("/api/v1/generation-results", headers=AUTH_A, json=body)
        self.assertEqual(again.json()["result_id"], saved.json()["result_id"])

        listed = self.client.get("/api/v1/generation-results", headers=AUTH_A).json()
        self.assertEqual(len(listed["items"]), 1)
        self.assertTrue(listed["items"][0]["title"].startswith("특약 초안"))

        detail = self.client.get(
            f"/api/v1/generation-results/{saved.json()['result_id']}", headers=AUTH_A
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["payload"]["draft_id"], draft["draft_id"])

        other_list = self.client.get("/api/v1/generation-results", headers=AUTH_B).json()
        self.assertEqual(other_list["items"], [])
        other_detail = self.client.get(
            f"/api/v1/generation-results/{saved.json()['result_id']}", headers=AUTH_B
        )
        self.assertEqual(other_detail.status_code, 404)
        self.assertEqual(
            other_detail.json()["error"]["code"], "generation_result_not_found"
        )

    def test_unknown_source_is_404(self) -> None:
        response = self.client.post(
            "/api/v1/generation-results",
            headers=AUTH_A,
            json={"kind": "special_clause", "source_id": str(uuid4())},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["error"]["code"], "generation_result_source_not_found"
        )

    def test_out_of_scope_only_result_is_not_savable(self) -> None:
        draft = self._draft(requests=("치킨 레시피 알려줘",))

        response = self.client.post(
            "/api/v1/generation-results",
            headers=AUTH_A,
            json={"kind": "special_clause", "source_id": draft["draft_id"]},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["error"]["code"], "generation_result_not_ready"
        )


if __name__ == "__main__":
    unittest.main()
