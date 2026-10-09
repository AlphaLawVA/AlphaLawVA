# generate_easy_summaries.py
"""
Description: 최종 판례 JSON의 생성요약을 Claude Sonnet Batch API로 쉬운요약으로 다시 작성한다.
구조화된 결과와 실패 내역, 실행 비용 manifest를 저장하고 선택 시 final_cases를 갱신한다.
Author: choeminju
Date: 2026-09-14
Before:
    - local_data/precedents/processed/final_cases/에 생성요약이 포함된 최종 판례 JSON이 있다.
    - .env에 ANTHROPIC_API_KEY 또는 CLAUDE_API_KEY가 설정되어 있다.
After:
    - easy_summaries/에 배치 입력, 결과, 실패 내역, manifest가 저장된다.
    - --write-final 사용 시 각 final case JSON에 쉬운요약 필드가 추가된다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from precedent_config import PROCESSED_DIR, load_env_file, now_utc_iso


FINAL_CASES_DIR = PROCESSED_DIR / "final_cases"
EASY_SUMMARY_DIR = PROCESSED_DIR / "easy_summaries"
DEFAULT_FIELD_NAME = "쉬운요약"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
ANTHROPIC_BATCHES_URL = "https://api.anthropic.com/v1/messages/batches"
ANTHROPIC_API_VERSION = "2023-06-01"
SCHEMA_VERSION = "precedent_easy_summary.v2"
MANIFEST_SCHEMA_VERSION = "precedent_easy_summary_manifest.v2"
PROMPT_VERSION = "precedent_easy_summary_no_db_natural.v18_term_lists"
MAX_EASY_SUMMARY_CHARS = 700
REVIEW_MIN_EASY_SUMMARY_CHARS = 80
ANTHROPIC_PRICING_USD_PER_MILLION = {
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
}


@dataclass(frozen=True)
class RunPaths:
    inputs: Path
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
    """Claude Sonnet Batch 쉬운요약 생성 옵션을 정의한다."""
    load_env_file()
    parser = argparse.ArgumentParser(
        description="Generate easy precedent summaries with Claude Sonnet Batch API."
    )
    parser.add_argument("--final-cases-dir", type=Path, default=FINAL_CASES_DIR)
    parser.add_argument("--output-dir", type=Path, default=EASY_SUMMARY_DIR)
    parser.add_argument(
        "--anthropic-model",
        default=os.environ.get(
            "PRECEDENT_EASY_SUMMARY_ANTHROPIC_MODEL",
            DEFAULT_ANTHROPIC_MODEL,
        ),
    )
    parser.add_argument("--field-name", default=DEFAULT_FIELD_NAME)
    parser.add_argument("--run-name", default=None)
    parser.add_argument(
        "--case-ids",
        default=None,
        help="처리할 쉼표 구분 판례일련번호. 생략하면 생성요약이 있는 전체 판례를 대상으로 한다.",
    )
    parser.add_argument("--case-ids-from-file", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--sample-size",
        type=int,
        default=None,
        help="생성요약 길이 구간별로 섞은 테스트 샘플 개수.",
    )
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=4096,
        help="Claude 응답의 최대 출력 토큰 수.",
    )
    parser.add_argument(
        "--batch-submit",
        action="store_true",
        help="Anthropic Message Batch를 제출하고 즉시 종료한다.",
    )
    parser.add_argument(
        "--batch-collect",
        action="store_true",
        help="완료된 Batch 결과를 수집하고 검증한다.",
    )
    parser.add_argument("--batch-id", default=None)
    parser.add_argument(
        "--write-final",
        action="store_true",
        help="검증을 통과한 쉬운요약을 final case JSON에 반영한다.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="--write-final 시 기존 쉬운요약이 있어도 덮어쓴다.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="배치 입력과 manifest만 저장하고 유료 API를 호출하지 않는다.",
    )
    return parser.parse_args()


def normalize_text(value: Any) -> str:
    """과도한 공백을 줄인 한 문단 텍스트를 만든다."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_easy_summary(value: Any) -> str:
    """쉬운요약의 문단 구분을 보존하면서 과도한 공백을 정리한다."""
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
    """생성요약 변경 여부를 확인할 SHA-256 해시를 만든다."""
    return hashlib.sha256(normalize_text(summary).encode("utf-8")).hexdigest()


