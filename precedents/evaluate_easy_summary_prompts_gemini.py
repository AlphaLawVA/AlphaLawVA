# evaluate_easy_summary_prompts_gemini.py
"""
Description: 저장된 판례 쉬운요약 프롬프트 입력을 Gemini Flash-Lite로 실행해
프롬프트 버전별 결과와 비용을 비교 가능한 JSONL 및 manifest로 저장한다.
Author: choeminju
Date: 2026-10-11
Before:
    - 판례일련번호, 사건명, 생성요약, prompt가 포함된 실험 입력 JSONL이 준비된 상태.
    - .env에 GEMINI_API_KEY가 설정된 상태.
After:
    - easy_summaries/에 Gemini 결과, 실패 내역, 실행 비용 manifest가 저장.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from precedent_config import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_GENERATE_URL_TEMPLATE,
    PROCESSED_DIR,
    load_env_file,
    now_utc_iso,
)


EASY_SUMMARY_DIR = PROCESSED_DIR / "easy_summaries"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
SCHEMA_VERSION = "precedent_easy_summary.v3"
MANIFEST_SCHEMA_VERSION = "precedent_easy_summary_gemini_manifest.v1"
MAX_EASY_SUMMARY_CHARS = 700
REVIEW_MIN_EASY_SUMMARY_CHARS = 80
GEMINI_PRICING_USD_PER_MILLION = {
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50},
}


@dataclass(frozen=True)
class RunPaths:
    results: Path
    failures: Path
    manifest: Path


@dataclass
class RunStats:
    started_at: str
    total_targets: int
    generated_count: int = 0
    failure_count: int = 0
    input_token_count: int = 0
    output_token_count: int = 0
    total_token_count: int = 0
    stopped_reason: str | None = None


def parse_args() -> argparse.Namespace:
    """Gemini 프롬프트 비교 실행 옵션을 정의한다."""
    load_env_file()
    parser = argparse.ArgumentParser(
        description="Evaluate stored precedent easy-summary prompts with Gemini."
    )
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--prompt-version", required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--output-dir", type=Path, default=EASY_SUMMARY_DIR)
    parser.add_argument(
        "--gemini-model",
        default=os.environ.get(
            "PRECEDENT_EASY_SUMMARY_GEMINI_MODEL",
            DEFAULT_GEMINI_MODEL or DEFAULT_MODEL,
        ),
    )
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def sanitize_run_name(value: str) -> str:
    """실행 이름에 파일명으로 안전한 문자만 남긴다."""
    return re.sub(r"[^A-Za-z0-9가-힣_.-]+", "-", value).strip("-")


def build_run_paths(output_dir: Path, run_name: str) -> RunPaths:
    """실행 이름에 대응하는 결과 경로를 만든다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_run_name(run_name)
    return RunPaths(
        results=output_dir / f"{safe_name}_results.jsonl",
        failures=output_dir / f"{safe_name}_failures.jsonl",
        manifest=output_dir / f"{safe_name}_manifest.json",
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 파일을 행 목록으로 읽는다."""
    if not path.exists():
        raise FileNotFoundError(f"입력 파일이 없습니다: {path}")
    with path.open("r", encoding="utf-8-sig") as file:
        return [json.loads(line) for line in file if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """행 목록을 UTF-8 JSONL로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_text(value: Any) -> str:
    """과도한 공백을 줄여 한 문단 문자열로 만든다."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_easy_summary(value: Any) -> str:
    """쉬운요약 문단은 유지하면서 과도한 공백을 정리한다."""
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = []
    for paragraph in re.split(r"\n{2,}", text):
        cleaned = re.sub(r"[ \t\f\v]+", " ", paragraph)
        cleaned = re.sub(r" *\n *", "\n", cleaned).strip()
        if cleaned:
            paragraphs.append(cleaned)
    return "\n\n".join(paragraphs).strip()


def source_summary_hash(summary: str) -> str:
    """입력 생성요약의 SHA-256 해시를 반환한다."""
    return hashlib.sha256(normalize_text(summary).encode("utf-8")).hexdigest()


def build_output_schema() -> dict[str, Any]:
    """Gemini 구조화 출력에 사용할 JSON schema를 만든다."""
    return {
        "type": "object",
        "properties": {
            "precedent_id": {"type": "string"},
            "easy_summary": {"type": "string"},
            "explained_difficult_terms": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "precedent_id",
            "easy_summary",
            "explained_difficult_terms",
        ],
    }


def get_gemini_api_key() -> str:
    """로컬 환경변수에서 Gemini API 키를 읽는다."""
    load_env_file()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(".env 또는 환경변수에 GEMINI_API_KEY가 필요합니다.")
    return api_key


def normalize_gemini_model_name(model: str) -> str:
    """Gemini REST 경로에 사용할 모델명을 정리한다."""
    cleaned = model.strip()
    return cleaned.removeprefix("models/")


def extract_gemini_text(response_payload: dict[str, Any]) -> str:
    """Gemini 응답의 첫 번째 후보에서 텍스트를 합친다."""
    candidates = response_payload.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini 응답에 candidates가 없습니다.")
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    texts = [str(part.get("text")) for part in parts if part.get("text")]
    if not texts:
        raise RuntimeError("Gemini 응답에 text part가 없습니다.")
    return "\n".join(texts)


def call_gemini(
    prompt: str,
    model: str,
    max_output_tokens: int,
    timeout: float,
) -> tuple[str, dict[str, int]]:
    """Gemini GenerateContent를 한 번 호출하고 텍스트와 사용량을 반환한다."""
    model_name = normalize_gemini_model_name(model)
    endpoint = GEMINI_GENERATE_URL_TEMPLATE.format(model=model_name)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.15,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
            "responseJsonSchema": build_output_schema(),
        },
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": get_gemini_api_key(),
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Gemini 호출 실패: HTTP {exc.code}; body={error_body[:1200]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Gemini 호출 실패: {exc}") from exc

    response_payload = json.loads(body)
    usage = response_payload.get("usageMetadata") or {}
    input_tokens = int(usage.get("promptTokenCount") or 0)
    total_tokens = int(usage.get("totalTokenCount") or 0)
    output_tokens = max(0, total_tokens - input_tokens)
    return extract_gemini_text(response_payload), {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def parse_json_object(text: str) -> dict[str, Any]:
    """구조화 출력 문자열을 JSON 객체로 읽는다."""
    parsed = json.loads(text.strip())
    if not isinstance(parsed, dict):
        raise ValueError("Gemini 출력이 JSON 객체가 아닙니다.")
    return parsed


def validate_output(
    input_row: dict[str, Any],
    parsed: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Gemini 결과의 ID, 본문 길이, 출력 필드 형식을 확인한다."""
    errors = []
    warnings = []
    case_id = str(input_row.get("판례일련번호") or "")
    if str(parsed.get("precedent_id") or "") != case_id:
        errors.append("precedent_id_mismatch")
    easy_summary = normalize_easy_summary(parsed.get("easy_summary"))
    if not easy_summary:
        errors.append("empty_easy_summary")
    if len(easy_summary) > MAX_EASY_SUMMARY_CHARS:
        errors.append(f"too_long:{len(easy_summary)}")
    if easy_summary and len(easy_summary) < REVIEW_MIN_EASY_SUMMARY_CHARS:
        warnings.append(f"review_too_short:{len(easy_summary)}")
    if easy_summary.startswith("이 판례는"):
        errors.append("starts_with_this_precedent")
    if any(value in easy_summary for value in ("입니다", "합니다", "했습니다")):
        errors.append("formal_polite_style")
    if not isinstance(parsed.get("explained_difficult_terms"), list):
        errors.append("explained_difficult_terms_not_list")
    return errors, warnings


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """표준 Gemini 토큰 가격으로 실행 비용을 추정한다."""
    pricing = GEMINI_PRICING_USD_PER_MILLION.get(model.strip())
    if not pricing:
        return None
    return round(
        input_tokens / 1_000_000 * pricing["input"]
        + output_tokens / 1_000_000 * pricing["output"],
        6,
    )


def write_manifest(
    paths: RunPaths,
    args: argparse.Namespace,
    stats: RunStats,
    input_rows: list[dict[str, Any]],
) -> None:
    """실행 조건, 사용량, 비용 및 결과 경로를 manifest에 저장한다."""
    model = normalize_gemini_model_name(args.gemini_model)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "started_at": stats.started_at,
        "finished_at": now_utc_iso(),
        "provider": "gemini",
        "model": model,
        "execution_mode": "gemini_generate_content",
        "prompt_version": args.prompt_version,
        "source_inputs_path": str(args.inputs),
        "case_ids": [str(row.get("판례일련번호") or "") for row in input_rows],
        "max_output_tokens": args.max_output_tokens,
        "dry_run": args.dry_run,
        "total_targets": stats.total_targets,
        "generated_count": stats.generated_count,
        "failure_count": stats.failure_count,
        "token_usage": {
            "input_tokens": stats.input_token_count,
            "output_tokens": stats.output_token_count,
            "total_tokens": stats.total_token_count,
        },
        "pricing_usd_per_million_tokens": GEMINI_PRICING_USD_PER_MILLION.get(model),
        "estimated_cost_usd": estimate_cost_usd(
            model,
            stats.input_token_count,
            stats.output_token_count,
        ),
        "stopped_reason": stats.stopped_reason,
        "results_path": str(paths.results),
        "failures_path": str(paths.failures),
    }
    paths.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """저장된 프롬프트를 Gemini로 실행하고 성공·실패를 즉시 기록한다."""
    args = parse_args()
    input_rows = read_jsonl(args.inputs)
    paths = build_run_paths(args.output_dir, args.run_name)
    stats = RunStats(started_at=now_utc_iso(), total_targets=len(input_rows))

    case_ids = [str(row.get("판례일련번호") or "") for row in input_rows]
    if not input_rows:
        raise ValueError("실행할 입력 행이 없습니다.")
    if any(not case_id for case_id in case_ids):
        raise ValueError("판례일련번호가 없는 입력이 있습니다.")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("입력에 중복 판례일련번호가 있습니다.")
    if any(not normalize_text(row.get("prompt")) for row in input_rows):
        raise ValueError("prompt가 비어 있는 입력이 있습니다.")

    print(f"대상 판례: {len(input_rows)}건")
    print(f"프롬프트 버전: {args.prompt_version}")
    print(f"모델: {normalize_gemini_model_name(args.gemini_model)}")

    if args.dry_run:
        write_manifest(paths, args, stats, input_rows)
        print("dry-run이므로 유료 Gemini API를 호출하지 않습니다.")
        print(f"manifest 저장: {paths.manifest}")
        return

    write_jsonl(paths.results, [])
    write_jsonl(paths.failures, [])
    for index, input_row in enumerate(input_rows, start=1):
        case_id = str(input_row["판례일련번호"])
        token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        raw_response = ""
        try:
            raw_response, token_usage = call_gemini(
                str(input_row["prompt"]),
                args.gemini_model,
                args.max_output_tokens,
                args.timeout,
            )
            parsed = parse_json_object(raw_response)
            errors, warnings = validate_output(input_row, parsed)
            if errors:
                raise ValueError(f"출력 검증 실패: {errors}")
            result_row = {
                "schema_version": SCHEMA_VERSION,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "판례일련번호": case_id,
                "사건명": normalize_text(input_row.get("사건명")),
                "생성요약": normalize_text(input_row.get("생성요약")),
                "source_summary_hash": source_summary_hash(input_row.get("생성요약", "")),
                "쉬운요약": normalize_easy_summary(parsed.get("easy_summary")),
                "explained_difficult_terms": parsed.get("explained_difficult_terms", []),
                "provider": "gemini",
                "model": normalize_gemini_model_name(args.gemini_model),
                "prompt_version": args.prompt_version,
                "execution_mode": "gemini_generate_content",
                "token_usage": token_usage,
                "검증경고": warnings,
                "final_case_updated": False,
            }
            with paths.results.open("a", encoding="utf-8") as file:
                file.write(json.dumps(result_row, ensure_ascii=False) + "\n")
            stats.generated_count += 1
        except Exception as exc:
            failure_row = {
                "failed_at": now_utc_iso(),
                "판례일련번호": case_id,
                "error": str(exc),
                "token_usage": token_usage,
                "raw_response": raw_response,
            }
            with paths.failures.open("a", encoding="utf-8") as file:
                file.write(json.dumps(failure_row, ensure_ascii=False) + "\n")
            stats.failure_count += 1
        stats.input_token_count += token_usage["input_tokens"]
        stats.output_token_count += token_usage["output_tokens"]
        stats.total_token_count += token_usage["total_tokens"]
        print(f"[{index}/{len(input_rows)}] {case_id} 완료")
        if index < len(input_rows) and args.delay > 0:
            time.sleep(args.delay)

    write_manifest(paths, args, stats, input_rows)
    print(
        f"결과 저장: 생성 {stats.generated_count}건, 실패 {stats.failure_count}건, "
        f"예상 비용 {estimate_cost_usd(normalize_gemini_model_name(args.gemini_model), stats.input_token_count, stats.output_token_count)} USD"
    )
    print(f"manifest 저장: {paths.manifest}")


if __name__ == "__main__":
    main()
