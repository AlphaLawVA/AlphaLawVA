# generate_easy_summaries_with_terms.py
"""
Description: 판례 생성요약에서 최종 용어 DB 용어를 탐지하고, LLM이 문맥에 맞는
정의 ID를 선택한 뒤 용어 정의를 참고한 쉬운요약 실험 결과를 저장한다.
Author: choeminju
Date: 2026-10-06
Before:
    - local_data/precedents/processed/final_cases/에 생성요약이 포함된 판례 JSON이 있다.
    - local_data/legal_terms/final_v01/legal_terms_981.jsonl에 최종 용어 DB가 있다.
After:
    - 용어 DB 후보를 포함한 프롬프트 입력과 쉬운요약 결과·manifest가 저장된다.
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
DEFAULT_DIFFICULTY_OVERLAY_PATH = (
    DEFAULT_OUTPUT_DIR / "difficulty_overlay_sample10_v01.json"
)
DEFAULT_FIELD_NAME = "쉬운요약"
SCHEMA_VERSION = "precedent_easy_summary_term_db.v0.2"
MANIFEST_SCHEMA_VERSION = "precedent_easy_summary_term_db_manifest.v0.2"
PROMPT_VERSION = "precedent_easy_summary_term_db.v14"
DIFFICULTY_PROMPT_VERSION = "precedent_easy_summary_term_db.v15_difficulty_overlay"
NO_DB_NATURAL_PROMPT_VERSION = "precedent_easy_summary_no_db_natural.v16"
DB_NATURAL_PROMPT_VERSION = "precedent_easy_summary_term_db_natural.v17"
MAX_EASY_SUMMARY_CHARS = 700
REVIEW_MIN_EASY_SUMMARY_CHARS = 80
GEMINI_PRICING_USD_PER_MILLION = {
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
    "gemini-3.5-flash": {"input": 1.50, "output": 9.00},
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
}
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_BATCHES_URL = "https://api.anthropic.com/v1/messages/batches"
ANTHROPIC_API_VERSION = "2023-06-01"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
ANTHROPIC_PRICING_USD_PER_MILLION = {
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00},
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
    parser.add_argument("--difficulty-overlay-path", type=Path, default=None)
    parser.add_argument("--final-cases-dir", type=Path, default=FINAL_CASES_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--provider",
        choices=["gemini", "anthropic"],
        default=os.environ.get("PRECEDENT_EASY_SUMMARY_PROVIDER", "gemini"),
        help="유료 LLM 제공자. 기본값은 gemini.",
    )
    parser.add_argument(
        "--gemini-model",
        default=os.environ.get("PRECEDENT_EASY_SUMMARY_GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
    )
    parser.add_argument(
        "--anthropic-model",
        default=os.environ.get(
            "PRECEDENT_EASY_SUMMARY_ANTHROPIC_MODEL",
            DEFAULT_ANTHROPIC_MODEL,
        ),
    )
    parser.add_argument("--field-name", default=DEFAULT_FIELD_NAME)
    parser.add_argument(
        "--experiment-mode",
        choices=["term_parentheses", "no_db_natural", "db_natural"],
        default="term_parentheses",
        help="쉬운요약 실험 모드. 기본값은 기존 용어 DB 괄호 풀이 방식.",
    )
    parser.add_argument("--run-name", default=None)
    parser.add_argument(
        "--case-ids",
        default=",".join(DEFAULT_EXPERIMENT_CASE_IDS),
        help="처리할 쉼표 구분 판례일련번호. 기본값은 용어 DB 실험용 10건.",
    )
    parser.add_argument("--case-ids-from-file", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-terms", type=int, default=12)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=4096,
        help="Anthropic 응답의 최대 출력 토큰 수. 긴 구조화 출력 재시도 시 늘릴 수 있다.",
    )
    parser.add_argument("--delay", type=float, default=0.05)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument(
        "--batch-submit",
        action="store_true",
        help="Anthropic Message Batch를 제출하고 즉시 종료한다.",
    )
    parser.add_argument(
        "--batch-collect",
        action="store_true",
        help="Anthropic Message Batch 상태를 확인하고 완료된 결과를 저장한다.",
    )
    parser.add_argument("--batch-id", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="프롬프트 입력과 manifest만 저장하고 유료 LLM API를 호출하지 않는다.",
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


def contains_inline_explanation(text: str, term: str) -> bool:
    """용어 바로 뒤 괄호 풀이가 붙었는지 확인한다."""
    if not text or not term:
        return False
    pattern = rf"(?<![0-9A-Za-z가-힣]){re.escape(term)}\("
    return bool(re.search(pattern, text))


def sanitize_run_name(value: str) -> str:
    """실행 이름에 안전한 파일명 문자열만 남긴다."""
    return re.sub(r"[^A-Za-z0-9가-힣_.-]+", "-", value).strip("-")


def normalize_gemini_model_name(model: str) -> str:
    """Gemini REST URL과 manifest에 사용할 모델명을 정리한다."""
    cleaned = model.strip()
    if cleaned.startswith("models/"):
        return cleaned.removeprefix("models/")
    return cleaned


def selected_model_name(args: argparse.Namespace) -> str:
    """선택된 provider에 맞는 모델명을 반환한다."""
    if args.provider == "anthropic":
        return args.anthropic_model.strip()
    return normalize_gemini_model_name(args.gemini_model)


def prompt_version_for_rows(input_rows: list[dict[str, Any]]) -> str:
    """입력 row에 난이도 overlay가 적용되었는지에 따라 prompt version을 반환한다."""
    modes = {row.get("experiment_mode") for row in input_rows}
    if "no_db_natural" in modes:
        return NO_DB_NATURAL_PROMPT_VERSION
    if "db_natural" in modes:
        return DB_NATURAL_PROMPT_VERSION
    if any(
        "difficulty_level" in candidate
        for row in input_rows
        for candidate in row.get("term_candidates", [])
    ):
        return DIFFICULTY_PROMPT_VERSION
    return PROMPT_VERSION


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


def load_difficulty_overlay(path: Path | None) -> dict[str, dict[str, Any]]:
    """실험용 용어 난이도 overlay를 읽는다."""
    if path is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(f"difficulty overlay 파일이 없습니다: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "terms" in payload:
        rows = payload["terms"]
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError("difficulty overlay는 terms 배열을 가진 객체 또는 배열이어야 합니다.")
    overlay = {}
    for row in rows:
        term = normalize_text(row.get("term"))
        match_key = normalize_match_text(row.get("match_key") or term)
        if not term and not match_key:
            continue
        item = {
            "difficulty_level": str(row.get("difficulty_level") or "unknown"),
            "difficulty_priority": int(row.get("difficulty_priority") or 0),
            "difficulty_reason": normalize_text(row.get("difficulty_reason")),
        }
        if term:
            overlay[term] = item
        if match_key:
            overlay[match_key] = item
    return overlay


def apply_difficulty_overlay(
    term_candidates: list[dict[str, Any]],
    difficulty_overlay: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """용어 후보에 실험용 난이도 메타데이터를 붙인다."""
    if not difficulty_overlay:
        return term_candidates
    result = []
    for row in term_candidates:
        difficulty = (
            difficulty_overlay.get(str(row.get("term") or ""))
            or difficulty_overlay.get(str(row.get("match_key") or ""))
            or {
                "difficulty_level": "unknown",
                "difficulty_priority": 0,
                "difficulty_reason": "overlay에 없는 용어",
            }
        )
        result.append({**row, **difficulty})
    return result


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
    include_terms: bool = True,
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
                **(
                    {
                        "difficulty_level": row.get("difficulty_level", "unknown"),
                        "difficulty_priority": row.get("difficulty_priority", 0),
                        "difficulty_reason": row.get("difficulty_reason", ""),
                    }
                    if row.get("difficulty_level")
                    else {}
                ),
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
        ]
        if include_terms
        else [],
    }


def build_prompt(
    payload: dict[str, Any],
    field_name: str,
    experiment_mode: str = "term_parentheses",
) -> str:
    """용어 DB 후보와 함께 LLM에 보낼 쉬운요약 프롬프트를 만든다."""
    input_json = json.dumps(payload, ensure_ascii=False, indent=2)
    if experiment_mode == "no_db_natural":
        return build_no_db_natural_prompt(payload, field_name)
    if experiment_mode == "db_natural":
        return build_db_natural_prompt(payload, field_name)
    has_difficulty = any("difficulty_level" in item for item in payload.get("용어후보", []))
    difficulty_rules = """