def sanitize_run_name(value: str) -> str:
    """실행 이름에 안전한 파일명 문자열만 남긴다."""
    return re.sub(r"[^A-Za-z0-9가-힣_.-]+", "-", value).strip("-")


def build_run_paths(output_dir: Path, run_name: str) -> RunPaths:
    """실행 이름에 대응되는 입출력 파일 경로를 만든다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    return RunPaths(
        inputs=output_dir / f"{run_name}_inputs.jsonl",
        results=output_dir / f"{run_name}_results.jsonl",
        failures=output_dir / f"{run_name}_failures.jsonl",
        manifest=output_dir / f"{run_name}_manifest.json",
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 파일을 행 목록으로 읽는다."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig") as file:
        return [json.loads(line) for line in file if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """행 목록을 JSONL 파일로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """한 행을 JSONL 파일에 추가한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False) + "\n")


def reset_run_outputs(paths: RunPaths) -> None:
    """결과 수집 전에 같은 실행의 이전 결과와 실패 기록을 비운다."""
    for path in (paths.results, paths.failures):
        if path.exists():
            path.unlink()


def load_ids_from_file(path: Path) -> list[str]:
    """JSON, JSONL, 일반 텍스트에서 판례 ID를 읽는다."""
    if not path.exists():
        raise FileNotFoundError(f"판례 ID 파일이 없습니다: {path}")
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
        values = payload if isinstance(payload, list) else payload.get("case_ids", [])
        return [str(value).strip() for value in values if str(value).strip()]
    if path.suffix.lower() == ".jsonl":
        ids = []
        for row in read_jsonl(path):
            case_id = row.get("판례일련번호") or row.get("precedent_id") or row.get("id")
            if case_id is not None:
                ids.append(str(case_id).strip())
        return [case_id for case_id in ids if case_id]
    return [value for value in re.split(r"[\s,]+", text) if value]


def requested_case_ids(args: argparse.Namespace) -> list[str] | None:
    """명령행에서 지정한 판례 ID를 중복 없이 읽는다."""
    values = []
    if args.case_ids:
        values.extend(value.strip() for value in args.case_ids.split(","))
    if args.case_ids_from_file:
        values.extend(load_ids_from_file(args.case_ids_from_file))
    if not values:
        return None
    return list(dict.fromkeys(value for value in values if value))


def load_final_case(path: Path) -> dict[str, Any]:
    """최종 판례 JSON을 읽는다."""
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_final_case(path: Path, case: dict[str, Any]) -> None:
    """최종 판례 JSON을 보기 좋은 형식으로 저장한다."""
    path.write_text(
        json.dumps(case, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def collect_target_paths(args: argparse.Namespace) -> list[Path]:
    """생성요약이 있는 처리 대상 판례 파일을 결정한다."""
    case_ids = requested_case_ids(args)
    if case_ids is None:
        paths = sorted(args.final_cases_dir.glob("*.json"))
    else:
        paths = [args.final_cases_dir / f"{case_id}.json" for case_id in case_ids]
        missing = [str(path) for path in paths if not path.exists()]
        if missing:
            raise FileNotFoundError(f"판례 파일이 없습니다: {missing[:10]}")

    targets = []
    for path in paths:
        case = load_final_case(path)
        if not normalize_text(case.get("생성요약")):
            continue
        if (
            args.write_final
            and not args.overwrite
            and normalize_text(case.get(args.field_name))
        ):
            continue
        targets.append(path)
    if args.sample_size is not None:
        targets = build_length_balanced_sample(targets, args.sample_size)
    if args.limit is not None:
        targets = targets[: args.limit]
    return targets


def build_length_balanced_sample(paths: list[Path], sample_size: int) -> list[Path]:
    """생성요약 길이 순서 전체에서 고르게 테스트 판례를 고른다."""
    if sample_size <= 0:
        return []
    if sample_size >= len(paths):
        return paths
    ranked = sorted(
        paths,
        key=lambda path: len(normalize_text(load_final_case(path).get("생성요약"))),
    )
    if sample_size == 1:
        return [ranked[len(ranked) // 2]]
    indexes = {
        round(index * (len(ranked) - 1) / (sample_size - 1))
        for index in range(sample_size)
    }
    return [ranked[index] for index in sorted(indexes)]


def build_prompt(case: dict[str, Any]) -> str:
    """용어 DB 없이 생성요약을 자연스럽게 다시 쓰는 프롬프트를 만든다."""
    input_json = json.dumps(
        {
            "판례일련번호": str(case.get("판례일련번호") or ""),
            "사건명": normalize_text(case.get("사건명")),
            "생성요약": normalize_text(case.get("생성요약")),
        },
        ensure_ascii=False,
        indent=2,
    )
    return f"""
