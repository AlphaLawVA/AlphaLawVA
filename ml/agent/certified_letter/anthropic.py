# anthropic.py
"""
Description: Anthropic Messages API의 JSON 출력을 내용증명 생성 포트에 연결한다.
계약·분쟁 원문은 저장하지 않고 호출 메타데이터와 토큰 사용량만 기록한다.
Author: AlphaLawVA Team
Date: 2026-11-16
Before:
    - 내용증명 최종 생성 모델이 OpenAI Responses API에만 연결된 상태.
After:
    - Claude Sonnet을 동일한 구조화 초안 스키마로 선택할 수 있음.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from ml.agent.certified_letter.gemini import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_TIMEOUT_SECONDS,
    GeminiStructuredLetterModel,
    _USAGE_LOCK,
)
from ml.agent.certified_letter.prompts import PromptBundle


DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
MODEL_PRICING_USD_PER_MILLION = {
    "claude-sonnet-5-5": (2.00, 10.00),
}


class AnthropicStructuredLetterModel(GeminiStructuredLetterModel):
    """Gemini와 같은 초안 검증을 재사용하고 전송만 Anthropic으로 바꾼다."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        base_url: str = DEFAULT_ANTHROPIC_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        usage_manifest_path: Path | None = None,
        failed_response_dir: Path | None = None,
        max_session_cost_usd: float | None = None,
    ) -> None:
        normalized_model = model.strip()
        normalized_base_url = base_url.strip().rstrip("/")
        if not normalized_model:
            raise ValueError("Claude 모델명은 비어 있을 수 없습니다.")
        if not normalized_base_url.startswith("https://"):
            raise ValueError("Anthropic base URL은 HTTPS 주소여야 합니다.")
        if max_session_cost_usd is not None and max_session_cost_usd <= 0:
            raise ValueError("Claude 세션 비용 상한은 0보다 커야 합니다.")
        if (
            max_session_cost_usd is not None
            and normalized_model not in MODEL_PRICING_USD_PER_MILLION
        ):
            raise ValueError("비용 정보가 없는 Claude 모델에는 상한을 적용할 수 없습니다.")

        super().__init__(
            api_key=api_key,
            model=DEFAULT_GEMINI_MODEL,
            base_url=normalized_base_url,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
            usage_manifest_path=usage_manifest_path,
            max_session_cost_usd=None,
        )
        self.model_name = normalized_model
        self.base_url = normalized_base_url
        self.max_session_cost_usd = max_session_cost_usd
        self.failed_response_dir = failed_response_dir

    def invoke(self, prompt: PromptBundle):
        """잘린 JSON 응답은 원문을 격리 보관하고 한 번만 재시도한다."""
        retry_count = 0
        while True:
            usage: dict[str, Any] = self._empty_usage()
            response_payload: dict[str, Any] | None = None
            try:
                self._ensure_session_budget(
                    prompt,
                    max_output_tokens=self.max_output_tokens,
                )
                response_payload = self._post_json(self._request_payload(prompt))
                usage = self._extract_usage(response_payload)
                draft = self._parse_draft(
                    json.loads(self._extract_text(response_payload))
                )
            except json.JSONDecodeError as error:
                self._save_failed_response(response_payload)
                self._record_usage(
                    prompt,
                    usage,
                    request_kind="draft",
                    success=False,
                    error_type="JSONDecodeError",
                    retry_count=retry_count,
                    failed_response_saved=response_payload is not None,
                )
                if retry_count == 0:
                    retry_count = 1
                    continue
                raise RuntimeError("Claude 구조화 응답을 해석할 수 없습니다.") from error
            except Exception as error:
                self._record_usage(
                    prompt,
                    usage,
                    request_kind="draft",
                    success=False,
                    error_type=type(error).__name__,
                    retry_count=retry_count,
                )
                raise

            self._record_usage(
                prompt,
                usage,
                request_kind="draft",
                success=True,
                retry_count=retry_count,
            )
            return draft

    def _request_payload(self, prompt: PromptBundle) -> dict[str, Any]:
        from ml.agent.certified_letter.gemini import LETTER_DRAFT_SCHEMA

        return {
            "systemInstruction": {"parts": [{"text": prompt.system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt.user}]}],
            "generationConfig": {
                "maxOutputTokens": self.max_output_tokens,
                "responseJsonSchema": LETTER_DRAFT_SCHEMA,
            },
        }

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        generation_config = payload.get("generationConfig") or {}
        system = str(payload["systemInstruction"]["parts"][0]["text"])
        schema = generation_config.get("responseJsonSchema")
        request_payload = {
            "model": self.model_name,
            "max_tokens": self.max_output_tokens,
            "system": system,
            "messages": [
                {
                    "role": "user",
                    "content": payload["contents"][0]["parts"][0]["text"],
                }
            ],
        }
        if schema is not None:
            request_payload["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": _anthropic_compatible_schema(schema),
                }
            }
        request = Request(
            f"{self.base_url}/messages",
            data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise RuntimeError(
                f"Claude 내용증명 모델 호출 실패: HTTP {error.code}"
            ) from error
        except (URLError, TimeoutError) as error:
            raise RuntimeError("Claude 내용증명 모델을 호출할 수 없습니다.") from error

        texts = [
            str(block["text"])
            for block in response_payload.get("content") or []
            if block.get("type") == "text" and block.get("text")
        ]
        if not texts:
            raise RuntimeError("Claude 응답에 text가 없습니다.")
        text = "\n".join(texts).strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else ""
            text = text.rsplit("```", 1)[0].strip()
        usage = response_payload.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        response_metadata = {
            "finish_reason": response_payload.get("stop_reason"),
            "reasoning_tokens": (response_payload.get("usage") or {})
            .get("output_tokens_details", {})
            .get("thinking_tokens"),
            "raw_text": text,
        }
        return {
            "candidates": [{"content": {"parts": [{"text": text}]}}],
            "usageMetadata": {
                "promptTokenCount": input_tokens,
                "candidatesTokenCount": output_tokens,
                "totalTokenCount": input_tokens + output_tokens,
            },
            "_anthropic_metadata": response_metadata,
        }

    @classmethod
    def _extract_usage(cls, response_payload: dict[str, Any]) -> dict[str, Any]:
        usage = super()._extract_usage(response_payload)
        metadata = response_payload.get("_anthropic_metadata") or {}
        usage["finish_reason"] = metadata.get("finish_reason")
        usage["reasoning_tokens"] = metadata.get("reasoning_tokens")
        return usage

    def _record_usage(
        self,
        prompt: PromptBundle,
        usage: dict[str, Any],
        *,
        request_kind: str,
        success: bool,
        error_type: str | None = None,
        retry_count: int = 0,
        failed_response_saved: bool = False,
    ) -> None:
        estimated_cost = self._estimated_cost(usage)
        self._session_estimated_cost_usd += estimated_cost
        if self.usage_manifest_path is None:
            return
        record: dict[str, str | int | float | bool | None] = {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider": "anthropic",
            "model": self.model_name,
            "prompt_version": prompt.version,
            "request_kind": request_kind,
            "success": success,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "total_tokens": usage["total_tokens"],
            "estimated_cost_usd": round(estimated_cost, 8),
            "retry_count": retry_count,
            "finish_reason": usage.get("finish_reason"),
            "reasoning_tokens": usage.get("reasoning_tokens"),
            "failed_response_saved": failed_response_saved,
        }
        if error_type:
            record["error_type"] = error_type
        with _USAGE_LOCK:
            self.usage_manifest_path.parent.mkdir(parents=True, exist_ok=True)
            with self.usage_manifest_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _ensure_session_budget(
        self,
        prompt: PromptBundle,
        *,
        max_output_tokens: int,
    ) -> None:
        if self.max_session_cost_usd is None:
            return
        input_price, output_price = MODEL_PRICING_USD_PER_MILLION[self.model_name]
        conservative_input_tokens = len(f"{prompt.system}\n{prompt.user}".encode("utf-8")) + 1024
        conservative_next_cost = (
            conservative_input_tokens * input_price
            + max_output_tokens * output_price
        ) / 1_000_000
        if self._session_estimated_cost_usd + conservative_next_cost > self.max_session_cost_usd:
            raise RuntimeError("Claude 내용증명 세션 비용 상한을 초과할 수 있습니다.")

    def _estimated_cost(self, usage: dict[str, int]) -> float:
        input_price, output_price = MODEL_PRICING_USD_PER_MILLION.get(
            self.model_name,
            (0.0, 0.0),
        )
        return (
            usage["input_tokens"] * input_price
            + usage["output_tokens"] * output_price
        ) / 1_000_000

    def _save_failed_response(self, response_payload: dict[str, Any] | None) -> None:
        if self.failed_response_dir is None or response_payload is None:
            return
        metadata = response_payload.get("_anthropic_metadata") or {}
        raw_text = metadata.get("raw_text")
        if not isinstance(raw_text, str) or not raw_text:
            return
        self.failed_response_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.failed_response_dir, 0o700)
        except OSError:
            pass
        filename = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + f"-{uuid4().hex}.json.txt"
        )
        target = self.failed_response_dir / filename
        target.write_text(raw_text, encoding="utf-8")
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass


def _anthropic_compatible_schema(value: Any) -> Any:
    """Claude가 지원하지 않는 배열 길이 제약만 제거한 스키마를 만든다."""
    if isinstance(value, list):
        return [_anthropic_compatible_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == "maxItems":
            continue
        if key == "minItems" and item not in {0, 1}:
            continue
        result[key] = _anthropic_compatible_schema(item)
    return result