[난이도 메타데이터 규칙]
각 용어후보에는 실험용 difficulty_level이 있을 수 있다. 이 값은 용어 DB에 난이도 필드가 있다고 가정한 비교 실험용 보조자료다.
- easy: 쉬운 일상어 또는 의미 추측이 쉬운 기본어다. 문맥상 맞는 definition_id는 고를 수 있지만 will_explain은 반드시 false로 둔다. 쉬운요약에서 괄호 풀이하지 않는다.
- medium: 사건 이해에 도움이 되는 법률·거래 용어다. hard 풀이 후 여유가 있고 사건 이해에 필요할 때 풀이한다.
- hard: 사건의 법적 쟁점 이해에 핵심인 어려운 용어다. 문맥에 맞는 정의가 있으면 가장 우선 풀이한다.
- procedure: 법원 절차, 소송 단계, 판결 결과를 나타내는 용어다. hard와 medium보다 뒤에 두되, 절차 상태를 오해할 위험이 크면 풀이한다.
- unknown: 난이도 정보가 없는 용어다. 기존 규칙에 따라 신중하게 판단한다.
- 풀이 우선순위는 hard > medium > procedure > easy다.
- 한 쉬운요약에서 will_explain=true는 최대 4개다.
- easy 용어는 selected_definition_id가 있어도 will_explain=false여야 한다.
""".rstrip() if has_difficulty else ""
    return f"""
너는 AlphaLawVA에서 법률에 익숙하지 않은 20~30대 사용자를 위한 판례 쉬운요약을 작성하는 리걸 에디터다.

[자료의 역할]
- 생성요약은 사건의 사실관계, 쟁점, 법원의 판단, 법적 결론의 유일한 근거다.
- 용어후보는 생성요약에 등장한 법률용어를 쉽게 풀기 위한 보조자료다.
- 용어후보의 source_definition은 그대로 복사할 문장이 아니라 뜻을 확인하기 위한 원 정의다.
- 용어후보의 정의를 사건의 새로운 사실, 요건, 판단 이유, 결론처럼 추가하지 않는다.
- 생성요약에 있는 표현으로 문맥을 좁혀 설명하는 것은 허용하지만, 생성요약에 없는 요건이나 효과를 덧붙이지 않는다.

