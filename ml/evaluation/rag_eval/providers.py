# providers.py
"""
Description: Gemini 요청 형식으로 만든 분석·생성·판정 요청을 OpenAI Chat Completions로 보내고
응답을 Gemini 응답 형식으로 되돌려, 서비스 분석기 코드를 바꾸지 않고 다른 계열 모델을 평가한다.
Author: ooheunsu
Date: 2026-11-06
Before:
    - 분석기·생성기·판정기가 Gemini 요청/응답 형식으로만 호출되는 상태.
After:
    - 모델명으로 제공자를 고르고, OpenAI 모델도 같은 평가 경로로 호출·사용량 기록이 가능함.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from api.services import gemini_contract_review_analyzer as review_module

OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 120.0
RETRYABLE_STATUS = {401, 429, 500, 502, 503}  # 401은 이 키에서 간헐적으로 관찰됨(실패 호출은 과금되지 않음)
MAX_ATTEMPTS = 4
MIN_COMPLETION_TOKENS = 4000  # 추론 토큰이 출력 한도를 먼저 쓰지 않게 하는 하한


def provider_for(model: str) -> str:
    name = model.removeprefix("models/")
    if name.startswith("gemini-"):
        return "gemini"
    if name.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    raise ValueError(f"제공자를 알 수 없는 모델입니다: {model}")


def to_openai_request(model: str, request: Mapping[str, Any]) -> dict[str, Any]:
    """Gemini 형식 요청을 Chat Completions 요청으로 바꾼다."""

    generation = request.get("generationConfig", {})
    system = request["systemInstruction"]["parts"][0]["text"]
    user = request["contents"][0]["parts"][0]["text"]
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_completion_tokens": max(
            int(generation.get("maxOutputTokens", 0)), MIN_COMPLETION_TOKENS
        ),
    }
    schema = generation.get("responseJsonSchema")
    if schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "result", "schema": schema, "strict": False},
        }
    return body


def to_gemini_response(response: Mapping[str, Any]) -> dict[str, Any]:
    """Chat Completions 응답을 Gemini 응답 형식(텍스트·사용량)으로 바꾼다."""

    choices = response.get("choices") or []
    message = (choices[0].get("message") or {}) if choices else {}
    text = message.get("content") or ""
    if not text:
        refusal = message.get("refusal")
        raise RuntimeError(f"OpenAI 응답에 텍스트가 없습니다{': ' + refusal if refusal else ''}.")
    usage = response.get("usage") or {}
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {
            "promptTokenCount": int(usage.get("prompt_tokens") or 0),
            "candidatesTokenCount": int(usage.get("completion_tokens") or 0),
            "totalTokenCount": int(usage.get("total_tokens") or 0),
        },
    }


class _RetryableHttpError(RuntimeError):
    """재시도할 수 있는 HTTP 오류다."""


class OpenAIChatTransport:
    """분석기 인스턴스처럼 _post_json 등을 제공하는 OpenAI 전송 객체다."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = OPENAI_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        opener: Callable[..., Any] = urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OpenAI API 키는 비어 있을 수 없습니다.")
        if not base_url.startswith("https://"):
            raise ValueError("OpenAI base URL은 HTTPS 주소여야 합니다.")
        self._api_key = api_key.strip()
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._opener = opener
        self._sleep = sleep

    def _post_json(self, request: Mapping[str, Any]) -> dict[str, Any]:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return self._post_once(request)
            except _RetryableHttpError as error:
                if attempt == MAX_ATTEMPTS:
                    raise RuntimeError(str(error)) from error
                self._sleep(1.5 * attempt)
        raise RuntimeError("OpenAI 호출 재시도 한도를 넘었습니다.")  # pragma: no cover

    def _post_once(self, request: Mapping[str, Any]) -> dict[str, Any]:
        body = to_openai_request(self._model, request)
        http_request = Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        try:
            with self._opener(http_request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = ""
            try:  # 인증 정보 없이 오류 설명만 남긴다.
                detail = json.loads(error.read().decode("utf-8")).get("error", {}).get("message", "")
            except Exception:  # noqa: BLE001 - 설명을 못 읽어도 상태 코드는 알린다.
                pass
            message = f"OpenAI 호출 실패: HTTP {error.code} {detail[:200]}"
            if error.code in RETRYABLE_STATUS:
                raise _RetryableHttpError(message) from error
            raise RuntimeError(message) from error
        except (URLError, TimeoutError) as error:
            raise RuntimeError("OpenAI 모델을 호출할 수 없습니다.") from error
        return to_gemini_response(payload)

    def _thinking_config(self) -> dict[str, Any]:
        return {}

    _extract_text = staticmethod(review_module.GeminiContractReviewAnalyzer._extract_text)
    _extract_usage = staticmethod(review_module.GeminiContractReviewAnalyzer._extract_usage)