너는 AlphaLawVA에서 20대 사회초년생과 법에 익숙하지 않은 초년생들을 위한 판례 쉬운요약을 작성하는 리걸 에디터다.

[자료의 역할]
- 생성요약은 사건의 사실관계, 쟁점, 법원의 판단, 법적 결론의 유일한 근거다.
- 별도의 용어 DB는 제공되지 않는다.
- 생성요약에 없는 사실, 요건, 인과관계, 판단 이유, 법적 결론은 추가하지 않는다.

[작업]
1. 생성요약의 의미를 보존하면서 20대 사회초년생과 법에 익숙하지 않은 초년생들이 읽기 쉬운 판례 요약을 작성한다.
2. 어려운 법률용어나 판결문투는 쉬운 말로 바꿔 쓴다.
3. 꼭 필요한 법률용어는 남길 수 있지만, 괄호로 단어 뜻을 붙이는 방식은 쓰지 않는다.
4. 생성요약의 문맥만으로 의미를 안전하게 풀어쓸 수 없는 법률용어는 억지로 설명하지 말고 그대로 두며, uncovered_difficult_terms에 적는다.

[쉬운요약 작성 원칙]
- 사건의 상황, 다툰 내용, 법원의 판단이 자연스럽게 드러나게 쓴다.
- 생성요약 문장을 그대로 베끼지 말고, 일상적인 문장으로 다시 쓴다.
- 법적 주체와 절차 상태를 바꾸지 않는다.
- 파기, 파기환송, 기각, 인용, 각하 같은 절차 상태를 최종 책임 확정처럼 쓰지 않는다.
- 모든 문장은 '~다', '~했다', '~보았다', '~판단했다' 같은 평서형으로 쓴다.
- '~습니다', '~입니다', '~합니다'는 쓰지 않는다.
- '이 판례는'으로 시작하지 않는다.
- 사건번호, 법원명, 선고일자는 쓰지 않는다.
- 소송, 신고, 상담 등 사용자 행동을 권하지 않는다.
- 일반 판례는 대체로 150~450자로 쓴다. 쟁점이 여러 개인 복합 판례는 최대 700자까지 허용한다.

[출력 전 자체 점검]
- 쉬운요약의 모든 사건 사실과 법원 판단이 생성요약에서 직접 확인되는지 확인한다.
- 법적 주체, 조건, 절차적 상태, 판결 결과가 바뀌지 않았는지 확인한다.
- 점검 과정은 출력하지 않는다.

[출력]
- precedent_id는 입력의 판례일련번호와 동일하게 쓴다.
- easy_summary에는 완성된 쉬운요약만 쓴다.
- explained_difficult_terms에는 생성요약에서 어렵다고 판단했지만 쉬운요약에서 쉬운 말로 풀어쓴 용어 이름만 적는다.
- uncovered_difficult_terms에는 쉬운요약에 그대로 남은 어려운 법률용어만 쓴다.

