# generate_easy_summaries_with_terms.py
"""
Description: 판례 생성요약에서 최종 용어 DB 용어를 탐지하고, Gemini가 문맥에 맞는
정의 ID를 선택한 뒤 용어 정의를 참고한 쉬운요약 실험 결과를 저장한다.
Author: choeminju
Date: 2026-10-06
Before:
    - local_data/precedents/processed/final_cases/에 생성요약이 포함된 판례 JSON이 있다.
    - local_data/legal_terms/final_v01/legal_terms_981.jsonl에 최종 용어 DB가 있다.
After:
    - 용어 DB 후보를 포함한 프롬프트 입력과 Gemini 쉬운요약 결과·manifest가 저장된다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from precedent_config import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_GENERATE_URL_TEMPLATE,
    PROCESSED_DIR,
    PROJECT_ROOT,
    load_env_file,
    now_utc_iso,
)

try:
    from ml.preprocessing.precedents.extract_legal_term_candidates import (
        AhoCorasickMatcher,
        normalize_match_text,
    )
except ModuleNotFoundError:
    import sys

    sys.path.append(str(PROJECT_ROOT))
    from ml.preprocessing.precedents.extract_legal_term_candidates import (
        AhoCorasickMatcher,
        normalize_match_text,
    )


FINAL_CASES_DIR = PROCESSED_DIR / "final_cases"
DEFAULT_TERM_DB_PATH = (
    PROJECT_ROOT / "local_data" / "legal_terms" / "final_v01" / "legal_terms_981.jsonl"
)
DEFAULT_OUTPUT_DIR = PROCESSED_DIR / "easy_summaries_term_db"
DEFAULT_FIELD_NAME = "쉬운요약"
SCHEMA_VERSION = "precedent_easy_summary_term_db.v0.1"
MANIFEST_SCHEMA_VERSION = "precedent_easy_summary_term_db_manifest.v0.1"
PROMPT_VERSION = "precedent_easy_summary_term_db_gemini.v13"
MAX_EASY_SUMMARY_CHARS = 700
REVIEW_MIN_EASY_SUMMARY_CHARS = 80
GEMINI_PRICING_USD_PER_MILLION = {
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
}
DEFAULT_EXPERIMENT_CASE_IDS = [
    "104528",
    "101930",
    "125973",
    "179740",
    "101064",
    "144186",
    "104484",
    "107884",
    "107798",
    "128004",
]
RISKY_TERMS = {
    "처분행위",
    "대항력",
    "명의수탁자",
    "명의신탁약정",
    "선관의무",
    "비채변제",
    "담보책임",
    "가액반환",
    "각하",
    "보전",
    "본등기",
    "추완",
    "수임",
    "공소",
    "경합",
}
LOW_VALUE_TERMS = {
    "등기",
    "법리",
    "심판",
    "증명",
    "과실",
    "설정",
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
    """용어 DB 기반 판례 쉬운요약 실험 옵션을 정의한다."""
    load_env_file()
    parser = argparse.ArgumentParser(
        description="Generate easy precedent summaries with final legal-term DB candidates."
    )
    parser.add_argument("--term-db-path", type=Path, default=DEFAULT_TERM_DB_PATH)
    parser.add_argument("--final-cases-dir", type=Path, default=FINAL_CASES_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--gemini-model",
        default=os.environ.get("PRECEDENT_EASY_SUMMARY_GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
    )
    parser.add_argument("--field-name", default=DEFAULT_FIELD_NAME)
    parser.add_argument("--run-name", default=None)
    parser.add_argument(
        "--case-ids",
        default=",".join(DEFAULT_EXPERIMENT_CASE_IDS),
        help="처리할 쉼표 구분 판례일련번호. 기본값은 용어 DB 실험용 10건.",
    )
    parser.add_argument("--case-ids-from-file", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-terms", type=int, default=12)
    parser.add_argument("--delay", type=float, default=0.05)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="프롬프트 입력과 manifest만 저장하고 Gemini API를 호출하지 않는다.",
    )
    parser.add_argument(
        "--write-final",
        action="store_true",
        help="실험 결과를 final case JSON의 쉬운요약 필드에 반영한다. 기본값은 결과 파일만 저장.",
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


def sanitize_run_name(value: str) -> str:
    """실행 이름에 안전한 파일명 문자열만 남긴다."""
    return re.sub(r"[^A-Za-z0-9가-힣_.-]+", "-", value).strip("-")


def normalize_gemini_model_name(model: str) -> str:
    """Gemini REST URL과 manifest에 사용할 모델명을 정리한다."""
    cleaned = model.strip()
    if cleaned.startswith("models/"):
        return cleaned.removeprefix("models/")
    return cleaned


def build_run_paths(output_dir: Path, run_name: str) -> RunPaths:
    """실행 이름에 대응되는 결과 파일 경로를 만든다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    return RunPaths(
        inputs=output_dir / f"{run_name}_inputs.jsonl",
        results=output_dir / f"{run_name}_results.jsonl",
        failures=output_dir / f"{run_name}_failures.jsonl",
        manifest=output_dir / f"{run_name}_manifest.json",
    )


