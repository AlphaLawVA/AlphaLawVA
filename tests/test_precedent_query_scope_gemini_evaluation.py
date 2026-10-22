# test_precedent_query_scope_gemini_evaluation.py
"""
Description: Gemini 판례 질문 범위 평가기의 구조화 요청, 토큰 비용 계산과
fail-open 처리를 실제 유료 호출 없이 확인한다.
Author: choeminju
Date: 2026-10-16
Before:
    - Gemini Flash-Lite 범위 분류 평가 어댑터가 정의된 상태.
After:
    - API 키 비노출, JSON Schema 옵션, 사용량과 오류 처리가 자동 검증됨.
"""

import json
import unittest
from unittest.mock import patch

from ml.evaluation.precedents.evaluate_query_scope_gemini import (
    GeminiQueryScopeClassifier,
)
from ml.evaluation.precedents.evaluate_query_scope_qwen import (
    DECISION_OUTPUT_SCHEMA,
    OUTPUT_SCHEMA,
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


def make_response(content: str) -> dict:
    return {
        "candidates": [
            {"content": {"parts": [{"text": content}]}}
        ],
        "usageMetadata": {
            "promptTokenCount": 500,
            "candidatesTokenCount": 5,
            "thoughtsTokenCount": 20,
            "totalTokenCount": 525,
        },
    }


class GeminiQueryScopeEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.classifier = GeminiQueryScopeClassifier(
            api_key="secret-test-key",
            model="gemini-3.5-flash-lite",
        )

    @patch(
        "ml.evaluation.precedents.evaluate_query_scope_gemini.urlopen"
    )
    def test_uses_structured_output_and_records_billable_thinking(self, mock_urlopen):
        mock_urlopen.return_value = FakeHttpResponse(
            make_response('{"in_scope":true}')
        )

        attempt = self.classifier.classify("보증금을 돌려받고 싶어요.")

        self.assertTrue(attempt.prediction)
        self.assertTrue(attempt.format_compliant)
        self.assertEqual(attempt.input_tokens, 500)
        self.assertEqual(attempt.output_tokens, 5)
        self.assertEqual(attempt.thinking_tokens, 20)
        self.assertEqual(attempt.estimated_cost_usd, 0.0002125)

        request = mock_urlopen.call_args.args[0]
        self.assertNotIn("secret-test-key", request.full_url)
        self.assertEqual(request.headers["X-goog-api-key"], "secret-test-key")
        payload = json.loads(request.data.decode("utf-8"))
        config = payload["generationConfig"]
        self.assertEqual(config["temperature"], 0)
        self.assertEqual(config["thinkingConfig"], {"thinkingLevel": "minimal"})
        self.assertEqual(config["responseJsonSchema"], OUTPUT_SCHEMA)
        self.assertEqual(config["responseMimeType"], "application/json")

    @patch(
        "ml.evaluation.precedents.evaluate_query_scope_gemini.urlopen"
    )
    def test_malformed_content_fails_open_without_losing_usage(self, mock_urlopen):
        mock_urlopen.return_value = FakeHttpResponse(make_response("not-json"))

        attempt = self.classifier.classify("파이썬 정렬법을 알려줘.")

        self.assertIsNone(attempt.prediction)
        self.assertTrue(attempt.effective_prediction)
        self.assertFalse(attempt.format_compliant)
        self.assertEqual(attempt.input_tokens, 500)
        self.assertIn("JSONDecodeError", attempt.error)

    @patch(
        "ml.evaluation.precedents.evaluate_query_scope_gemini.urlopen"
    )
    def test_v3_uses_decision_schema_and_parses_enum(self, mock_urlopen):
        classifier = GeminiQueryScopeClassifier(
            api_key="secret-test-key",
            model="gemini-3.5-flash-lite",
            prompt_version="v3",
        )
        mock_urlopen.return_value = FakeHttpResponse(
            make_response('{"decision":"NEEDS_CONTEXT"}')
        )

        attempt = classifier.classify("전세 판례 알려줘.")

        self.assertEqual(attempt.prediction, "NEEDS_CONTEXT")
        self.assertFalse(attempt.effective_prediction)
        request = mock_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(
            payload["generationConfig"]["responseJsonSchema"],
            DECISION_OUTPUT_SCHEMA,
        )

    def test_rejects_invalid_configuration(self):
        with self.assertRaises(ValueError):
            GeminiQueryScopeClassifier(api_key="")
        with self.assertRaises(ValueError):
            GeminiQueryScopeClassifier(
                api_key="test",
                base_url="http://example.com",
            )
        with self.assertRaisesRegex(ValueError, "프롬프트 버전"):
            GeminiQueryScopeClassifier(
                api_key="test",
                prompt_version="unknown",
            )


if __name__ == "__main__":
    unittest.main()