[입력]
{input_json}
""".strip()


def build_input_rows(paths: list[Path]) -> list[dict[str, Any]]:
    """배치 제출과 결과 결합에 사용할 입력 행을 만든다."""
    rows = []
    for path in paths:
        case = load_final_case(path)
        case_id = str(case.get("판례일련번호") or path.stem)
        rows.append(
            {
                "판례일련번호": case_id,
                "사건명": normalize_text(case.get("사건명")),
                "생성요약": normalize_text(case.get("생성요약")),
                "source_path": str(path),
                "prompt": build_prompt(case),
            }
        )
    return rows


def get_anthropic_api_key() -> str:
    """Anthropic API 키를 환경변수에서 읽는다."""
    load_env_file()
    api_key = (
        os.environ.get("ANTHROPIC_API_KEY", "").strip()
        or os.environ.get("CLAUDE_API_KEY", "").strip()
    )
    if not api_key:
        raise RuntimeError(
            ".env 또는 환경변수에 ANTHROPIC_API_KEY 또는 CLAUDE_API_KEY를 설정해야 한다."
        )
    return api_key


def anthropic_request_headers() -> dict[str, str]:
    """Anthropic REST 요청 공통 헤더를 만든다."""
    return {
        "Content-Type": "application/json",
        "x-api-key": get_anthropic_api_key(),
        "anthropic-version": ANTHROPIC_API_VERSION,
    }


def build_output_schema() -> dict[str, Any]:
    """Claude 구조화 출력에 사용할 최소 JSON schema를 만든다."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "precedent_id": {"type": "string"},
            "easy_summary": {"type": "string"},
            "explained_difficult_terms": {
                "type": "array",
                "items": {"type": "string"},
            },
            "uncovered_difficult_terms": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "precedent_id",
            "easy_summary",
            "explained_difficult_terms",
            "uncovered_difficult_terms",
        ],
    }


def build_message_params(
    prompt: str,
    model: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    """Anthropic Batch 요청 한 건의 Messages 파라미터를 만든다."""
    return {
        "model": model.strip(),
        "max_tokens": max_output_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": build_output_schema(),
            }
        },
    }