[작업]
1. 각 용어후보마다 생성요약 문맥에 맞는 정의가 있는지 판단한다.
2. 문맥에 맞는 정의가 있으면 selected_definition_id에 해당 definition_id를 쓴다.
3. 문맥에 맞는 정의가 없거나 불확실하면 selected_definition_id를 null로 둔다.
4. selected_definition_id가 있는 용어 중 쉬운요약에서 실제로 풀이할 용어를 고른다.
5. 풀이할 용어는 법학 비전공 20~30대가 모를 만하고, 사건 이해에 필요한 용어를 우선한다.
6. 한 쉬운요약에서 풀이하는 용어는 보통 1~3개, 많아도 4개로 제한한다.
7. 이미 일상적으로 쉬운 용어이거나 사건 이해에 핵심이 아니면 selected_definition_id가 있어도 will_explain을 false로 둔다.
8. will_explain이 true인 용어는 inserted_phrase에 쉬운요약에 그대로 넣을 짧은 풀이 문구를 작성한다.
9. will_explain이 true인 inserted_phrase는 쉬운요약에서 정확히 한 번 등장해야 한다.
10. 용어후보에 없지만 생성요약 이해에 중요한 어려운 법률용어가 남으면 uncovered_difficult_terms에 적는다.

[쉬운요약 작성 원칙]
- 단순히 짧게 압축하지 않는다.
- 사건의 상황, 다툰 내용, 법원의 판단이 자연스럽게 드러나게 쓴다.
- 생성요약 문장을 그대로 베끼지 말고, 한자어나 판결문투를 일상적인 표현으로 바꿔 쓴다.
- 필요한 법률용어는 남기되, will_explain이 true인 용어는 처음 등장할 때 반드시 짧게 풀이한다.
- 문맥에 맞는 DB 정의가 없으면 용어를 억지로 설명하지 않는다.
- DB 정의가 없는 용어는 일반 법률상식으로 설명하지 않는다. 쉬운요약에는 그대로 두고 uncovered_difficult_terms에 적는다.
- 권리자, 본인, 양수인, 수탁자, 위탁자 같은 법적 주체를 임의로 주인, 소유자 등으로 바꾸지 않는다.
- 파기, 파기환송, 기각, 인용 등 절차적 상태를 최종 책임 확정처럼 쓰지 않는다.
- 모든 문장은 '~다', '~했다', '~보았다', '~판단했다' 같은 평서형으로 쓴다.
- '~습니다', '~입니다', '~합니다'는 쓰지 않는다.
- '이 판례는'으로 시작하지 않는다.
- 사건번호, 법원명, 선고일자는 쓰지 않는다.
- 소송, 신고, 상담 등 사용자 행동을 권하지 않는다.
- 일반 판례는 대체로 150~450자로 쓴다. 쟁점이 여러 개인 복합 판례는 최대 700자까지 허용한다.
- 분량을 맞추려고 내용을 반복하거나 새로운 사실을 만들지 않는다.

[용어 풀이 방식]
- source_definition을 그대로 붙이지 않는다.
- "A인 B"처럼 긴 정의를 용어 앞에 붙이지 않는다.
- 권장 형식은 "용어(짧은 풀이)"다.
- 괄호 안 풀이는 30자 안팎의 짧은 말로 쓴다.
- 한 문장에 풀이를 여러 개 몰아넣어 문장을 길게 만들지 않는다.
- 같은 용어를 두 번 이상 풀이하지 않는다.
{difficulty_rules}

[좋은 예]
- 등기부(부동산의 권리관계를 적어 두는 공적 장부)에 적힌 토지가 거래 대상으로 특정되었다면, 현장을 볼 때 착오가 있었더라도 매매 대상은 등기부상 토지 전체로 본다.

[나쁜 예]
- 부동산의 권리관계를 적어 두는 공적 장부인 등기부에 적힌 토지가 거래 대상으로 특정된 목적물인 토지라면, 매수인에게 일정한 주의를 하여야 할 법률상의 의무인 주의의무는 없다.

[출력 전 자체 점검]
- 쉬운요약의 모든 사건 사실과 법원 판단이 생성요약에서 직접 확인되는지 확인한다.
- 용어 설명은 selected_definition_id로 고른 정의의 범위 안에 있는지 확인한다.
- 법적 주체, 조건, 절차적 상태, 판결 결과가 바뀌지 않았는지 확인한다.
- inserted_phrase가 쉬운요약 안에 그대로 1회 들어갔는지 확인한다.
- 점검 과정은 출력하지 않는다.

[출력 형식]
유효한 JSON 객체 하나만 출력한다. 마크다운 코드 블록, 제목, 설명, 인사말은 출력하지 않는다.
JSON 필드는 반드시 다음 네 개만 사용한다.

{{
  "판례일련번호": "입력과 동일한 값",
  "term_decisions": [
    {{
      "term": "용어후보의 term",
      "selected_definition_id": "선택한 definition_id 또는 null",
      "will_explain": true,
      "inserted_phrase": "will_explain이 true이면 쉬운요약에 그대로 넣을 짧은 풀이 문구, false이면 null",
      "selection_reason": "생성요약 문맥, 정의 선택, 풀이 여부를 한 문장으로 설명"
    }}
  ],
  "uncovered_difficult_terms": ["용어 DB 후보에 없지만 쉬운요약에서 설명하지 못한 어려운 용어"],
  "{field_name}": "용어 정의를 참고하되 생성요약의 의미를 벗어나지 않는 쉬운 판례 요약"
}}