def reset_run_outputs(paths: RunPaths) -> None:
    """같은 run-name 재실행 시 이전 결과·실패 로그가 섞이지 않도록 정리한다."""
    for path in (paths.results, paths.failures):
        if path.exists():
            path.unlink()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 파일을 행 목록으로 읽는다."""
    with path.open("r", encoding="utf-8-sig") as file:
        return [json.loads(line) for line in file if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """행 목록을 UTF-8 JSONL로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """한 행을 JSONL 파일에 이어 쓴다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_case_ids(args: argparse.Namespace) -> list[str]:
    """CLI 또는 파일에서 판례 ID 목록을 읽는다."""
    if args.case_ids_from_file:
        text = args.case_ids_from_file.read_text(encoding="utf-8")
        if args.case_ids_from_file.suffix == ".json":
            payload = json.loads(text)
            ids = [
                str(item.get("판례일련번호") or item.get("precedent_id") or item).strip()
                if isinstance(item, dict)
                else str(item).strip()
                for item in payload
            ]
        elif args.case_ids_from_file.suffix == ".jsonl":
            ids = [
                str(row.get("판례일련번호") or row.get("precedent_id") or "").strip()
                for row in read_jsonl(args.case_ids_from_file)
            ]
        else:
            ids = [line.strip() for line in text.splitlines()]
    else:
        ids = [case_id.strip() for case_id in args.case_ids.split(",")]
    result = [case_id for case_id in ids if case_id]
    if args.limit is not None:
        result = result[: args.limit]
    return result


def load_final_case(final_cases_dir: Path, case_id: str) -> dict[str, Any]:
    """판례일련번호에 해당하는 final case JSON을 읽는다."""
    path = final_cases_dir / f"{case_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"final case 파일이 없습니다: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_final_case(final_cases_dir: Path, case_id: str, case: dict[str, Any]) -> None:
    """final case JSON을 보기 좋은 UTF-8 JSON으로 저장한다."""
    path = final_cases_dir / f"{case_id}.json"
    path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_term_db(path: Path) -> dict[str, dict[str, Any]]:
    """최종 용어 DB를 매칭 키 기준으로 읽는다."""
    terms: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path):
        match_key = str(row.get("match_key") or "").strip() or normalize_match_text(row.get("term"))
        if len(match_key) < 2:
            continue
        definitions = []
        for definition in row.get("definitions") or []:
            definitions.append(
                {
                    "definition_id": str(definition.get("definition_id") or "").strip(),
                    "category": str(definition.get("category") or ""),
                    "source_definition": str(definition.get("source_definition") or "").strip(),
                    "source_link": str(definition.get("source_link") or ""),
                }
            )
        if not definitions:
            continue
        terms[match_key] = {
            "match_key": match_key,
            "term": str(row.get("term") or match_key),
            "definition_count": len(definitions),
            "generation_status": str(row.get("generation_status") or ""),
            "definition_warning": str(row.get("definition_warning") or ""),
            "definitions": definitions,
            "source_domains": row.get("source_domains") or [],
        }
    return terms


def score_candidate(row: dict[str, Any], occurrence_count: int) -> tuple[int, int, int, int, str]:
    """프롬프트에 넣을 용어 후보의 우선순위 점수를 만든다."""
    term = str(row["term"])
    risky_score = 1 if term in RISKY_TERMS or row["match_key"] in RISKY_TERMS else 0
    multi_score = 1 if int(row["definition_count"]) >= 2 else 0
    low_value_penalty = 1 if term in LOW_VALUE_TERMS or row["match_key"] in LOW_VALUE_TERMS else 0
    return (
        risky_score,
        multi_score,
        occurrence_count,
        -low_value_penalty,
        term,
    )


def detect_term_candidates(
    summary: str,
    term_db: dict[str, dict[str, Any]],
    max_terms: int,
) -> list[dict[str, Any]]:
    """생성요약에서 최종 용어 DB 후보를 찾아 우선순위 순서로 반환한다."""
    matcher = AhoCorasickMatcher({match_key: match_key for match_key in term_db})
    compact_summary = normalize_match_text(summary)
    counts = matcher.count(compact_summary)
    for risk_term in RISKY_TERMS:
        if risk_term in term_db and risk_term in compact_summary and risk_term not in counts:
            counts[risk_term] = compact_summary.count(risk_term)
    candidates = []
    for match_key, count in counts.items():
        row = term_db[match_key]
        candidates.append(
            {
                **row,
                "occurrence_count": int(count),
            }
        )
    candidates.sort(
        key=lambda row: score_candidate(row, row["occurrence_count"]),
        reverse=True,
    )
    return candidates[:max_terms]


def build_input_payload(
    case: dict[str, Any],
    term_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """프롬프트와 결과 저장에 공통으로 사용할 모델 입력 payload를 만든다."""
    return {
        "판례일련번호": normalize_text(case.get("판례일련번호")),
        "사건명": normalize_text(case.get("사건명")),
        "생성요약": normalize_text(case.get("생성요약")),
        "용어후보": [
            {
                "term": row["term"],
                "match_key": row["match_key"],
                "definition_count": row["definition_count"],
                "definitions": [
                    {
                        "definition_id": item["definition_id"],
                        "category": item["category"],
                        "source_definition": item["source_definition"],
                    }
                    for item in row["definitions"]
                ],
            }
            for row in term_candidates
        ],
    }


def build_prompt(payload: dict[str, Any], field_name: str) -> str:
    """용어 DB 후보와 함께 Gemini에 보낼 쉬운요약 프롬프트를 만든다."""
    input_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return f"""
