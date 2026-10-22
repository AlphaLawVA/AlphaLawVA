# gemini.py
"""
Description: Gemini 구조화 출력을 공급자 중립 내용증명 생성 포트에 연결한다.
응답 스키마와 사용량을 검증하고 프롬프트 원문 없이 호출 메타데이터만 기록한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 내용증명 그래프에 실제 생성 모델 구현이 연결되지 않은 상태.
After:
    - Gemini Flash-Lite로 검증 가능한 내용증명 초안 객체를 생성할 수 있음.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from ml.agent.certified_letter.prompts import PromptBundle
from ml.agent.certified_letter.state import DraftPartyData, LetterDraftData


DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_TIMEOUT_SECONDS = 90.0
DEFAULT_MAX_OUTPUT_TOKENS = 4096
MODEL_PRICING_USD_PER_MILLION = {
    "gemini-3.5-flash-lite": (0.30, 2.50),
}

PARTY_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "계약 입력에 있는 당사자 이름"},
        "address": {"type": "string", "description": "계약 입력에 있는 송달 주소"},
        "detail_address": {
            "type": ["string", "null"],
            "description": "계약 입력에 있는 상세 주소 또는 null",
        },
    },
    "required": ["name", "address", "detail_address"],
    "additionalProperties": False,
}

LETTER_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "내용증명 문서 제목",
        },
        "sender": PARTY_SCHEMA,
        "recipient": PARTY_SCHEMA,
        "body": {
            "type": "string",
            "description": "문단 구분을 보존한 내용증명 본문",
        },
        "strategy_summary": {
            "type": "string",
            "description": "본문과 분리된 작성 전략 요약",
        },
        "follow_up_actions": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 8,
            "description": "발송 전후 확인할 후속 조치",
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 8,
            "description": "초안 성격과 기한 등 주의사항",
        },
    },
    "required": [
        "title",
        "sender",
        "recipient",
        "body",
        "strategy_summary",
        "follow_up_actions",
        "warnings",
    ],
    "additionalProperties": False,
}

_USAGE_LOCK = Lock()


class GeminiStructuredLetterModel:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        base_url: str = DEFAULT_GEMINI_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        usage_manifest_path: Path | None = None,
    ) -> None:
        normalized_key = api_key.strip()
        normalized_model = model.strip().removeprefix("models/")
        normalized_base_url = base_url.strip().rstrip("/")
        if not normalized_key:
            raise ValueError("Gemini API 키는 비어 있을 수 없습니다.")
        if not normalized_model:
            raise ValueError("Gemini 모델명은 비어 있을 수 없습니다.")
        if not normalized_base_url.startswith("https://"):
            raise ValueError("Gemini base URL은 HTTPS 주소여야 합니다.")
        if timeout_seconds <= 0:
            raise ValueError("Gemini timeout은 0보다 커야 합니다.")
        if max_output_tokens < 1:
            raise ValueError("Gemini 출력 토큰 수는 1 이상이어야 합니다.")

        self._api_key = normalized_key
        self.model_name = normalized_model
        self.base_url = normalized_base_url
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.usage_manifest_path = usage_manifest_path

    def invoke(self, prompt: PromptBundle) -> LetterDraftData:
        request_payload = {
            "systemInstruction": {"parts": [{"text": prompt.system}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt.user}],
                }
            ],
            "generationConfig": {
                "maxOutputTokens": self.max_output_tokens,
                "thinkingConfig": {"thinkingLevel": "minimal"},
                "responseMimeType": "application/json",
                "responseJsonSchema": LETTER_DRAFT_SCHEMA,
            },
        }
        usage = self._empty_usage()
        try:
            response_payload = self._post_json(request_payload)
            usage = self._extract_usage(response_payload)
            parsed = json.loads(self._extract_text(response_payload))
            draft = self._parse_draft(parsed)
        except Exception as error:
            self._record_usage(
                prompt,
                usage,
                success=False,
                error_type=type(error).__name__,
            )
            raise

        self._record_usage(prompt, usage, success=True)
        return draft

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint = (
            f"{self.base_url}/models/{quote(self.model_name, safe='-._')}:"
            "generateContent"
        )
        request = Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self._api_key,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except HTTPError as error:
            raise RuntimeError(
                f"Gemini 내용증명 생성 호출 실패: HTTP {error.code}"
            ) from error
        except (URLError, TimeoutError) as error:
            raise RuntimeError("Gemini 내용증명 생성 모델을 호출할 수 없습니다.") from error

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as error:
            raise RuntimeError("Gemini 응답을 해석할 수 없습니다.") from error
        if not isinstance(parsed, dict):
            raise RuntimeError("Gemini 응답 객체 형식이 올바르지 않습니다.")
        return parsed

    @staticmethod
    def _extract_text(response_payload: dict[str, Any]) -> str:
        candidates = response_payload.get("candidates") or []
        if not candidates:
            raise RuntimeError("Gemini 응답에 candidates가 없습니다.")
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        texts = [str(part.get("text")) for part in parts if part.get("text")]
        if not texts:
            raise RuntimeError("Gemini 응답에 text part가 없습니다.")
        return "\n".join(texts)

    @staticmethod
    def _extract_usage(response_payload: dict[str, Any]) -> dict[str, int]:
        usage = response_payload.get("usageMetadata") or {}
        input_tokens = int(usage.get("promptTokenCount") or 0)
        output_tokens = int(usage.get("candidatesTokenCount") or 0)
        total_tokens = int(usage.get("totalTokenCount") or 0)
        if not output_tokens and total_tokens >= input_tokens:
            output_tokens = total_tokens - input_tokens
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }

    @classmethod
    def _parse_draft(cls, value: Any) -> LetterDraftData:
        if not isinstance(value, dict):
            raise ValueError("Gemini 내용증명 응답은 객체여야 합니다.")
        expected_keys = {
            "title",
            "sender",
            "recipient",
            "body",
            "strategy_summary",
            "follow_up_actions",
            "warnings",
        }
        if set(value) != expected_keys:
            raise ValueError("Gemini 내용증명 응답 필드가 스키마와 일치하지 않습니다.")
        return LetterDraftData(
            title=cls._required_text(value["title"], "title", maximum=200),
            sender=cls._parse_party(value["sender"], "sender"),
            recipient=cls._parse_party(value["recipient"], "recipient"),
            body=cls._required_text(value["body"], "body", maximum=12_000),
            strategy_summary=cls._required_text(
                value["strategy_summary"],
                "strategy_summary",
                maximum=2_000,
            ),
            follow_up_actions=cls._string_tuple(
                value["follow_up_actions"],
                "follow_up_actions",
            ),
            warnings=cls._string_tuple(value["warnings"], "warnings"),
        )

    @classmethod
    def _parse_party(cls, value: Any, field: str) -> DraftPartyData:
        if not isinstance(value, dict) or set(value) != {
            "name",
            "address",
            "detail_address",
        }:
            raise ValueError(f"Gemini {field} 필드가 올바르지 않습니다.")
        detail_address = value["detail_address"]
        if detail_address is not None:
            detail_address = cls._required_text(
                detail_address,
                f"{field}.detail_address",
                maximum=300,
            )
        return DraftPartyData(
            name=cls._required_text(value["name"], f"{field}.name", maximum=100),
            address=cls._required_text(
                value["address"],
                f"{field}.address",
                maximum=300,
            ),
            detail_address=detail_address,
        )

    @classmethod
    def _string_tuple(cls, value: Any, field: str) -> tuple[str, ...]:
        if not isinstance(value, list) or len(value) > 8:
            raise ValueError(f"Gemini {field} 필드가 올바르지 않습니다.")
        return tuple(
            cls._required_text(item, field, maximum=500) for item in value
        )

    @staticmethod
    def _required_text(value: Any, field: str, *, maximum: int) -> str:
        if not isinstance(value, str):
            raise ValueError(f"Gemini {field} 필드는 문자열이어야 합니다.")
        normalized = value.strip()
        if not normalized or len(normalized) > maximum:
            raise ValueError(f"Gemini {field} 필드 길이가 올바르지 않습니다.")
        return normalized

    def _record_usage(
        self,
        prompt: PromptBundle,
        usage: dict[str, int],
        *,
        success: bool,
        error_type: str | None = None,
    ) -> None:
        if self.usage_manifest_path is None:
            return
        input_price, output_price = MODEL_PRICING_USD_PER_MILLION.get(
            self.model_name,
            (0.0, 0.0),
        )
        estimated_cost = (
            usage["input_tokens"] * input_price
            + usage["output_tokens"] * output_price
        ) / 1_000_000
        record: dict[str, str | int | float | bool] = {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider": "gemini",
            "model": self.model_name,
            "prompt_version": prompt.version,
            "success": success,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "total_tokens": usage["total_tokens"],
            "estimated_cost_usd": round(estimated_cost, 8),
            "retry_count": 0,
        }
        if error_type:
            record["error_type"] = error_type
        with _USAGE_LOCK:
            self.usage_manifest_path.parent.mkdir(parents=True, exist_ok=True)
            with self.usage_manifest_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _empty_usage() -> dict[str, int]:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
