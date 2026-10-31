# evaluate_query_scope_gemini.py
"""
Description: 판례 검색 질문 범위 분류를 Gemini Flash-Lite 구조화 출력으로
실행하고 정확도, 오차단, 응답시간, 토큰 사용량과 예상 비용을 평가한다.
Author: choeminju
Date: 2026-10-16
Before:
    - 판례 질문 범위 JSONL 평가셋과 Gemini API 키가 준비된 상태.
After:
    - local_data 아래에 Gemini 모델의 범위 분류 평가 보고서가 생성된다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.services.gemini_similarity_explainer import (  # noqa: E402
    DEFAULT_GEMINI_BASE_URL,
    DEFAULT_GEMINI_MODEL,
    MODEL_PRICING_USD_PER_MILLION,
)
from ml.evaluation.precedents.evaluate_query_scope_qwen import (  # noqa: E402
    DECISION_DATASET_PATH,
    DEFAULT_DATASET_PATH,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PROMPT_VERSION,
    OUTPUT_SCHEMAS,
    SYSTEM_PROMPTS,
    ClassificationAttempt,
    EvaluationCase,
    elapsed_ms,
    evaluate_model,
    format_rate,
    load_evaluation_cases,
    parse_structured_output,
    write_outputs,
)
from precedents.precedent_config import load_env_file  # noqa: E402


DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_OUTPUT_TOKENS = 64


class GeminiQueryScopeClassifier:
    """Gemini JSON Schema 출력을 사용하는 평가 전용 범위 분류기다."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        base_url: str = DEFAULT_GEMINI_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        prompt_version: str = DEFAULT_PROMPT_VERSION,
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
        if timeout_seconds <= 0 or max_output_tokens < 1:
            raise ValueError("Gemini 실행 제한값은 0보다 커야 합니다.")
        if prompt_version not in SYSTEM_PROMPTS:
            raise ValueError(f"지원하지 않는 프롬프트 버전입니다: {prompt_version}")

        self._api_key = normalized_key
        self.model = normalized_model
        self.base_url = normalized_base_url
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.prompt_version = prompt_version
        self.system_prompt = SYSTEM_PROMPTS[prompt_version]
        self.output_schema = OUTPUT_SCHEMAS[prompt_version]

    def build_request_payload(self, query: str) -> dict[str, Any]:
        """Gemini의 최소 thinking과 JSON Schema 출력 옵션을 고정한다."""
        return {
            "systemInstruction": {"parts": [{"text": self.system_prompt}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": json.dumps(
                                {"user_query": query},
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": self.max_output_tokens,
                "thinkingConfig": {"thinkingLevel": "minimal"},
                "responseMimeType": "application/json",
                "responseJsonSchema": self.output_schema,
            },
        }

    def classify(self, query: str) -> ClassificationAttempt:
        """질문 한 건을 호출하고 오류 시 검색을 허용하는 결과를 반환한다."""
        started_at = time.perf_counter()
        raw_content: str | None = None
        usage = self._empty_usage()
        try:
            response_payload = self._post_json(self.build_request_payload(query))
            usage = self._extract_usage(response_payload)
            raw_content = self._extract_text(response_payload)
            prediction = parse_structured_output(raw_content, self.prompt_version)
        except (RuntimeError, ValueError, json.JSONDecodeError) as error:
            return ClassificationAttempt(
                prediction=None,
                format_compliant=False,
                latency_ms=elapsed_ms(started_at),
                error=f"{type(error).__name__}: {error}",
                raw_content=raw_content,
                **usage,
            )

        return ClassificationAttempt(
            prediction=prediction,
            format_compliant=True,
            latency_ms=elapsed_ms(started_at),
            raw_content=raw_content,
            **usage,
        )

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint = (
            f"{self.base_url}/models/{quote(self.model, safe='-._')}:"
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
                response_body = response.read().decode("utf-8")
        except HTTPError as error:
            error_body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Gemini 호출 실패: HTTP {error.code}; body={error_body[:500]}"
            ) from error
        except (URLError, TimeoutError) as error:
            raise RuntimeError("Gemini 범위 분류 모델을 호출할 수 없습니다.") from error

        parsed = json.loads(response_body)
        if not isinstance(parsed, dict):
            raise RuntimeError("Gemini 응답이 JSON 객체가 아닙니다.")
        return parsed

    @staticmethod
    def _extract_text(response_payload: dict[str, Any]) -> str:
        candidates = response_payload.get("candidates") or []
        if not candidates:
            raise RuntimeError("Gemini 응답에 candidates가 없습니다.")
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        texts = [
            str(part.get("text"))
            for part in parts
            if part.get("text") and not part.get("thought")
        ]
        if not texts:
            raise RuntimeError("Gemini 응답에 최종 text part가 없습니다.")
        return "\n".join(texts)

    def _extract_usage(self, response_payload: dict[str, Any]) -> dict[str, Any]:
        metadata = response_payload.get("usageMetadata") or {}
        input_tokens = int(metadata.get("promptTokenCount") or 0)
        output_tokens = int(metadata.get("candidatesTokenCount") or 0)
        thinking_tokens = int(metadata.get("thoughtsTokenCount") or 0)
        total_tokens = int(metadata.get("totalTokenCount") or 0)
        if not output_tokens and not thinking_tokens and total_tokens >= input_tokens:
            output_tokens = total_tokens - input_tokens
        input_price, output_price = MODEL_PRICING_USD_PER_MILLION.get(
            self.model,
            (0.0, 0.0),
        )
        estimated_cost = (
            input_tokens * input_price
            + (output_tokens + thinking_tokens) * output_price
        ) / 1_000_000
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "thinking_tokens": thinking_tokens,
            "estimated_cost_usd": round(estimated_cost, 10),
        }

    @staticmethod
    def _empty_usage() -> dict[str, int | float]:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "estimated_cost_usd": 0.0,
        }


