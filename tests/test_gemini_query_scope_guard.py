# test_gemini_query_scope_guard.py
"""
Description: Gemini 질문 분류기의 구조화 요청, 판정 매핑과 fail-open을 검증한다.
가짜 HTTP 응답을 사용하여 실제 유료 API 호출 없이 동작을 검사한다.
Author: choeminju
Date: 2026-10-27
Before:
    - 평가용 질문 분류만 있고 실제 검색용 범위 분류 서비스가 없음.
After:
    - 네 가지 판정과 오류 시 검색 허용, 환경 설정, 사용 기록이 자동 확인됨.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from api.services.gemini_query_scope_guard import (
    DECISION_MESSAGES,
    OUTPUT_SCHEMA,
    GeminiQueryScopeGuard,
    create_gemini_query_scope_guard_from_env,
)


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def response_payload(content):
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": json.dumps(content, ensure_ascii=False)}
                    ]
                }
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 700,
            "candidatesTokenCount": 5,
            "totalTokenCount": 705,
        },
    }


class GeminiQueryScopeGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.manifest_path = Path(self.temporary_directory.name) / "usage.jsonl"
        self.guard = GeminiQueryScopeGuard(
            api_key="test-key",
            model="gemini-3.5-flash-lite",
            timeout_seconds=10,
            usage_manifest_path=self.manifest_path,
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    @patch("api.services.gemini_query_scope_guard.urlopen")
    def test_allows_valid_query_with_structured_output(self, mock_urlopen):
        mock_urlopen.return_value = FakeHttpResponse(
            response_payload({"decision": "ALLOW"})
        )

        decision = self.guard.evaluate("집주인이 보증금을 돌려주지 않아요.")

        self.assertTrue(decision.is_in_scope)
        self.assertIsNone(decision.message)
        request = mock_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(
            payload["generationConfig"]["responseJsonSchema"],
            OUTPUT_SCHEMA,
        )
        self.assertEqual(payload["generationConfig"]["temperature"], 0)
        self.assertEqual(
            payload["generationConfig"]["thinkingConfig"],
            {"thinkingLevel": "minimal"},
        )
        user_data = json.loads(payload["contents"][0]["parts"][0]["text"])
        self.assertEqual(
            user_data,
            {"user_query": "집주인이 보증금을 돌려주지 않아요."},
        )
        usage = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(usage["decision"], "ALLOW")
        self.assertFalse(usage["fail_open"])
        self.assertNotIn("user_query", usage)

    @patch("api.services.gemini_query_scope_guard.urlopen")
    def test_maps_rejection_decisions_to_user_messages(self, mock_urlopen):
        for label in ("NEEDS_CONTEXT", "MIXED_INTENT", "NOT_REAL_ESTATE"):
            with self.subTest(label=label):
                mock_urlopen.return_value = FakeHttpResponse(
                    response_payload({"decision": label})
                )

                decision = self.guard.evaluate("분류할 질문")

                self.assertFalse(decision.is_in_scope)
                self.assertEqual(decision.message, DECISION_MESSAGES[label])

    @patch("api.services.gemini_query_scope_guard.urlopen")
    def test_invalid_output_fails_open(self, mock_urlopen):
        mock_urlopen.return_value = FakeHttpResponse(
            response_payload({"decision": "BLOCK"})
        )

        decision = self.guard.evaluate("보증금을 돌려받지 못했습니다.")

        self.assertTrue(decision.is_in_scope)
        usage = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(usage["fail_open"])
        self.assertEqual(usage["error_type"], "ValueError")

    @patch("api.services.gemini_query_scope_guard.urlopen")
    def test_connection_error_fails_open(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("offline")

        decision = self.guard.evaluate("감자")

        self.assertTrue(decision.is_in_scope)
        usage = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(usage["fail_open"])
        self.assertEqual(usage["error_type"], "RuntimeError")

    def test_reads_environment_and_disable_switch(self):
        with (
            patch("api.services.gemini_query_scope_guard.load_env_file"),
            patch.dict(
                os.environ,
                {
                    "GEMINI_API_KEY": "test-key",
                    "PRECEDENT_QUERY_SCOPE_ENABLED": "true",
                    "PRECEDENT_QUERY_SCOPE_GEMINI_MODEL": "models/gemini-test",
                    "PRECEDENT_QUERY_SCOPE_GEMINI_TIMEOUT_SECONDS": "12",
                    "PRECEDENT_QUERY_SCOPE_GEMINI_MAX_OUTPUT_TOKENS": "48",
                },
                clear=True,
            ),
        ):
            guard = create_gemini_query_scope_guard_from_env()

        self.assertIsInstance(guard, GeminiQueryScopeGuard)
        self.assertEqual(guard.model_name, "gemini-test")
        self.assertEqual(guard.timeout_seconds, 12)
        self.assertEqual(guard.max_output_tokens, 48)

        with (
            patch("api.services.gemini_query_scope_guard.load_env_file"),
            patch.dict(
                os.environ,
                {"PRECEDENT_QUERY_SCOPE_ENABLED": "false"},
                clear=True,
            ),
        ):
            self.assertIsNone(create_gemini_query_scope_guard_from_env())

    def test_missing_api_key_disables_guard(self):
        with (
            patch("api.services.gemini_query_scope_guard.load_env_file"),
            patch.dict(os.environ, {}, clear=True),
        ):
            self.assertIsNone(create_gemini_query_scope_guard_from_env())


if __name__ == "__main__":
    unittest.main()