너는 AlphaLawVA에서 법률에 익숙하지 않은 20~30대 사용자를 위한 판례 쉬운요약을 작성하는 리걸 에디터다.

[자료의 역할]
- 생성요약은 사건의 사실관계, 쟁점, 법원의 판단, 법적 결론의 유일한 근거다.
- 용어후보는 생성요약에 등장한 법률용어를 쉽게 풀기 위한 보조자료다.
- 용어후보의 정의를 사건의 새로운 사실, 요건, 판단 이유, 결론처럼 추가하지 않는다.

[작업]
1. 각 용어후보가 생성요약의 문맥에 맞는지 판단한다.
2. 문맥에 맞는 정의가 있으면 selected_definition_id에 해당 definition_id를 쓴다.
3. 문맥에 맞는 정의가 없거나 불확실하면 selected_definition_id를 null로 둔다.
4. 선택한 정의만 참고해 쉬운요약을 작성한다.
5. 생성요약에 없는 사실, 요건, 인과관계, 판단 이유, 법적 결론은 추가하지 않는다.
6. 용어후보에 없는 어려운 용어가 생성요약에 남아 있으면 uncovered_difficult_terms에 적는다.

[쉬운요약 작성 원칙]
- 단순히 짧게 압축하지 않는다.
- 사건의 상황, 다툰 내용, 법원의 판단이 자연스럽게 드러나게 쓴다.
- 필요한 법률용어는 남기되, 선택한 DB 정의가 있으면 그 정의의 범위 안에서 짧게 풀어쓴다.
- 문맥에 맞는 DB 정의가 없으면 용어를 억지로 설명하지 않는다.
- 권리자, 본인, 양수인, 수탁자, 위탁자 같은 법적 주체를 임의로 주인, 소유자 등으로 바꾸지 않는다.
- 파기, 파기환송, 기각, 인용 등 절차적 상태를 최종 책임 확정처럼 쓰지 않는다.
- 모든 문장은 '~다', '~했다', '~보았다', '~판단했다' 같은 평서형으로 쓴다.
- '~습니다', '~입니다', '~합니다'는 쓰지 않는다.
- '이 판례는'으로 시작하지 않는다.
- 사건번호, 법원명, 선고일자는 쓰지 않는다.
- 소송, 신고, 상담 등 사용자 행동을 권하지 않는다.
- 일반 판례는 대체로 150~450자로 쓴다. 쟁점이 여러 개인 복합 판례는 최대 700자까지 허용한다.
- 분량을 맞추려고 내용을 반복하거나 새로운 사실을 만들지 않는다.