def make_output_dir(output_dir: Path | None, model: str, prompt_version: str) -> Path:
    """모델과 프롬프트 버전이 드러나는 결과 폴더를 만든다."""
    if output_dir is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_model = model.replace(":", "_").replace("/", "_")
        output_dir = (
            DEFAULT_OUTPUT_ROOT
            / f"query_scope_v01_{safe_model}_prompt_{prompt_version}_{timestamp}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def parse_args() -> argparse.Namespace:
    """Gemini 범위 분류 평가 옵션을 정의한다."""
    parser = argparse.ArgumentParser(
        description="Evaluate Gemini Flash-Lite for precedent query scope.",
    )
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--model", default=DEFAULT_GEMINI_MODEL)
    parser.add_argument("--gemini-url", default=DEFAULT_GEMINI_BASE_URL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=DEFAULT_MAX_OUTPUT_TOKENS,
    )
    parser.add_argument(
        "--prompt-version",
        choices=sorted(SYSTEM_PROMPTS),
        default=DEFAULT_PROMPT_VERSION,
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    """환경에서 키를 읽고 Gemini 평가를 실행해 보고서를 저장한다."""
    args = parse_args()
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit은 1 이상이어야 합니다.")
    load_env_file()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY가 설정되어 있지 않습니다.")

    dataset_path = args.dataset or (
        DECISION_DATASET_PATH
        if args.prompt_version == "v3"
        else DEFAULT_DATASET_PATH
    )
    cases: list[EvaluationCase] = load_evaluation_cases(dataset_path, args.limit)
    classifier = GeminiQueryScopeClassifier(
        api_key=api_key,
        model=args.model,
        base_url=args.gemini_url,
        timeout_seconds=args.timeout,
        max_output_tokens=args.max_output_tokens,
        prompt_version=args.prompt_version,
    )
    output_dir = make_output_dir(
        args.output_dir,
        classifier.model,
        args.prompt_version,
    )
    attempts, summary = evaluate_model(classifier, cases)
    pricing = MODEL_PRICING_USD_PER_MILLION.get(classifier.model, (0.0, 0.0))
    config = {
        "provider": "gemini",
        "base_url": args.gemini_url,
        "timeout_seconds": args.timeout,
        "max_output_tokens": args.max_output_tokens,
        "temperature": 0,
        "thinking_level": "minimal",
        "prompt_version": args.prompt_version,
        "input_price_usd_per_million": pricing[0],
        "output_price_usd_per_million": pricing[1],
        "schema": classifier.output_schema,
    }
    write_outputs(
        output_dir,
        dataset_path,
        cases,
        {classifier.model: (attempts, summary)},
        config,
    )
    print(f"결과 저장: {output_dir}")
    print(
        f"{summary['model']}: raw={format_rate(summary['raw_accuracy'])}, "
        f"fail_open={format_rate(summary['fail_open_accuracy'])}, "
        f"format={format_rate(summary['format_compliance_rate'])}, "
        f"false_blocks={summary['false_block_count']}, "
        f"cost=${summary['usage']['estimated_cost_usd']:.6f}",
    )


if __name__ == "__main__":
    main()
