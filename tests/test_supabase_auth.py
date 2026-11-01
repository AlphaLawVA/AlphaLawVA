"""Supabase 사용자 토큰 검증과 안전한 오류 변환을 검증한다."""

from __future__ import annotations

import io
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch
from uuid import UUID

from api.services.supabase_auth import (
    AuthenticationRequiredError,
    AuthenticationServiceUnavailableError,
    SupabaseAuthSettings,
    SupabaseAuthVerifier,
)


class SupabaseAuthVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.verifier = SupabaseAuthVerifier(
            SupabaseAuthSettings(
                url="https://example.supabase.co",
                publishable_key="public-test-key",
            )
        )

    @patch("api.services.supabase_auth.urlopen")
    def test_returns_verified_user_identity(self, urlopen_mock):
        urlopen_mock.return_value = io.BytesIO(
            b'{"id":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa","email":"user@example.com"}'
        )

        user = self.verifier.verify("valid-access-token")

        self.assertEqual(
            user.user_id,
            UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        )
        self.assertEqual(user.email, "user@example.com")
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(
            request.get_header("Authorization"), "Bearer valid-access-token"
        )
        self.assertEqual(request.get_header("Apikey"), "public-test-key")

    @patch("api.services.supabase_auth.urlopen")
    def test_rejects_expired_or_invalid_token(self, urlopen_mock):
        urlopen_mock.side_effect = HTTPError(
            "https://example.supabase.co/auth/v1/user",
            401,
            "Unauthorized",
            hdrs=None,
            fp=None,
        )

        with self.assertRaises(AuthenticationRequiredError):
            self.verifier.verify("expired-access-token")

    @patch("api.services.supabase_auth.urlopen")
    def test_reports_auth_service_network_failure(self, urlopen_mock):
        urlopen_mock.side_effect = URLError("offline")

        with self.assertRaises(AuthenticationServiceUnavailableError):
            self.verifier.verify("access-token")


if __name__ == "__main__":
    unittest.main()
