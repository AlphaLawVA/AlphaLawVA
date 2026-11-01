"""로그인 사용자의 내용증명 결과 저장·목록·상세 API를 검증한다."""

from __future__ import annotations

import unittest
from uuid import UUID

from fastapi.testclient import TestClient

from api.main import create_app
from api.services.certified_letter_results import InMemoryCertifiedLetterResultStore
from api.services.supabase_auth import AuthenticatedUser, AuthenticationRequiredError
from tests.test_certified_letter_results import DRAFT_ID, FakeDraftService


USER_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


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


class CertifiedLetterResultApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app = create_app(
            precedent_search_service=object(),
            certified_letter_service=FakeDraftService(),
            certified_letter_result_store=InMemoryCertifiedLetterResultStore(),
            auth_verifier=FakeAuthVerifier(),
        )
        self.client = TestClient(app)

    def test_requires_login(self):
        response = self.client.get("/api/v1/certified-letter-results")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "authentication_required")

    def test_save_list_and_detail_are_user_scoped(self):
        saved_response = self.client.post(
            "/api/v1/certified-letter-results",
            headers={"Authorization": "Bearer token-a"},
            json={"draft_id": str(DRAFT_ID)},
        )
        self.assertEqual(saved_response.status_code, 201)
        saved = saved_response.json()

        duplicate_response = self.client.post(
            "/api/v1/certified-letter-results",
            headers={"Authorization": "Bearer token-a"},
            json={"draft_id": str(DRAFT_ID)},
        )
        self.assertEqual(duplicate_response.json()["result_id"], saved["result_id"])

        own_list = self.client.get(
            "/api/v1/certified-letter-results",
            headers={"Authorization": "Bearer token-a"},
        )
        other_list = self.client.get(
            "/api/v1/certified-letter-results",
            headers={"Authorization": "Bearer token-b"},
        )
        self.assertEqual(len(own_list.json()["items"]), 1)
        self.assertEqual(other_list.json()["items"], [])

        hidden_detail = self.client.get(
            f"/api/v1/certified-letter-results/{saved['result_id']}",
            headers={"Authorization": "Bearer token-b"},
        )
        self.assertEqual(hidden_detail.status_code, 404)


if __name__ == "__main__":
    unittest.main()