[입력]
{input_json}
""".strip()


def build_no_db_natural_prompt(payload: dict[str, Any], field_name: str) -> str:
    """DB 없이 생성요약만으로 자연스러운 쉬운요약을 생성하는 프롬프트를 만든다."""
    input_json = json.dumps(
        {
            "판례일련번호": payload["판례일련번호"],
            "사건명": payload["사건명"],
            "생성요약": payload["생성요약"],
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

[출력 형식]
유효한 JSON 객체 하나만 출력한다. 마크다운 코드 블록, 제목, 설명, 인사말은 출력하지 않는다.
JSON 필드는 반드시 다음 네 개만 사용한다.

{{
  "판례일련번호": "입력과 동일한 값",
  "term_decisions": [],
  "uncovered_difficult_terms": ["쉬운요약에서 설명하지 못한 어려운 용어"],
  "{field_name}": "생성요약의 의미를 벗어나지 않는 자연스러운 쉬운 판례 요약"
}}

[입력]
{input_json}
""".strip()


def build_db_natural_prompt(payload: dict[str, Any], field_name: str) -> str:
    """DB 정의를 참고하되 괄호 풀이 없이 자연스러운 쉬운요약을 생성하는 프롬프트를 만든다."""
    input_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return f"""
너는 AlphaLawVA에서 20대 사회초년생과 법에 익숙하지 않은 초년생들을 위한 판례 쉬운요약을 작성하는 리걸 에디터다.

[자료의 역할]
- 생성요약은 사건의 사실관계, 쟁점, 법원의 판단, 법적 결론의 유일한 근거다.
- 용어후보는 생성요약에 등장한 법률용어를 쉽게 설명하기 위한 보조자료다.
- source_definition은 그대로 복사할 문장이 아니라, 법률용어의 뜻을 확인하고 쉬운 말로 풀어쓰기 위한 의미의 경계다.
- 용어후보의 정의를 사건의 새로운 사실, 요건, 판단 이유, 결론처럼 추가하지 않는다.
- 생성요약에 있는 표현으로 문맥을 좁혀 설명하는 것은 허용하지만, 생성요약에 없는 요건이나 효과를 덧붙이지 않는다.

[작업]
1. 각 용어후보마다 생성요약 문맥에 맞는 정의가 있는지 판단한다.
2. 문맥에 맞는 정의가 있으면 selected_definition_id에 해당 definition_id를 쓴다.
3. 문맥에 맞는 정의가 없거나 불확실하면 selected_definition_id를 null로 둔다.
4. 선택한 정의를 참고하여 독자가 이해하기 어려운 법률용어와 문장을 쉬운 말로 풀어 쓴다.
5. 정의 원문을 그대로 복사하거나 요약 앞뒤에 붙이지 않는다.
6. '용어(뜻풀이)'처럼 괄호 안에 정의를 넣지 않는다.
7. 용어의 의미가 문장 흐름 속에서 자연스럽게 이해되도록 문장 전체를 다시 작성한다.
8. 각 용어후보를 term_decisions에 정확히 한 번씩 기록한다.
9. 실제로 정의의 의미를 쉬운요약에 반영한 용어만 will_explain을 true로 둔다.
10. will_explain이 true인 용어는 inserted_phrase에 쉬운요약에 실제로 들어간 설명 문장 또는 문구를 그대로 쓴다.
11. 용어후보에 없지만 생성요약 이해에 중요한 어려운 법률용어가 남으면 uncovered_difficult_terms에 적는다.

[쉬운요약 작성 원칙]
- 대상 독자는 20대 사회초년생과 법에 익숙하지 않은 초년생들이다.
- 사건의 상황, 다툰 내용, 법원의 판단이 자연스럽게 드러나게 쓴다.
- 생성요약의 문장을 단순히 짧게 줄이거나 그대로 옮기지 말고, 일상적인 표현과 자연스러운 문장으로 다시 쓴다.
- 어려운 한자어, 판결문체, 지나치게 긴 문장은 쉬운 표현과 짧은 문장으로 바꾼다.
- 일상적인 단어까지 억지로 풀이하지 않는다.
- 정의 후보의 내용을 사건의 문맥에 맞게 좁혀 설명할 수 있지만, 정의에 없는 법적 요건이나 효과를 새로 만들어서는 안 된다.
- 법적 주체와 절차 상태를 바꾸지 않는다.
- 파기, 파기환송, 기각, 인용, 각하 같은 절차 상태를 최종 책임 확정처럼 쓰지 않는다.
- 모든 문장은 '~다', '~했다', '~보았다', '~판단했다' 같은 평서형으로 쓴다.
- '~습니다', '~입니다', '~합니다'는 쓰지 않는다.
- '이 판례는'으로 시작하지 않는다.
- 사건번호, 법원명, 선고일자는 쓰지 않는다.
- 소송, 신고, 상담 등 사용자 행동을 권하지 않는다.
- 일반 판례는 대체로 150~450자로 쓴다. 쟁점이 여러 개인 복합 판례는 최대 700자까지 허용한다.

[용어 풀이 방식]
- "용어(뜻풀이)" 형식은 쓰지 않는다.
- source_definition을 그대로 복사하지 않는다.
- 정의를 용어 앞에 'A인 B' 형태로 길게 붙이지 않는다.
- 좋은 방식: "세입자는 보증금을 돌려받기 전까지 새 집주인에게도 임대차가 계속된다고 주장할 수 있다. 이를 대항력이라고 한다."
- 나쁜 방식: "대항력(제삼자에게 권리를 주장할 수 있는 힘)"
- 나쁜 방식: "제삼자에게 권리를 주장할 수 있는 힘인 대항력이 인정된다."
- inserted_phrase는 쉬운요약 안에 그대로 1회 들어가야 한다.

[출력 전 자체 점검]
- 쉬운요약의 모든 사건 사실과 법원 판단이 생성요약에서 직접 확인되는지 확인한다.
- 용어 설명은 selected_definition_id로 고른 정의의 범위 안에 있는지 확인한다.
- 법적 주체, 조건, 절차적 상태, 판결 결과가 바뀌지 않았는지 확인한다.
- inserted_phrase가 쉬운요약 안에 그대로 1회 들어갔는지 확인한다.
- 점검 과정은 출력하지 않는다.

[출력 형식]
유효한 JSON 객체 하나만 출력한다. 마크다운 코드 블록, 제목, 설명, 인사말은 출력하지 않는다.
JSON 필드는 반드시 다음 네 개만 사용한다.

{{
  "판례일련번호": "입력과 동일한 값",
  "term_decisions": [
    {{
      "term": "용어후보의 term",
      "selected_definition_id": "선택한 definition_id 또는 null",
      "will_explain": true,
      "inserted_phrase": "will_explain이 true이면 쉬운요약에 그대로 들어간 자연스러운 설명 문장 또는 문구, false이면 null",
      "selection_reason": "생성요약 문맥, 정의 선택, 풀이 여부를 한 문장으로 설명"
    }}
  ],
  "uncovered_difficult_terms": ["용어 DB 후보에 없지만 쉬운요약에서 설명하지 못한 어려운 용어"],
  "{field_name}": "용어 정의를 참고하되 생성요약의 의미를 벗어나지 않는 자연스러운 쉬운 판례 요약"
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
        decoder = json.JSONDecoder()
        try:
            parsed, _ = decoder.raw_decode(stripped[start:])
            return parsed
        except json.JSONDecodeError:
            pass
        return json.loads(stripped[start : end + 1])


def normalize_output_keys(parsed: dict[str, Any], field_name: str) -> dict[str, Any]:
    """provider별 구조화 출력 키를 저장 스키마의 한국어 키로 맞춘다."""
    normalized = dict(parsed)
    if "precedent_id" in normalized and "판례일련번호" not in normalized:
        normalized["판례일련번호"] = normalized.pop("precedent_id")
    if "easy_summary" in normalized and field_name not in normalized:
        normalized[field_name] = normalized.pop("easy_summary")
    return normalized


def get_gemini_api_key() -> str:
    """Gemini API 키를 환경변수에서 읽는다."""
    load_env_file()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(".env 또는 환경변수에 GEMINI_API_KEY를 설정해야 한다.")
    return api_key


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


def extract_anthropic_text(response_payload: dict[str, Any], field_name: str) -> str:
    """Anthropic Messages API 응답에서 tool input 또는 텍스트 블록을 반환한다."""
    blocks = response_payload.get("content") or []
    for block in blocks:
        if block.get("type") == "tool_use" and block.get("input") is not None:
            tool_input = dict(block["input"])
            if "precedent_id" in tool_input and "판례일련번호" not in tool_input:
                tool_input["판례일련번호"] = tool_input.pop("precedent_id")
            if "easy_summary" in tool_input and field_name not in tool_input:
                tool_input[field_name] = tool_input.pop("easy_summary")
            return json.dumps(tool_input, ensure_ascii=False)
    texts = [str(block.get("text", "")) for block in blocks if block.get("type") == "text"]
    if not texts:
        raise RuntimeError(f"Anthropic 응답에 text content가 없습니다: {response_payload}")
    return "\n".join(texts)


def build_anthropic_output_schema() -> dict[str, Any]:
    """Claude structured output에 사용할 v14 JSON schema를 만든다."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "precedent_id": {"type": "string"},
            "term_decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "term": {"type": "string"},
                        "selected_definition_id": {"type": ["string", "null"]},
                        "will_explain": {"type": "boolean"},
                        "inserted_phrase": {"type": ["string", "null"]},
                        "selection_reason": {"type": "string"},
                    },
                    "required": [
                        "term",
                        "selected_definition_id",
                        "will_explain",
                        "inserted_phrase",
                        "selection_reason",
                    ],
                },
            },
            "uncovered_difficult_terms": {
                "type": "array",
                "items": {"type": "string"},
            },
            "easy_summary": {"type": "string"},
        },
        "required": [
            "precedent_id",
            "term_decisions",
            "uncovered_difficult_terms",
            "easy_summary",
        ],
    }


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