[출력 전 자체 점검]
- 쉬운요약의 모든 사건 사실과 법원 판단이 생성요약에서 직접 확인되는지 확인한다.
- 용어 설명은 selected_definition_id로 고른 정의의 범위 안에 있는지 확인한다.
- 법적 주체, 조건, 절차적 상태, 판결 결과가 바뀌지 않았는지 확인한다.
- 점검 과정은 출력하지 않는다.

[출력 형식]
유효한 JSON 객체 하나만 출력한다. 마크다운 코드 블록, 제목, 설명, 인사말은 출력하지 않는다.
JSON 필드는 반드시 다음 네 개만 사용한다.

{{
  "판례일련번호": "입력과 동일한 값",
  "selected_terms": [
    {{
      "term": "용어후보의 term",
      "selected_definition_id": "선택한 definition_id 또는 null",
      "selection_reason": "생성요약 문맥과 정의 선택 이유를 한 문장으로 설명",
      "used_in_easy_summary": true
    }}
  ],
  "uncovered_difficult_terms": ["용어 DB 후보에 없지만 쉬운요약에서 설명하지 못한 어려운 용어"],
  "{field_name}": "용어 정의를 참고하되 생성요약의 의미를 벗어나지 않는 쉬운 판례 요약"
}}

[입력]
{input_json}
""".strip()


def parse_json_object(text: str) -> dict[str, Any]:
    """LLM 응답에서 JSON 객체만 안전하게 뽑아 파싱한다."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or start >= end:
            raise
        return json.loads(stripped[start : end + 1])


def get_gemini_api_key() -> str:
    """Gemini API 키를 환경변수에서 읽는다."""
    load_env_file()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(".env 또는 환경변수에 GEMINI_API_KEY를 설정해야 한다.")
    return api_key


def extract_gemini_text(response_payload: dict[str, Any]) -> str:
    """Gemini generateContent 응답에서 텍스트 파트를 합쳐 반환한다."""
    candidates = response_payload.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini 응답에 candidates가 없습니다: {response_payload}")
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    texts = [str(part.get("text", "")) for part in parts if part.get("text")]
    if not texts:
        raise RuntimeError(f"Gemini 응답에 text part가 없습니다: {response_payload}")
    return "\n".join(texts)


def call_gemini(prompt: str, model: str, timeout: float) -> tuple[str, dict[str, int]]:
    """Gemini API에 프롬프트를 보내고 원문 응답 텍스트와 사용량을 받는다."""
    api_key = get_gemini_api_key()
    model_name = normalize_gemini_model_name(model)
    endpoint = GEMINI_GENERATE_URL_TEMPLATE.format(model=model_name)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini 호출 실패: HTTP {exc.code}; body={error_body[:1200]}") from exc
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


