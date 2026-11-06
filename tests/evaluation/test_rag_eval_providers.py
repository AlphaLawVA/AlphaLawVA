# test_rag_eval_providers.py
"""
Description: OpenAI 어댑터의 요청·응답 변환과 제공자 선택을 가짜 전송으로 검증한다. 네트워크를 쓰지 않는다.
Author: ooheunsu
Date: 2026-11-06
Before:
    - ml.evaluation.rag_eval.providers가 존재.
After:
    - 요청 변환, 응답 변환, 오류 처리, 제공자 판별이 오프라인에서 검증됨.
"""

import io
import json
import unittest
from urllib.error import HTTPError

from ml.evaluation.rag_eval import providers
from ml.evaluation.rag_eval.generation_eval import GenerationEvalError, _require_keys, make_transport

REQUEST = {
    "systemInstruction": {"parts": [{"text": "시스템"}]},
    "contents": [{"role": "user", "parts": [{"text": "{\"items\": []}"}]}],
    "generationConfig": {"maxOutputTokens": 800, "responseJsonSchema": {"type": "object"}},
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ProviderTest(unittest.TestCase):
    def test_provider_for(self):
        self.assertEqual(providers.provider_for("gemini-3.8-flash"), "gemini")
        self.assertEqual(providers.provider_for("gpt-6.1-terra"), "openai")
        self.assertEqual(providers.provider_for("claude-sonnet-5-5"), "anthropic")
        with self.assertRaises(ValueError):
            providers.provider_for("llama-3")

    def test_request_conversion(self):
        body = providers.to_openai_request("gpt-6.1-terra", REQUEST)
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertGreaterEqual(body["max_completion_tokens"], providers.MIN_COMPLETION_TOKENS)
        self.assertEqual(body["response_format"]["json_schema"]["schema"], {"type": "object"})
        self.assertNotIn("temperature", body)

    def test_response_conversion(self):
        converted = providers.to_gemini_response(
            {"choices": [{"message": {"content": "{\"items\": []}"}}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        )
        self.assertEqual(converted["usageMetadata"]["promptTokenCount"], 10)
        self.assertEqual(converted["candidates"][0]["content"]["parts"][0]["text"], "{\"items\": []}")
        with self.assertRaises(RuntimeError):
            providers.to_gemini_response({"choices": [{"message": {"content": "", "refusal": "거절"}}]})

    def test_transport_round_trip_and_error(self):
        seen = {}

        def opener(request, timeout):
            seen["auth"] = request.headers.get("Authorization")
            return FakeResponse(json.dumps({"choices": [{"message": {"content": "{}"}}], "usage": {}}).encode())

        transport = providers.OpenAIChatTransport(api_key="k", model="gpt-6.1-terra", opener=opener)
        response = transport._post_json(REQUEST)
        self.assertEqual(transport._extract_text(response), "{}")
        self.assertEqual(seen["auth"], "Bearer k")

        def failing(request, timeout):
            raise HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"error": {"message": "unsupported"}}'))

        bad = providers.OpenAIChatTransport(api_key="k", model="m", opener=failing)
        with self.assertRaises(RuntimeError) as ctx:
            bad._post_json(REQUEST)
        self.assertIn("unsupported", str(ctx.exception))
        self.assertNotIn("Bearer", str(ctx.exception))

    def test_retries_transient_401_then_succeeds(self):
        calls = {"n": 0}

        def flaky(request, timeout):
            calls["n"] += 1
            if calls["n"] < 3:
                raise HTTPError("u", 401, "denied", {}, io.BytesIO(b'{"error": {"message": "scope"}}'))
            return FakeResponse(json.dumps({"choices": [{"message": {"content": "{}"}}], "usage": {}}).encode())

        transport = providers.OpenAIChatTransport(
            api_key="k", model="m", opener=flaky, sleep=lambda seconds: None
        )
        self.assertEqual(transport._extract_text(transport._post_json(REQUEST)), "{}")
        self.assertEqual(calls["n"], 3)

    def test_gives_up_after_max_attempts(self):
        calls = {"n": 0}

        def always(request, timeout):
            calls["n"] += 1
            raise HTTPError("u", 401, "denied", {}, io.BytesIO(b"{}"))

        transport = providers.OpenAIChatTransport(
            api_key="k", model="m", opener=always, sleep=lambda seconds: None
        )
        with self.assertRaises(RuntimeError):
            transport._post_json(REQUEST)
        self.assertEqual(calls["n"], providers.MAX_ATTEMPTS)

    def test_non_retryable_error_is_immediate(self):
        calls = {"n": 0}

        def bad(request, timeout):
            calls["n"] += 1
            raise HTTPError("u", 400, "bad", {}, io.BytesIO(b"{}"))

        transport = providers.OpenAIChatTransport(
            api_key="k", model="m", opener=bad, sleep=lambda seconds: None
        )
        with self.assertRaises(RuntimeError):
            transport._post_json(REQUEST)
        self.assertEqual(calls["n"], 1)

    def test_anthropic_request_and_response(self):
        body = providers.to_anthropic_request("claude-sonnet-5-5", REQUEST)
        self.assertIn("JSON 스키마", body["system"])
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertNotIn("temperature", body)
        converted = providers.anthropic_to_gemini_response(
            {"content": [{"type": "text", "text": "```json\n{\"items\": []}\n```"}],
             "usage": {"input_tokens": 7, "output_tokens": 3}}
        )
        self.assertEqual(converted["candidates"][0]["content"]["parts"][0]["text"], "{\"items\": []}")
        self.assertEqual(converted["usageMetadata"]["totalTokenCount"], 10)
        with self.assertRaises(RuntimeError):
            providers.anthropic_to_gemini_response({"content": []})

    def test_anthropic_transport_headers_and_errors(self):
        seen = {}

        def opener(request, timeout):
            seen["key"] = request.headers.get("X-api-key")
            seen["version"] = request.headers.get("Anthropic-version")
            return FakeResponse(json.dumps({"content": [{"type": "text", "text": "{}"}], "usage": {}}).encode())

        transport = providers.AnthropicMessagesTransport(api_key="k", model="claude-sonnet-5-5", opener=opener)
        self.assertEqual(transport._extract_text(transport._post_json(REQUEST)), "{}")
        self.assertEqual(seen, {"key": "k", "version": providers.ANTHROPIC_VERSION})

        def credit(request, timeout):
            raise HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"error": {"message": "credit balance too low"}}'))

        with self.assertRaises(RuntimeError) as ctx:
            providers.AnthropicMessagesTransport(api_key="k", model="m", opener=credit)._post_json(REQUEST)
        self.assertIn("credit balance", str(ctx.exception))
        self.assertNotIn("k", str(ctx.exception).replace("credit", ""))

    def test_key_requirements(self):
        with self.assertRaises(GenerationEvalError):
            _require_keys(["gpt-6.1-terra"], "gemini-key", None)
        with self.assertRaises(GenerationEvalError):
            _require_keys(["claude-sonnet-5-5"], "g", "o", None)
        _require_keys(["claude-sonnet-5-5"], "g", "o", "a")
        _require_keys(["gpt-6.1-terra", "gemini-3.8-flash"], "g", "o")
        self.assertIsInstance(make_transport("gpt-6-luna", None, "o"), providers.OpenAIChatTransport)


if __name__ == "__main__":
    unittest.main()