def call_anthropic(
    prompt: str,
    model: str,
    timeout: float,
    field_name: str,
    max_output_tokens: int,
) -> tuple[str, dict[str, int]]:
    """Anthropic Messages API에 프롬프트를 보내고 원문 응답 텍스트와 사용량을 받는다."""
    payload = build_anthropic_message_params(prompt, model, max_output_tokens)
    request = Request(
        ANTHROPIC_MESSAGES_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=anthropic_request_headers(),
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Anthropic 호출 실패: HTTP {exc.code}; body={error_body[:1200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Anthropic 호출 실패: {exc}") from exc
    response_payload = json.loads(body)
    usage = response_payload.get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    return extract_anthropic_text(response_payload, field_name), {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def anthropic_request_headers() -> dict[str, str]:
    """Anthropic REST 요청 공통 헤더를 만든다."""
    return {
        "Content-Type": "application/json",
        "x-api-key": get_anthropic_api_key(),
        "anthropic-version": ANTHROPIC_API_VERSION,
    }


def build_anthropic_message_params(
    prompt: str,
    model: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    """동기·배치 호출에 공통으로 쓰는 Anthropic Messages 파라미터를 만든다."""
    return {
        "model": model.strip(),
        "max_tokens": max_output_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": build_anthropic_output_schema(),
            }
        },
    }


def submit_anthropic_batch(
    input_rows: list[dict[str, Any]],
    model: str,
    timeout: float,
    max_output_tokens: int,
) -> dict[str, Any]:
    """Anthropic Message Batch를 제출한다."""
    requests = [
        {
            "custom_id": str(row["판례일련번호"]),
            "params": build_anthropic_message_params(
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
        raise RuntimeError(f"Anthropic batch 제출 실패: HTTP {exc.code}; body={error_body[:1200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Anthropic batch 제출 실패: {exc}") from exc


def retrieve_anthropic_batch(batch_id: str, timeout: float) -> dict[str, Any]:
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
        raise RuntimeError(f"Anthropic batch 조회 실패: HTTP {exc.code}; body={error_body[:1200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Anthropic batch 조회 실패: {exc}") from exc


def fetch_anthropic_batch_results(results_url: str, timeout: float) -> list[dict[str, Any]]:
    """완료된 Anthropic Message Batch 결과 JSONL을 읽는다."""
    request = Request(results_url, headers=anthropic_request_headers(), method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Anthropic batch 결과 조회 실패: HTTP {exc.code}; body={error_body[:1200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Anthropic batch 결과 조회 실패: {exc}") from exc
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def call_model(args: argparse.Namespace, prompt: str) -> tuple[str, dict[str, int]]:
    """선택된 provider의 유료 모델 API를 호출한다."""
    if args.provider == "anthropic":
        return call_anthropic(
            prompt,
            args.anthropic_model,
            args.timeout,
            args.field_name,
            args.max_output_tokens,
        )
    return call_gemini(prompt, args.gemini_model, args.timeout)


def validate_output(
    payload: dict[str, Any],
    parsed: dict[str, Any],
    field_name: str,
    experiment_mode: str = "term_parentheses",
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
    difficulty_by_term = {
        item["term"]: str(item.get("difficulty_level") or "")
        for item in payload["용어후보"]
    }
    definition_text_by_id = {
        definition["definition_id"]: definition["source_definition"]
        for item in payload["용어후보"]
        for definition in item["definitions"]
    }
    term_decisions = parsed.get("term_decisions")
    if not isinstance(term_decisions, list):
        errors.append("term_decisions_not_list")
        term_decisions = []
    explained_count = 0
    seen_terms = set()
    for index, item in enumerate(term_decisions):
        if not isinstance(item, dict):
            errors.append(f"term_decision_not_object:{index}")
            continue
        term = str(item.get("term") or "")
        if term in seen_terms:
            warnings.append(f"duplicate_term_decision:{term}")
        seen_terms.add(term)
        selected_id = item.get("selected_definition_id")
        if term not in definitions_by_term:
            errors.append(f"unknown_term_decision:{index}:{term}")
            continue
        if selected_id is not None and str(selected_id) not in definitions_by_term[term]:
            errors.append(f"unknown_definition_id:{term}:{selected_id}")
        will_explain = item.get("will_explain")
        if not isinstance(will_explain, bool):
            errors.append(f"will_explain_not_bool:{term}")
            will_explain = False
        inserted_phrase = item.get("inserted_phrase")
        if will_explain:
            explained_count += 1
            if difficulty_by_term.get(term) == "easy":
                warnings.append(f"easy_term_explained:{term}")
            if selected_id is None:
                warnings.append(f"explain_without_definition:{term}")
            if not isinstance(inserted_phrase, str) or not inserted_phrase.strip():
                errors.append(f"empty_inserted_phrase:{term}")
                continue
            phrase = inserted_phrase.strip()
            occurrence = easy_summary.count(phrase)
            if occurrence != 1:
                warnings.append(f"inserted_phrase_occurrence:{term}:{occurrence}")
            if len(phrase) > 80:
                warnings.append(f"inserted_phrase_too_long:{term}:{len(phrase)}")
            if selected_id is not None:
                definition_text = definition_text_by_id.get(str(selected_id), "")
                if definition_text and len(definition_text) >= 20 and definition_text in phrase:
                    warnings.append(f"source_definition_copied:{term}")
        elif inserted_phrase not in (None, ""):
            warnings.append(f"inserted_phrase_for_unexplained_term:{term}")
        if not will_explain and contains_inline_explanation(easy_summary, term):
            warnings.append(f"unreported_inline_explanation:{term}")
        if experiment_mode == "db_natural" and contains_inline_explanation(easy_summary, term):
            warnings.append(f"parenthetical_explanation_in_natural_mode:{term}")
    missing_terms = set(definitions_by_term) - seen_terms
    for term in sorted(missing_terms):
        warnings.append(f"missing_term_decision:{term}")
    if experiment_mode == "term_parentheses" and explained_count > 4:
        warnings.append(f"too_many_explained_terms:{explained_count}")
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
    term_decisions = []
    for item in parsed.get("term_decisions", []):
        if not isinstance(item, dict):
            continue
        inserted_phrase = item.get("inserted_phrase")
        phrase = inserted_phrase.strip() if isinstance(inserted_phrase, str) else ""
        term_decisions.append(
            {
                **item,
                "used_in_easy_summary": bool(phrase and phrase in easy_summary),
                "inserted_phrase_occurrence": easy_summary.count(phrase) if phrase else 0,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "판례일련번호": payload["판례일련번호"],
        "사건명": payload["사건명"],
        "생성요약": payload["생성요약"],
        "term_candidates": payload["용어후보"],
        "term_decisions": term_decisions,
        "uncovered_difficult_terms": parsed.get("uncovered_difficult_terms", []),
        args.field_name: easy_summary,
        "provider": args.provider,
        "model": selected_model_name(args),
        "prompt_version": (
            NO_DB_NATURAL_PROMPT_VERSION
            if args.experiment_mode == "no_db_natural"
            else DB_NATURAL_PROMPT_VERSION
            if args.experiment_mode == "db_natural"
            else
            DIFFICULTY_PROMPT_VERSION
            if any("difficulty_level" in item for item in payload.get("용어후보", []))
            else PROMPT_VERSION
        ),
        "experiment_mode": args.experiment_mode,
        "token_usage": token_usage,
        "검증경고": validation_warnings,
        "final_case_updated": bool(args.write_final),
    }


def is_terminal_model_error(error: Exception) -> bool:
    """잔액·쿼터·결제처럼 같은 실행에서 계속 실패할 유료 모델 오류인지 판단한다."""
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
            "authentication_error",
            "invalid_api_key",
            "not_found_error",
        ]
    )


def pricing_for(provider: str, model: str) -> dict[str, float] | None:
    """provider와 모델명에 맞는 백만 토큰당 가격표를 반환한다."""
    if provider == "anthropic":
        return ANTHROPIC_PRICING_USD_PER_MILLION.get(model.strip())
    return GEMINI_PRICING_USD_PER_MILLION.get(normalize_gemini_model_name(model))


def estimate_cost_usd(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    """사용량과 가격표로 예상 비용을 계산한다."""
    pricing = pricing_for(provider, model)
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
    model_name = selected_model_name(args)
    estimated_cost_usd = estimate_cost_usd(
        args.provider,
        model_name,
        stats.input_token_count,
        stats.output_token_count,
    )
    pricing = pricing_for(args.provider, model_name)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "started_at": stats.started_at,
        "finished_at": now_utc_iso(),
        "provider": args.provider,
        "model": model_name,
        "prompt_version": prompt_version_for_rows(dry_run_input_rows),
        "experiment_mode": args.experiment_mode,
        "field_name": args.field_name,
        "max_output_tokens": args.max_output_tokens,
        "difficulty_overlay_path": str(args.difficulty_overlay_path)
        if args.difficulty_overlay_path
        else None,
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


def write_batch_manifest(
    paths: RunPaths,
    args: argparse.Namespace,
    stats: RunStats,
    case_ids: list[str],
    input_rows: list[dict[str, Any]],
    batch_payload: dict[str, Any],
    pricing_discount: float = 0.5,
) -> None:
    """Anthropic Batch 제출·회수 상태를 manifest로 저장한다."""
    model_name = selected_model_name(args)
    pricing = pricing_for(args.provider, model_name)
    discounted_pricing = (
        {
            "input": round(pricing["input"] * pricing_discount, 6),
            "output": round(pricing["output"] * pricing_discount, 6),
        }
        if pricing
        else None
    )
    estimated_cost_usd = None
    if discounted_pricing:
        estimated_cost_usd = round(
            stats.input_token_count / 1_000_000 * discounted_pricing["input"]
            + stats.output_token_count / 1_000_000 * discounted_pricing["output"],
            6,
        )
    term_counts = Counter(
        candidate["term"]
        for row in input_rows
        for candidate in row["term_candidates"]
    )
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "started_at": stats.started_at,
        "finished_at": now_utc_iso(),
        "provider": args.provider,
        "model": model_name,
        "execution_mode": "anthropic_message_batch",
        "batch_discount": pricing_discount,
        "prompt_version": prompt_version_for_rows(input_rows),
        "experiment_mode": args.experiment_mode,
        "field_name": args.field_name,
        "max_output_tokens": args.max_output_tokens,
        "difficulty_overlay_path": str(args.difficulty_overlay_path)
        if args.difficulty_overlay_path
        else None,
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
        "pricing_usd_per_million_tokens": discounted_pricing,
        "estimated_cost_usd": estimated_cost_usd,
        "stopped_reason": stats.stopped_reason,
        "batch": batch_payload,
        "term_candidate_counts": dict(term_counts.most_common()),
        "inputs_path": str(paths.inputs),
        "results_path": str(paths.results),
        "failures_path": str(paths.failures),
    }
    paths.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def collect_anthropic_batch_results(
    paths: RunPaths,
    args: argparse.Namespace,
    stats: RunStats,
    case_ids: list[str],
    input_rows: list[dict[str, Any]],
    batch_payload: dict[str, Any],
) -> None:
    """완료된 Anthropic Batch 결과를 검증하고 결과 파일로 저장한다."""
    results_url = batch_payload.get("results_url")
    if not results_url:
        stats.stopped_reason = f"batch_not_ready:{batch_payload.get('processing_status')}"
        return
    input_by_id = {str(row["판례일련번호"]): row for row in input_rows}
    reset_run_outputs(paths)
    for batch_row in fetch_anthropic_batch_results(str(results_url), args.timeout):
        case_id = str(batch_row.get("custom_id") or "")
        result = batch_row.get("result") or {}
        result_type = result.get("type")
        if result_type != "succeeded":
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
            raw_response = extract_anthropic_text(message, args.field_name)
            parsed = normalize_output_keys(parse_json_object(raw_response), args.field_name)
            input_row = input_by_id[case_id]
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
                args.experiment_mode,
            )
            if validation_errors:
                raise ValueError(f"출력 검증 실패: {validation_errors}")
            append_jsonl(
                paths.results,
                build_result_row(payload, parsed, token_usage, args, validation_warnings),
            )
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


def main() -> None:
    """용어 DB 후보 입력을 만들고, 선택 시 유료 LLM 쉬운요약 생성을 실행한다."""
    args = parse_args()
    model_name = selected_model_name(args)
    run_name = sanitize_run_name(
        args.run_name or f"term_db_{args.provider}_{model_name}_sample"
    )
    paths = build_run_paths(args.output_dir, run_name)
    case_ids = load_case_ids(args)
    term_db = load_term_db(args.term_db_path)
    difficulty_overlay = load_difficulty_overlay(args.difficulty_overlay_path)
    stats = RunStats(started_at=now_utc_iso(), total_targets=len(case_ids))

    input_rows = []
    for case_id in case_ids:
        case = load_final_case(args.final_cases_dir, case_id)
        summary = normalize_text(case.get("생성요약"))
        if not summary:
            raise ValueError(f"생성요약이 없는 판례입니다: {case_id}")
        term_candidates = detect_term_candidates(summary, term_db, args.max_terms)
        term_candidates = apply_difficulty_overlay(term_candidates, difficulty_overlay)
        payload = build_input_payload(
            case,
            term_candidates,
            include_terms=args.experiment_mode != "no_db_natural",
        )
        input_rows.append(
            {
                "판례일련번호": payload["판례일련번호"],
                "사건명": payload["사건명"],
                "생성요약": payload["생성요약"],
                "term_candidates": payload["용어후보"],
                "experiment_mode": args.experiment_mode,
                "prompt": build_prompt(payload, args.field_name, args.experiment_mode),
            }
        )
    write_jsonl(paths.inputs, input_rows)

    print(f"대상 판례: {len(case_ids)}건")
    print(f"프롬프트 입력 저장: {paths.inputs}")
    if args.dry_run:
        print("dry-run이므로 유료 LLM API를 호출하지 않습니다.")
        write_manifest(paths, args, stats, case_ids, input_rows)
        print(f"manifest 저장: {paths.manifest}")
        return

    if args.batch_submit or args.batch_collect:
        if args.provider != "anthropic":
            raise ValueError("batch-submit/batch-collect는 --provider anthropic에서만 사용할 수 있습니다.")
        if args.batch_submit:
            batch_payload = submit_anthropic_batch(
                input_rows,
                args.anthropic_model,
                args.timeout,
                args.max_output_tokens,
            )
            write_batch_manifest(paths, args, stats, case_ids, input_rows, batch_payload)
            print(f"batch 제출: {batch_payload.get('id')}")
            print(f"batch 상태: {batch_payload.get('processing_status')}")
            print(f"manifest 저장: {paths.manifest}")
            return
        if not args.batch_id:
            raise ValueError("--batch-collect에는 --batch-id가 필요합니다.")
        batch_payload = retrieve_anthropic_batch(args.batch_id, args.timeout)
        print(f"batch 상태: {batch_payload.get('processing_status')}")
        if batch_payload.get("processing_status") == "ended":
            collect_anthropic_batch_results(
                paths,
                args,
                stats,
                case_ids,
                input_rows,
                batch_payload,
            )
            print(f"batch 결과 저장: {paths.results}")
        else:
            stats.stopped_reason = f"batch_not_ready:{batch_payload.get('processing_status')}"
            print("아직 완료되지 않아 결과를 저장하지 않았습니다.")
        write_batch_manifest(paths, args, stats, case_ids, input_rows, batch_payload)
        print(f"manifest 저장: {paths.manifest}")
        return

    reset_run_outputs(paths)
    for index, input_row in enumerate(input_rows, start=1):
        case_id = str(input_row["판례일련번호"])
        raw_response = ""
        token_usage: dict[str, int] = {}
        try:
            raw_response, token_usage = call_model(args, str(input_row["prompt"]))
            stats.input_token_count += int(token_usage.get("input_tokens") or 0)
            stats.output_token_count += int(token_usage.get("output_tokens") or 0)
            stats.total_token_count += int(token_usage.get("total_tokens") or 0)
            parsed = normalize_output_keys(parse_json_object(raw_response), args.field_name)
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
                args.experiment_mode,
            )
            if validation_errors:
                raise ValueError(f"출력 검증 실패: {validation_errors}; raw={raw_response[:800]}")
            row = build_result_row(payload, parsed, token_usage, args, validation_warnings)
            append_jsonl(paths.results, row)
            stats.generated_count += 1
            if args.write_final:
                case = load_final_case(args.final_cases_dir, case_id)
                case[args.field_name] = row[args.field_name]
                case["쉬운요약_용어판단"] = row["term_decisions"]
                write_final_case(args.final_cases_dir, case_id, case)
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
                },
            )
            print(f"[{index}/{len(input_rows)}] 실패: {case_id} {exc}", flush=True)
            if is_terminal_model_error(exc):
                stats.stopped_reason = f"terminal_{args.provider}_error: {str(exc)[:500]}"
                break
        else:
            print(f"[{index}/{len(input_rows)}] 성공: {case_id}", flush=True)
            time.sleep(args.delay)

    write_manifest(paths, args, stats, case_ids, input_rows)
    print(
        f"완료: 생성 {stats.generated_count}건, 실패 {stats.failure_count}건, "
        f"예상 비용 "
        f"{estimate_cost_usd(args.provider, model_name, stats.input_token_count, stats.output_token_count)} USD"
    )


if __name__ == "__main__":
    main()