def validate_output(
    payload: dict[str, Any],
    parsed: dict[str, Any],
    field_name: str,
) -> tuple[list[str], list[str]]:
    """선택 정의 ID와 쉬운요약 기본 형식을 검증한다."""
    errors = []
    warnings = []
    if parsed.get("판례일련번호") != payload["판례일련번호"]:
        errors.append("precedent_id_mismatch")
    easy_summary = normalize_easy_summary(parsed.get(field_name))
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

    definitions_by_term = {
        item["term"]: {definition["definition_id"] for definition in item["definitions"]}
        for item in payload["용어후보"]
    }
    selected_terms = parsed.get("selected_terms")
    if not isinstance(selected_terms, list):
        errors.append("selected_terms_not_list")
        selected_terms = []
    for index, item in enumerate(selected_terms):
        if not isinstance(item, dict):
            errors.append(f"selected_term_not_object:{index}")
            continue
        term = str(item.get("term") or "")
        selected_id = item.get("selected_definition_id")
        if term not in definitions_by_term:
            errors.append(f"unknown_selected_term:{index}:{term}")
            continue
        if selected_id is not None and str(selected_id) not in definitions_by_term[term]:
            errors.append(f"unknown_definition_id:{term}:{selected_id}")
        if not isinstance(item.get("used_in_easy_summary"), bool):
            errors.append(f"used_in_easy_summary_not_bool:{term}")
    if not isinstance(parsed.get("uncovered_difficult_terms"), list):
        errors.append("uncovered_difficult_terms_not_list")
    return errors, warnings


def build_result_row(
    payload: dict[str, Any],
    parsed: dict[str, Any],
    token_usage: dict[str, int],
    args: argparse.Namespace,
    validation_warnings: list[str],
) -> dict[str, Any]:
    """성공 결과 행을 만든다."""
    easy_summary = normalize_easy_summary(parsed.get(args.field_name))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "판례일련번호": payload["판례일련번호"],
        "사건명": payload["사건명"],
        "생성요약": payload["생성요약"],
        "term_candidates": payload["용어후보"],
        "selected_terms": parsed.get("selected_terms", []),
        "uncovered_difficult_terms": parsed.get("uncovered_difficult_terms", []),
        args.field_name: easy_summary,
        "provider": "gemini",
        "model": normalize_gemini_model_name(args.gemini_model),
        "prompt_version": PROMPT_VERSION,
        "token_usage": token_usage,
        "검증경고": validation_warnings,
        "final_case_updated": bool(args.write_final),
    }