def submit_batch(
    input_rows: list[dict[str, Any]],
    model: str,
    max_output_tokens: int,
    timeout: float,
) -> dict[str, Any]:
    """Anthropic Message Batch를 제출한다."""
    requests = [
        {
            "custom_id": str(row["판례일련번호"]),
            "params": build_message_params(
                str(row["prompt"]),
                model,
                max_output_tokens,
            ),
        }
        for row in input_rows
    ]
    request = Request(
        ANTHROPIC_BATCHES_URL,
        data=json.dumps({"requests": requests}, ensure_ascii=False).encode("utf-8"),
        headers=anthropic_request_headers(),
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Anthropic batch 제출 실패: HTTP {exc.code}; body={error_body[:1200]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Anthropic batch 제출 실패: {exc}") from exc


def retrieve_batch(batch_id: str, timeout: float) -> dict[str, Any]:
    """Anthropic Message Batch 상태를 조회한다."""
    request = Request(
        f"{ANTHROPIC_BATCHES_URL}/{batch_id}",
        headers=anthropic_request_headers(),
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Anthropic batch 조회 실패: HTTP {exc.code}; body={error_body[:1200]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Anthropic batch 조회 실패: {exc}") from exc


def fetch_batch_results(results_url: str, timeout: float) -> list[dict[str, Any]]:
    """완료된 Anthropic Message Batch 결과 JSONL을 읽는다."""
    request = Request(results_url, headers=anthropic_request_headers(), method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Anthropic batch 결과 조회 실패: HTTP {exc.code}; body={error_body[:1200]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Anthropic batch 결과 조회 실패: {exc}") from exc
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def extract_anthropic_text(message: dict[str, Any]) -> str:
    """Anthropic 응답에서 구조화된 JSON 텍스트를 꺼낸다."""
    blocks = message.get("content") or []
    texts = [str(block.get("text", "")) for block in blocks if block.get("type") == "text"]
    if not texts:
        raise RuntimeError(f"Anthropic 응답에 text content가 없습니다: {message}")
    return "\n".join(texts)


def parse_json_object(text: str) -> dict[str, Any]:
    """응답에서 JSON 객체를 안전하게 파싱한다."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        if start == -1:
            raise
        decoder = json.JSONDecoder()
        parsed, _ = decoder.raw_decode(stripped[start:])
        return parsed


def validate_output(
    input_row: dict[str, Any],
    parsed: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """판례 ID와 쉬운요약 기본 형식을 검증한다."""
    errors = []
    warnings = []
    if str(parsed.get("precedent_id") or "") != str(input_row["판례일련번호"]):
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
    if not isinstance(parsed.get("uncovered_difficult_terms"), list):
        errors.append("uncovered_difficult_terms_not_list")
    return errors, warnings


def build_result_row(
    input_row: dict[str, Any],
    parsed: dict[str, Any],
    token_usage: dict[str, int],
    args: argparse.Namespace,
    warnings: list[str],
) -> dict[str, Any]:
    """검증을 통과한 결과 한 행을 만든다."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "판례일련번호": input_row["판례일련번호"],
        "사건명": input_row["사건명"],
        "생성요약": input_row["생성요약"],
        "source_summary_hash": source_summary_hash(input_row["생성요약"]),
        args.field_name: normalize_easy_summary(parsed.get("easy_summary")),
        "explained_difficult_terms": parsed.get("explained_difficult_terms", []),
        "uncovered_difficult_terms": parsed.get("uncovered_difficult_terms", []),
        "provider": "anthropic",
        "model": args.anthropic_model.strip(),
        "prompt_version": PROMPT_VERSION,
        "execution_mode": "anthropic_message_batch",
        "token_usage": token_usage,
        "검증경고": warnings,
        "final_case_updated": False,
    }


def update_final_case(
    input_row: dict[str, Any],
    easy_summary: str,
    args: argparse.Namespace,
) -> bool:
    """선택된 경우 최종 판례 JSON에 쉬운요약을 반영한다."""
    if not args.write_final:
        return False
    path = Path(str(input_row["source_path"]))
    case = load_final_case(path)
    if normalize_text(case.get(args.field_name)) and not args.overwrite:
        return False
    case[args.field_name] = easy_summary
    write_final_case(path, case)
    return True


def collect_batch_results(
    paths: RunPaths,
    args: argparse.Namespace,
    stats: RunStats,
    input_rows: list[dict[str, Any]],
    batch_payload: dict[str, Any],
) -> None:
    """완료된 Batch 결과를 검증하고 결과 파일로 저장한다."""
    results_url = batch_payload.get("results_url")
    if not results_url:
        stats.stopped_reason = f"batch_not_ready:{batch_payload.get('processing_status')}"
        return
    input_by_id = {str(row["판례일련번호"]): row for row in input_rows}
    reset_run_outputs(paths)
    for batch_row in fetch_batch_results(str(results_url), args.timeout):
        case_id = str(batch_row.get("custom_id") or "")
        result = batch_row.get("result") or {}
        if result.get("type") != "succeeded":
            stats.failure_count += 1
            append_jsonl(
                paths.failures,
                {
                    "failed_at": now_utc_iso(),
                    "판례일련번호": case_id,
                    "batch_result": batch_row,
                },
            )
            continue
        message = result.get("message") or {}
        usage = message.get("usage") or {}
        token_usage = {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": int(usage.get("input_tokens") or 0)
            + int(usage.get("output_tokens") or 0),
        }
        stats.input_token_count += token_usage["input_tokens"]
        stats.output_token_count += token_usage["output_tokens"]
        stats.total_token_count += token_usage["total_tokens"]
        raw_response = ""
        try:
            input_row = input_by_id[case_id]
            raw_response = extract_anthropic_text(message)
            parsed = parse_json_object(raw_response)
            errors, warnings = validate_output(input_row, parsed)
            if errors:
                raise ValueError(f"출력 검증 실패: {errors}")
            row = build_result_row(input_row, parsed, token_usage, args, warnings)
            row["final_case_updated"] = update_final_case(
                input_row,
                row[args.field_name],
                args,
            )
            append_jsonl(paths.results, row)
            stats.generated_count += 1
        except Exception as exc:
            stats.failure_count += 1
            append_jsonl(
                paths.failures,
                {
                    "failed_at": now_utc_iso(),
                    "판례일련번호": case_id,
                    "error": str(exc),
                    "token_usage": token_usage,
                    "raw_response": raw_response,
                    "batch_result": batch_row,
                },
            )


def batch_pricing(model: str) -> dict[str, float] | None:
    """모델의 50% Batch 할인 가격을 반환한다."""
    pricing = ANTHROPIC_PRICING_USD_PER_MILLION.get(model.strip())
    if not pricing:
        return None
    return {
        "input": round(pricing["input"] * 0.5, 6),
        "output": round(pricing["output"] * 0.5, 6),
    }


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    """Batch 할인 가격과 실제 토큰으로 비용을 계산한다."""
    pricing = batch_pricing(model)
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
    batch_payload: dict[str, Any] | None,
) -> None:
    """실행 설정, Batch 상태, 사용량과 비용을 manifest에 저장한다."""
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "started_at": stats.started_at,
        "finished_at": now_utc_iso(),
        "provider": "anthropic",
        "model": args.anthropic_model.strip(),
        "execution_mode": "anthropic_message_batch",
        "batch_discount": 0.5,
        "prompt_version": PROMPT_VERSION,
        "field_name": args.field_name,
        "max_output_tokens": args.max_output_tokens,
        "dry_run": args.dry_run,
        "write_final": args.write_final,
        "overwrite": args.overwrite,
        "case_ids": [row["판례일련번호"] for row in input_rows],
        "total_targets": stats.total_targets,
        "generated_count": stats.generated_count,
        "failure_count": stats.failure_count,
        "token_usage": {
            "input_tokens": stats.input_token_count,
            "output_tokens": stats.output_token_count,
            "total_tokens": stats.total_token_count,
        },
        "pricing_usd_per_million_tokens": batch_pricing(args.anthropic_model),
        "estimated_cost_usd": estimate_cost_usd(
            args.anthropic_model,
            stats.input_token_count,
            stats.output_token_count,
        ),
        "stopped_reason": stats.stopped_reason,
        "batch": batch_payload,
        "inputs_path": str(paths.inputs),
        "results_path": str(paths.results),
        "failures_path": str(paths.failures),
    }
    paths.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """쉬운요약 Batch 입력을 만들고 제출 또는 결과 수집을 수행한다."""
    args = parse_args()
    if args.batch_submit and args.batch_collect:
        raise ValueError("--batch-submit과 --batch-collect는 동시에 사용할 수 없습니다.")
    run_name = sanitize_run_name(
        args.run_name or f"easy_summary_{args.anthropic_model}_batch"
    )
    paths = build_run_paths(args.output_dir, run_name)

    if args.batch_collect:
        if not args.batch_id:
            raise ValueError("--batch-collect에는 --batch-id가 필요합니다.")
        input_rows = read_jsonl(paths.inputs)
        if not input_rows:
            raise FileNotFoundError(
                f"제출 당시 입력 파일이 없습니다. 같은 --run-name을 사용해야 합니다: {paths.inputs}"
            )
    else:
        target_paths = collect_target_paths(args)
        input_rows = build_input_rows(target_paths)
        write_jsonl(paths.inputs, input_rows)

    stats = RunStats(started_at=now_utc_iso(), total_targets=len(input_rows))
    print(f"대상 판례: {len(input_rows)}건")
    print(f"배치 입력 저장: {paths.inputs}")

    if args.dry_run:
        write_manifest(paths, args, stats, input_rows, None)
        print("dry-run이므로 유료 Anthropic API를 호출하지 않습니다.")
        print(f"manifest 저장: {paths.manifest}")
        return

    if args.batch_submit:
        batch_payload = submit_batch(
            input_rows,
            args.anthropic_model,
            args.max_output_tokens,
            args.timeout,
        )
        write_manifest(paths, args, stats, input_rows, batch_payload)
        print(f"batch 제출: {batch_payload.get('id')}")
        print(f"batch 상태: {batch_payload.get('processing_status')}")
        print(f"manifest 저장: {paths.manifest}")
        return

    if args.batch_collect:
        batch_payload = retrieve_batch(args.batch_id, args.timeout)
        print(f"batch 상태: {batch_payload.get('processing_status')}")
        if batch_payload.get("processing_status") == "ended":
            collect_batch_results(paths, args, stats, input_rows, batch_payload)
            print(
                f"결과 저장: 생성 {stats.generated_count}건, 실패 {stats.failure_count}건, "
                f"예상 비용 {estimate_cost_usd(args.anthropic_model, stats.input_token_count, stats.output_token_count)} USD"
            )
        else:
            stats.stopped_reason = f"batch_not_ready:{batch_payload.get('processing_status')}"
            print("아직 완료되지 않아 결과를 저장하지 않았습니다.")
        write_manifest(paths, args, stats, input_rows, batch_payload)
        print(f"manifest 저장: {paths.manifest}")
        return

    write_manifest(paths, args, stats, input_rows, None)
    print("유료 호출을 하지 않았습니다. 제출하려면 --batch-submit을 사용하세요.")
    print(f"manifest 저장: {paths.manifest}")


if __name__ == "__main__":
    main()
