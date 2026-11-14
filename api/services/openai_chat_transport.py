# openai_chat_transport.py
"""
Description: Gemini 요청 형식으로 만든 생성·분석 요청을 OpenAI Chat Completions로 보내고 응답을
Gemini 응답 형식(텍스트·사용량)으로 되돌린다. 서비스의 생성기·분석기 코드를 바꾸지 않고 OpenAI 모델을 쓰게 한다.
Author: ooheunsu
Date: 2026-11-12
Before:
    - 평가 코드(ml/evaluation/rag_eval/providers.py) 안에만 OpenAI 전송 객체가 있던 상태.
After:
    - 서비스와 평가가 같은 전송 객체를 쓴다. 이 키는 Chat Completions만 허용되고 Responses API 권한이 없다.
"""

from __future__ import annotations

import http.client
import json
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 120.0
RETRYABLE_STATUS = {401, 429, 500, 502, 503}  # 401은 이 키에서 간헐적으로 관찰됨(실패 호출은 과금되지 않음)
MAX_ATTEMPTS = 4
DEFAULT_LLM_MODEL = "gpt-6.1-terra"  # 특약 생성·계약서 검토의 기본 생성 모델(2026-11-12 결정)
MIN_COMPLETION_TOKENS = 4000  # 추론 토큰이 출력 한도를 먼저 쓰지 않게 하는 하한


def is_openai_model(model: str) -> bool:
    """모델 이름으로 OpenAI 모델인지 판단한다."""

    return model.strip().removeprefix("models/").startswith(("gpt-", "o1", "o3", "o4"))


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


def _extract_text(response: dict[str, Any]) -> str:
    candidates = response.get("candidates") or []
    parts = (candidates[0].get("content") or {}).get("parts") if candidates else []
    texts = [str(part.get("text")) for part in parts or [] if part.get("text")]
    if not texts:
        raise RuntimeError("OpenAI 응답에 text part가 없습니다.")
    return "\n".join(texts)


def _extract_usage(response: dict[str, Any]) -> dict[str, int]:
    usage = response.get("usageMetadata") or {}
    input_tokens = int(usage.get("promptTokenCount") or 0)
    output_tokens = int(usage.get("candidatesTokenCount") or 0)
    total_tokens = int(usage.get("totalTokenCount") or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens or max(total_tokens - input_tokens, 0),
        "total_tokens": total_tokens,
    }


class OpenAIChatTransport:
    """생성기·분석기 인스턴스처럼 _post_json 등을 제공하는 OpenAI 전송 객체다."""

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
        except (URLError, TimeoutError, ConnectionError, http.client.HTTPException) as error:
            # 와이파이·핫스팟 전환 등으로 연결이 끊긴 경우: 응답을 받지 못했으므로 재시도한다.
            raise _RetryableHttpError("OpenAI 모델을 호출할 수 없습니다(연결 끊김).") from error
        return to_gemini_response(payload)

    def _thinking_config(self) -> dict[str, Any]:
        return {}

    _extract_text = staticmethod(_extract_text)
    _extract_usage = staticmethod(_extract_usage)