def is_terminal_gemini_error(error: Exception) -> bool:
    """잔액·쿼터·결제처럼 같은 실행에서 계속 실패할 Gemini 오류인지 판단한다."""
    text = str(error).lower()
    return any(
        signal in text
        for signal in [
            "resource_exhausted",
            "insufficient",
            "quota",
            "billing",
            "credit",
            "permission_denied",
        ]
    )


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Gemini 사용량과 가격표로 예상 비용을 계산한다."""
    pricing = GEMINI_PRICING_USD_PER_MILLION.get(normalize_gemini_model_name(model))
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
    case_ids: list[str],
    dry_run_input_rows: list[dict[str, Any]],
) -> None:
    """실행 설정, 비용, 후보 통계를 manifest로 저장한다."""
    term_counts = Counter(
        candidate["term"]
        for row in dry_run_input_rows
        for candidate in row["term_candidates"]
    )
    estimated_cost_usd = estimate_cost_usd(
        args.gemini_model,
        stats.input_token_count,
        stats.output_token_count,
    )
    pricing = GEMINI_PRICING_USD_PER_MILLION.get(normalize_gemini_model_name(args.gemini_model))
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "started_at": stats.started_at,
        "finished_at": now_utc_iso(),
        "provider": "gemini",
        "model": normalize_gemini_model_name(args.gemini_model),
        "prompt_version": PROMPT_VERSION,
        "field_name": args.field_name,
        "dry_run": args.dry_run,
        "write_final": args.write_final,
        "case_ids": case_ids,
        "total_targets": stats.total_targets,
        "generated_count": stats.generated_count,
        "failure_count": stats.failure_count,
        "token_usage": {
            "input_tokens": stats.input_token_count,
            "output_tokens": stats.output_token_count,
            "total_tokens": stats.total_token_count,
        },
        "pricing_usd_per_million_tokens": pricing,
        "estimated_cost_usd": estimated_cost_usd,
        "stopped_reason": stats.stopped_reason,
        "term_candidate_counts": dict(term_counts.most_common()),
        "inputs_path": str(paths.inputs),
        "results_path": str(paths.results),
        "failures_path": str(paths.failures),
    }
    paths.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """용어 DB 후보 입력을 만들고, 선택 시 Gemini 쉬운요약 생성을 실행한다."""
    args = parse_args()
    run_name = sanitize_run_name(
        args.run_name or f"term_db_gemini_{normalize_gemini_model_name(args.gemini_model)}_sample"
    )
    paths = build_run_paths(args.output_dir, run_name)
    case_ids = load_case_ids(args)
    term_db = load_term_db(args.term_db_path)
    stats = RunStats(started_at=now_utc_iso(), total_targets=len(case_ids))

    input_rows = []
    for case_id in case_ids:
        case = load_final_case(args.final_cases_dir, case_id)
        summary = normalize_text(case.get("생성요약"))
        if not summary:
            raise ValueError(f"생성요약이 없는 판례입니다: {case_id}")
        term_candidates = detect_term_candidates(summary, term_db, args.max_terms)
        payload = build_input_payload(case, term_candidates)
        input_rows.append(
            {
                "판례일련번호": payload["판례일련번호"],
                "사건명": payload["사건명"],
                "생성요약": payload["생성요약"],
                "term_candidates": payload["용어후보"],
                "prompt": build_prompt(payload, args.field_name),
            }
        )
    write_jsonl(paths.inputs, input_rows)

    print(f"대상 판례: {len(case_ids)}건")
    print(f"프롬프트 입력 저장: {paths.inputs}")
    if args.dry_run:
        print("dry-run이므로 Gemini API를 호출하지 않습니다.")
        write_manifest(paths, args, stats, case_ids, input_rows)
        print(f"manifest 저장: {paths.manifest}")
        return

    reset_run_outputs(paths)
    for index, input_row in enumerate(input_rows, start=1):
        case_id = str(input_row["판례일련번호"])
        try:
            raw_response, token_usage = call_gemini(
                str(input_row["prompt"]),
                args.gemini_model,
                args.timeout,
            )
            parsed = parse_json_object(raw_response)
            payload = {
                "판례일련번호": input_row["판례일련번호"],
                "사건명": input_row["사건명"],
                "생성요약": input_row["생성요약"],
                "용어후보": input_row["term_candidates"],
            }
            validation_errors, validation_warnings = validate_output(
                payload,
                parsed,
                args.field_name,
            )
            if validation_errors:
                raise ValueError(f"출력 검증 실패: {validation_errors}; raw={raw_response[:800]}")
            row = build_result_row(payload, parsed, token_usage, args, validation_warnings)
            append_jsonl(paths.results, row)
            stats.generated_count += 1
            stats.input_token_count += int(token_usage.get("input_tokens") or 0)
            stats.output_token_count += int(token_usage.get("output_tokens") or 0)
            stats.total_token_count += int(token_usage.get("total_tokens") or 0)
            if args.write_final:
                case = load_final_case(args.final_cases_dir, case_id)
                case[args.field_name] = row[args.field_name]
                case["쉬운요약_용어선택"] = row["selected_terms"]
                write_final_case(args.final_cases_dir, case_id, case)
        except Exception as exc:
            stats.failure_count += 1
            append_jsonl(
                paths.failures,
                {
                    "failed_at": now_utc_iso(),
                    "판례일련번호": case_id,
                    "error": str(exc),
                },
            )
            print(f"[{index}/{len(input_rows)}] 실패: {case_id} {exc}", flush=True)
            if is_terminal_gemini_error(exc):
                stats.stopped_reason = f"terminal_gemini_error: {str(exc)[:500]}"
                break
        else:
            print(f"[{index}/{len(input_rows)}] 성공: {case_id}", flush=True)
            time.sleep(args.delay)

    write_manifest(paths, args, stats, case_ids, input_rows)
    print(
        f"완료: 생성 {stats.generated_count}건, 실패 {stats.failure_count}건, "
        f"예상 비용 {estimate_cost_usd(args.gemini_model, stats.input_token_count, stats.output_token_count)} USD"
    )


if __name__ == "__main__":
    main()
