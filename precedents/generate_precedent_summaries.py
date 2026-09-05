# generate_precedent_summaries.py
"""
Description: 최종 related 판례를 대상으로 검색 결과와 요약 청크에 사용할 생성요약을 만든다.
Gemini 또는 Ollama 호환 LLM에 판례 내용을 전달하고 결과를 모델별 JSONL 파일로 즉시 저장한다.
Author: choeminju
Date: 2026-09-01
Before:
    - local_data/precedents/processed/cases/와 related 분류 결과 JSONL이 있는 상태.
    - 테스트용 샘플 실행 시 summaries/summary_quality_sample_30.json이 있는 상태.

After:
    - local_data/precedents/processed/summaries/에 모델별 생성요약 결과, 입력, 실패, manifest 파일이 생성.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from precedent_config import (
    BASIC_FIELD_CLASSIFICATION_RESULTS_PATH,
    CLASSIFICATION_RESULTS_PATH,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_PRECEDENT_LLM_MODEL,
    GEMINI_GENERATE_URL_TEMPLATE,
    PROCESSED_CASES_DIR,
    PROCESSED_DIR,
    PROJECT_ROOT,
    ensure_collection_dirs,
    load_env_file,
    now_utc_iso,
)


SUMMARY_DIR = PROCESSED_DIR / "summaries"
DEFAULT_SAMPLE_FILE = SUMMARY_DIR / "summary_quality_sample_30.json"
UNCERTAIN_RESOLUTION_RESULTS_PATH = PROCESSED_DIR / "uncertain_resolution_results.jsonl"

SUMMARY_SCHEMA_VERSION = "precedent_generated_summary.v1"
MANIFEST_SCHEMA_VERSION = "precedent_generated_summary_manifest.v1"
MIN_SUMMARY_CHARS = 150
STANDARD_MAX_SUMMARY_CHARS = 230
EXTENDED_MAX_SUMMARY_CHARS = 300

REASON_HEADING_PATTERNS = [
    r"기초\s*사실",
    r"인정\s*사실",
    r"처분의\s*경위",
    r"청구\s*원인",
    r"당사자(?:의)?\s*주장",
    r"원고(?:의)?\s*주장",
    r"피고(?:의)?\s*주장",
    r"항소\s*이유",
    r"상고\s*이유",
    r"판\s*단",
    r"살피건대",
]

SUMMARY_FOCUS_KEYWORDS = [
    "임대차",
    "임차인",
    "임대인",
    "보증금",
    "전세",
    "월세",
    "차임",
    "건물명도",
    "건물인도",
    "주택",
    "아파트",
    "매매",
    "매도인",
    "매수인",
    "계약금",
    "중도금",
    "잔금",
    "소유권이전등기",
    "근저당",
    "전세권",
    "경매",
    "배당",
    "대항력",
    "우선변제",
    "중개",
    "손해배상",
    "부당이득",
]


@dataclass
class RunPaths:
    """생성요약 실행에서 사용하는 모델별 파일 경로."""

    inputs: Path
    results: Path
    failures: Path
    manifest: Path


@dataclass
class RunStats:
    """생성요약 실행 통계."""

    started_at: str
    total_targets: int = 0
    generated_count: int = 0
    skipped_existing_count: int = 0
    failure_count: int = 0
    stopped_reason: str | None = None
    input_policy_counts: Counter[str] = field(default_factory=Counter)
    warning_counts: Counter[str] = field(default_factory=Counter)


def parse_args() -> argparse.Namespace:
    """커맨드라인 옵션을 정의한다."""
    load_env_file()
    parser = argparse.ArgumentParser(
        description="Generate precedent summaries with Gemini or Ollama-compatible LLMs."
    )
    parser.add_argument(
        "--target",
        choices=["sample", "related", "case_ids"],
        default="sample",
        help="sample은 30건 품질 샘플, related는 최종 관련 판례 전체, case_ids는 지정 ID만 처리한다.",
    )
    parser.add_argument(
        "--provider",
        choices=["ollama", "gemini"],
        required=True,
        help="요약 생성에 사용할 provider.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("PRECEDENT_SUMMARY_LLM_MODEL", DEFAULT_PRECEDENT_LLM_MODEL),
        help="Ollama 호환 API에서 사용할 모델명. RunPod가 Ollama 호환이면 이 옵션을 그대로 사용한다.",
    )
    parser.add_argument(
        "--gemini-model",
        default=os.environ.get("PRECEDENT_SUMMARY_GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        help="Gemini API에서 사용할 모델명.",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
        help="Ollama base URL. RunPod에서 Ollama를 띄운 경우 RunPod의 Ollama URL을 넣는다.",
    )
    parser.add_argument(
        "--sample-file",
        type=Path,
        default=DEFAULT_SAMPLE_FILE,
        help="target=sample일 때 사용할 샘플 JSON 파일.",
    )
    parser.add_argument(
        "--case-ids",
        default=None,
        help="target=case_ids일 때 처리할 쉼표 구분 판례일련번호.",
    )
    parser.add_argument(
        "--case-ids-from-file",
        type=Path,
        default=None,
        help="판례일련번호 목록이 들어 있는 JSON/JSONL/텍스트 파일.",
    )
    parser.add_argument(
        "--run-name",
        default=None,
        help="결과 파일명에 붙일 실행 이름. 생략하면 provider와 model로 자동 생성한다.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="테스트용 처리 개수 제한.",
    )
    parser.add_argument(
        "--max-full-reason-chars",
        type=int,
        default=10000,
        help="이유가 이 글자 수 이하면 전문을 보내고, 초과하면 발췌본을 보낸다.",
    )
    parser.add_argument(
        "--max-excerpt-reason-chars",
        type=int,
        default=10000,
        help="긴 이유 발췌본의 최대 글자 수.",
    )
    parser.add_argument(
        "--max-field-chars",
        type=int,
        default=4000,
        help="판시사항, 판결요지, 주문, 청구취지 각 필드의 최대 입력 글자 수.",
    )
    parser.add_argument(
        "--ollama-num-ctx",
        type=int,
        default=int(os.environ.get("PRECEDENT_SUMMARY_OLLAMA_NUM_CTX", "8192")),
        help="Ollama context window 크기.",
    )
    parser.add_argument(
        "--ollama-num-predict",
        type=int,
        default=int(os.environ.get("PRECEDENT_SUMMARY_OLLAMA_NUM_PREDICT", "700")),
        help="Ollama가 생성할 최대 토큰 수.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.05,
        help="LLM 호출 사이 대기 시간.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=240,
        help="LLM HTTP 호출 타임아웃 초.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=5,
        help="N건마다 진행 로그를 출력한다.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="이미 저장된 요약도 다시 생성한다.",
    )
    return parser.parse_args()


def normalize_text(value: Any) -> str:
    """HTML 태그와 과도한 공백을 정리한 텍스트를 반환한다."""
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_summary_text(value: Any) -> str:
    """생성요약을 프론트 표시와 요약 청크에 쓰기 좋은 한 문단으로 정리한다."""
    text = normalize_text(value)
    return re.sub(r"\s+", " ", text).strip()


def truncate_text(value: str, max_chars: int) -> tuple[str, bool]:
    """문자열을 최대 글자 수로 자르고 잘림 여부를 반환한다."""
    if len(value) <= max_chars:
        return value, False
    return value[:max_chars].rstrip(), True


def read_json(path: Path) -> dict[str, Any]:
    """UTF-8 JSON 파일을 딕셔너리로 읽는다."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """딕셔너리를 보기 좋은 UTF-8 JSON 파일로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    """JSONL 파일에 한 줄짜리 JSON 객체를 추가한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 파일을 읽어 JSON 객체 목록으로 반환한다."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def display_path(path: Path) -> str:
    """프로젝트 내부 파일은 상대경로로 표시한다."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def format_duration(seconds: float) -> str:
    """초 단위 시간을 사람이 읽기 쉬운 문자열로 바꾼다."""
    seconds_int = max(0, int(seconds))
    hours, remainder = divmod(seconds_int, 3600)
    minutes, seconds_left = divmod(remainder, 60)
    if hours:
        return f"{hours}시간 {minutes}분 {seconds_left}초"
    if minutes:
        return f"{minutes}분 {seconds_left}초"
    return f"{seconds_left}초"


def now_kst_text() -> str:
    """로그에서 보기 쉬운 현재 시각 문자열을 만든다."""
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def sanitize_run_name(value: str) -> str:
    """모델명을 파일명에 안전한 문자열로 바꾼다."""
    return re.sub(r"[^A-Za-z0-9가-힣_.-]+", "-", value).strip("-")


def build_run_name(args: argparse.Namespace) -> str:
    """provider와 모델명으로 결과 파일 접두어를 만든다."""
    if args.run_name:
        return sanitize_run_name(args.run_name)
    model_name = args.gemini_model if args.provider == "gemini" else args.model
    target = args.target
    return sanitize_run_name(f"{target}_{args.provider}_{model_name}")


def build_run_paths(run_name: str) -> RunPaths:
    """실행 이름에 대응되는 입출력 파일 경로를 만든다."""
    return RunPaths(
        inputs=SUMMARY_DIR / f"{run_name}_inputs.jsonl",
        results=SUMMARY_DIR / f"{run_name}_results.jsonl",
        failures=SUMMARY_DIR / f"{run_name}_failures.jsonl",
        manifest=SUMMARY_DIR / f"{run_name}_manifest.json",
    )


def load_processed_ids(path: Path) -> set[str]:
    """기존 결과 파일에서 이미 처리된 판례 ID를 읽는다."""
    processed_ids: set[str] = set()
    for row in iter_jsonl(path):
        precedent_id = row.get("판례일련번호") or row.get("precedent_id")
        if precedent_id:
            processed_ids.add(str(precedent_id))
    return processed_ids


def load_related_ids() -> list[str]:
    """최종 related 분류 결과 3종을 합쳐 판례 ID 목록을 만든다."""
    sources = [
        CLASSIFICATION_RESULTS_PATH,
        BASIC_FIELD_CLASSIFICATION_RESULTS_PATH,
        UNCERTAIN_RESOLUTION_RESULTS_PATH,
    ]
    related_ids: list[str] = []
    seen: set[str] = set()
    for path in sources:
        for row in iter_jsonl(path):
            if row.get("is_related") != "related":
                continue
            precedent_id = str(row.get("precedent_id") or row.get("판례일련번호") or "").strip()
            if precedent_id and precedent_id not in seen:
                seen.add(precedent_id)
                related_ids.append(precedent_id)
    return related_ids


def load_ids_from_file(path: Path) -> list[str]:
    """JSON, JSONL, 텍스트 파일에서 판례 ID 목록을 읽는다."""
    if not path.exists():
        raise FileNotFoundError(f"판례 ID 파일을 찾지 못했습니다: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        payload = json.loads(text)
        if isinstance(payload, list):
            ids = []
            for item in payload:
                if isinstance(item, dict):
                    ids.append(str(item.get("판례일련번호") or item.get("precedent_id") or "").strip())
                else:
                    ids.append(str(item).strip())
            return [precedent_id for precedent_id in ids if precedent_id]
    if path.suffix == ".jsonl":
        return [
            str(row.get("판례일련번호") or row.get("precedent_id") or "").strip()
            for row in iter_jsonl(path)
            if str(row.get("판례일련번호") or row.get("precedent_id") or "").strip()
        ]
    return [line.strip() for line in text.splitlines() if line.strip()]


def select_target_ids(args: argparse.Namespace) -> list[str]:
    """실행 대상 판례 ID를 고른다."""
    if args.case_ids_from_file:
        ids = load_ids_from_file(args.case_ids_from_file)
    elif args.target == "sample":
        ids = load_ids_from_file(args.sample_file)
    elif args.target == "related":
        ids = load_related_ids()
    else:
        ids = [case_id.strip() for case_id in str(args.case_ids or "").split(",") if case_id.strip()]
        if not ids:
            raise ValueError("--target case_ids를 사용할 때는 --case-ids 또는 --case-ids-from-file이 필요합니다.")

    if args.limit is not None:
        ids = ids[: args.limit]
    return ids


def find_windows(text: str, patterns: list[str], before: int, after: int) -> list[tuple[int, int]]:
    """정규식이나 키워드가 나온 위치 주변의 발췌 구간을 찾는다."""
    windows = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            windows.append((max(0, match.start() - before), min(len(text), match.end() + after)))
    return windows


def merge_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """겹치거나 맞닿은 발췌 구간을 하나로 합친다."""
    if not windows:
        return []
    merged: list[list[int]] = []
    for start, end in sorted(windows):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def excerpt_reason(reason: str, max_chars: int) -> tuple[str, dict[str, Any]]:
    """긴 이유는 앞부분, 핵심 제목, 관련 키워드 주변, 뒷부분으로 줄인다."""
    head_chars = min(2200, max_chars // 3)
    tail_chars = min(1200, max_chars // 5)
    windows = [(0, head_chars), (max(0, len(reason) - tail_chars), len(reason))]
    windows.extend(find_windows(reason, REASON_HEADING_PATTERNS, before=600, after=1400))
    windows.extend(find_windows(reason, SUMMARY_FOCUS_KEYWORDS, before=500, after=900)[:10])

    chunks = []
    used_windows = []
    used_chars = 0
    for start, end in merge_windows(windows):
        if used_chars >= max_chars:
            break
        chunk = reason[start:end].strip()
        if not chunk:
            continue
        remaining = max_chars - used_chars
        if len(chunk) > remaining:
            chunk = chunk[:remaining].rstrip()
            end = start + len(chunk)
        chunks.append(f"[이유 발췌 {len(used_windows) + 1}: {start}~{end}]\n{chunk}")
        used_windows.append({"start": start, "end": end})
        used_chars += len(chunk)

    excerpt = "\n\n...\n\n".join(chunks).strip()
    return excerpt, {
        "reason_policy": "excerpt",
        "reason_original_chars": len(reason),
        "reason_sent_chars": len(excerpt),
        "reason_truncated": True,
        "reason_windows": used_windows,
    }


def prepare_reason(reason: str, args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    """이유 전문 또는 발췌본과 입력 정책 정보를 만든다."""
    if len(reason) <= args.max_full_reason_chars:
        return reason, {
            "reason_policy": "full",
            "reason_original_chars": len(reason),
            "reason_sent_chars": len(reason),
            "reason_truncated": False,
            "reason_windows": [],
        }
    return excerpt_reason(reason, args.max_excerpt_reason_chars)


def build_llm_input(case: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """요약 생성 LLM에 전달할 판례 입력을 만든다."""
    field_payload = {}
    truncation = {}
    for field_name in ["판시사항", "판결요지", "주문", "청구취지"]:
        text = normalize_text(case.get(field_name))
        truncated_text, was_truncated = truncate_text(text, args.max_field_chars)
        field_payload[field_name] = truncated_text
        truncation[f"{field_name}_truncated"] = was_truncated

    reason = normalize_text(case.get("이유"))
    reason_text, reason_policy = prepare_reason(reason, args)
    truncation.update(reason_policy)

    summary_input_text = "\n\n".join(
        [
            f"[판례일련번호]\n{normalize_text(case.get('판례일련번호'))}",
            f"[사건명]\n{normalize_text(case.get('사건명'))}",
            f"[판시사항]\n{field_payload['판시사항']}",
            f"[판결요지]\n{field_payload['판결요지']}",
            f"[주문]\n{field_payload['주문']}",
            f"[청구취지]\n{field_payload['청구취지']}",
            f"[이유]\n{reason_text}",
        ]
    )

    return {
        "schema_version": "precedent_summary_input.v1",
        "판례일련번호": normalize_text(case.get("판례일련번호")),
        "metadata": {
            "사건명": normalize_text(case.get("사건명")),
            "사건번호": normalize_text(case.get("사건번호")),
            "법원명": normalize_text(case.get("법원명")),
            "선고일자": normalize_text(case.get("선고일자")),
            "사건종류명": normalize_text(case.get("사건종류명")),
            "이유_글자수": len(reason),
            "판시사항_있음": bool(field_payload["판시사항"]),
            "판결요지_있음": bool(field_payload["판결요지"]),
            "청구취지_있음": bool(field_payload["청구취지"]),
        },
        "input_policy": truncation,
        "summary_input_text": summary_input_text,
    }


def build_model_specific_summary_rules(args: argparse.Namespace) -> str:
    """모델별로 필요한 추가 요약 규칙을 만든다."""
    model_name = (args.model if args.provider == "ollama" else args.gemini_model).lower()
    if "qwen" not in model_name:
        return ""
    return """
Qwen 계열 모델 추가 규칙:
- 이번 실행에서는 생성요약을 가능한 한 160~220자 사이로 맞춘다.
- 150자 미만 요약은 실패로 간주하므로 너무 짧게 끝내지 않는다.
- 230자를 넘기지 않도록 문장을 압축한다.
- 길이를 맞추기 위해 원문에 없는 내용을 새로 만들지 않는다.
""".strip()


def build_prompt(llm_input: dict[str, Any], args: argparse.Namespace) -> str:
    """판례 생성요약 프롬프트를 만든다."""
    model_specific_rules = build_model_specific_summary_rules(args)
    model_specific_block = f"\n\n{model_specific_rules}" if model_specific_rules else ""
    return f"""
너는 AlphaLawVA의 판례 요약 생성 보조자다.
목표는 제공된 판례 내용을 바탕으로 검색 결과 화면과 생성요약 청크에 사용할 짧은 요약을 만드는 것이다.
요약은 법률 전문가가 아닌 일반 사용자도 읽을 수 있도록 자연스럽고 명확한 설명문으로 작성한다.

요약 규칙:
- 생성요약은 기본적으로 {MIN_SUMMARY_CHARS}~{STANDARD_MAX_SUMMARY_CHARS}자 사이로 작성한다.
- 판례 내용이 길거나 핵심 쟁점이 여러 개라서 기본 길이로 담기 어려울 때만 최대 {EXTENDED_MAX_SUMMARY_CHARS}자까지 작성할 수 있다.
- 생성요약은 줄바꿈 없이 한 문단으로 작성한다.
- 전체 길이는 {EXTENDED_MAX_SUMMARY_CHARS}자를 넘기지 않는다.
- 모든 문장은 평서형 "~다"체로 끝낸다.
- "~입니다", "~했습니다", "~합니다" 체를 쓰지 않는다.
- 사건번호, 법원명, 선고일자는 요약문에 쓰지 않는다.
- "이 판례는"으로 시작하지 않는다.
- 핵심 법률용어는 원문 의미를 해치지 않는 범위에서 유지한다.
- 어려운 용어를 요약문 안에서 길게 풀어 설명하지 않는다.
- 너무 딱딱한 판결문 문체를 피하고 자연스러운 설명문으로 작성한다.
- 원문에 없는 사실관계, 법적 효과, 승패 결과를 지어내지 않는다.
- 요약은 사건의 사실관계, 핵심 분쟁, 법원의 판단 방향에 집중한다.
- 법률 조언이나 평가 표현을 쓰지 않는다.
- "중요한 판례", "의미 있는 판례", "시사하는 바가 크다" 같은 표현을 쓰지 않는다.
- 주문만 보고 승소/패소를 단정하지 않는다.
- 판결 결과가 명확하지 않으면 "책임 여부를 판단했다", "효력을 판단했다"처럼 중립적으로 쓴다.
{model_specific_block}

좋은 요약 예시:
임차인이 임대차계약 종료 후 보증금을 돌려받지 못해 임대인을 상대로 반환을 청구한 사건이다. 법원은 계약 종료 여부와 목적물 반환 관계를 중심으로 보증금 반환 책임을 판단했다.

좋지 않은 요약 예시:
이 판례는 임대차보증금 반환에 관한 중요한 판례입니다.
→ "이 판례는"으로 시작하고, 중요하다는 평가만 있을 뿐 사건 내용과 판단 방향이 부족하다.

좋지 않은 요약 예시:
2020다12345 사건에서 대법원은 임차인의 손을 들어주었다.
→ 사건번호와 법원명을 요약문에 넣었고, 원문 검토 없이 승패를 단정하는 표현이 위험하다.

좋지 않은 요약 예시:
임차인의 대항력, 즉 새 집주인에게도 임대차를 주장할 수 있는 힘이 문제 된 사건이다.
→ 요약문 안에서 법률용어를 길게 풀어 설명해 글자 수를 소모하고 있다.

출력 규칙:
- 반드시 JSON 객체만 출력한다.
- 생성요약 외의 설명 문장을 출력하지 않는다.
- 판례일련번호는 입력값과 동일하게 유지한다.

출력 JSON 스키마:
{{
  "판례일련번호": "{llm_input["판례일련번호"]}",
  "생성요약": "150~300자 요약문"
}}

판례 입력:
{llm_input["summary_input_text"]}
""".strip()


def build_retry_prompt(llm_input: dict[str, Any], warnings: list[str], args: argparse.Namespace) -> str:
    """검증 경고가 나온 요약을 한 번 더 고치게 하는 프롬프트를 만든다."""
    model_specific_rules = build_model_specific_summary_rules(args)
    model_specific_block = f"\n\n{model_specific_rules}" if model_specific_rules else ""
    return f"""
아래 판례 요약이 저장 기준을 지키지 못했다.
경고: {warnings}

다시 작성 규칙:
- 반드시 {MIN_SUMMARY_CHARS}~{EXTENDED_MAX_SUMMARY_CHARS}자 사이로 쓴다.
- 모든 문장은 평서형 "~다"체로 끝낸다.
- "~입니다", "~했습니다", "~합니다" 체를 쓰지 않는다.
- "이 판례는"으로 시작하지 않는다.
- 사건번호, 법원명, 선고일자는 쓰지 않는다.
- 어려운 법률용어를 요약문 안에서 길게 풀어 설명하지 않는다.
- 원문에 없는 사실이나 승패를 만들지 않는다.
- JSON 객체만 출력한다.
{model_specific_block}

출력 JSON 스키마:
{{
  "판례일련번호": "{llm_input["판례일련번호"]}",
  "생성요약": "수정된 요약문"
}}

판례 입력:
{llm_input["summary_input_text"]}
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
        raise RuntimeError(".env 또는 환경변수에 GEMINI_API_KEY를 설정해야 Gemini 요약 생성을 실행할 수 있다.")
    return api_key


def normalize_gemini_model_name(model: str) -> str:
    """Gemini REST URL에 넣을 모델명을 정리한다."""
    cleaned = model.strip()
    if cleaned.startswith("models/"):
        return cleaned.removeprefix("models/")
    return cleaned


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


def call_gemini(prompt: str, model: str, timeout: float) -> str:
    """Gemini API에 프롬프트를 보내고 원문 응답 텍스트를 받는다."""
    api_key = get_gemini_api_key()
    model_name = normalize_gemini_model_name(model)
    endpoint = GEMINI_GENERATE_URL_TEMPLATE.format(model=model_name)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
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

    return extract_gemini_text(json.loads(body))


def call_ollama(prompt: str, args: argparse.Namespace) -> str:
    """Ollama 호환 API에 프롬프트를 보내고 원문 응답 텍스트를 받는다."""
    endpoint = args.ollama_url.rstrip("/") + "/api/generate"
    payload = {
        "model": args.model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.2,
            "num_ctx": args.ollama_num_ctx,
            "num_predict": args.ollama_num_predict,
        },
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "curl/8.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=args.timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama 호출 실패: HTTP {exc.code}; body={error_body[:1200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Ollama 호출 실패: {exc}") from exc

    response_payload = json.loads(body)
    return str(response_payload.get("response") or "")


def is_terminal_gemini_error(error: Exception) -> bool:
    """잔액·쿼터·결제처럼 같은 실행에서 계속 실패할 Gemini 오류인지 판단한다."""
    text = str(error).lower()
    terminal_signals = [
        "resource_exhausted",
        "quota",
        "billing",
        "payment",
        "insufficient",
        "exceeded",
        "429",
        "403",
        "quota exceeded",
        "free tier",
    ]
    return any(signal in text for signal in terminal_signals)


def validate_summary(payload: dict[str, Any], llm_input: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """LLM 요약 결과를 저장 전 검증하고 경고를 만든다."""
    warnings = []
    precedent_id = str(payload.get("판례일련번호") or payload.get("precedent_id") or "").strip()
    expected_id = str(llm_input["판례일련번호"])
    if precedent_id != expected_id:
        warnings.append("판례일련번호가 입력과 달라서 입력값으로 보정함")
        precedent_id = expected_id

    summary = normalize_summary_text(payload.get("생성요약"))
    if not summary:
        raise ValueError("생성요약이 비어 있음")

    if summary.startswith("이 판례는"):
        warnings.append("생성요약이 금지된 표현 '이 판례는'으로 시작함")
    if re.search(r"(입니다|했습니다|합니다|됩니다|있습니다|없습니다|였습니다|하였습니다)", summary):
        warnings.append("생성요약에 '~입니다/합니다' 계열 문체가 포함됨")
    if len(summary) < MIN_SUMMARY_CHARS:
        warnings.append(f"생성요약이 너무 짧음: {len(summary)}자")
    if len(summary) > EXTENDED_MAX_SUMMARY_CHARS:
        warnings.append(f"생성요약이 너무 김: {len(summary)}자")
    if len(summary) > STANDARD_MAX_SUMMARY_CHARS:
        warnings.append(f"생성요약이 기본 권장 길이 {STANDARD_MAX_SUMMARY_CHARS}자를 초과함: {len(summary)}자")

    metadata = llm_input.get("metadata", {})
    for field_name in ["사건번호", "법원명", "선고일자"]:
        value = normalize_text(metadata.get(field_name))
        if value and value in summary:
            warnings.append(f"생성요약에 {field_name} 값이 포함됨")

    result = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "판례일련번호": precedent_id,
        "생성요약": summary,
        "요약글자수": len(summary),
    }
    return result, warnings


def generate_raw_response(llm_input: dict[str, Any], args: argparse.Namespace, prompt: str) -> str:
    """선택된 provider로 요약 생성 원문 응답을 받는다."""
    if args.provider == "gemini":
        return call_gemini(prompt, args.gemini_model, args.timeout)
    if args.provider == "ollama":
        return call_ollama(prompt, args)
    raise RuntimeError(f"지원하지 않는 provider입니다: {args.provider}")


def generate_summary(llm_input: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """판례 하나의 생성요약을 만들고 검증 경고를 붙인다."""
    prompt = build_prompt(llm_input, args)
    raw_response = generate_raw_response(llm_input, args, prompt)
    parsed = parse_json_object(raw_response)
    result, warnings = validate_summary(parsed, llm_input)

    retry_warnings = [
        warning
        for warning in warnings
        if warning.startswith("생성요약이 금지된 표현")
        or warning.startswith("생성요약에 '~입니다")
        or warning.startswith("생성요약이 너무")
        or "사건번호" in warning
        or "법원명" in warning
        or "선고일자" in warning
    ]
    if retry_warnings:
        retry_prompt = build_retry_prompt(llm_input, retry_warnings, args)
        retry_response = generate_raw_response(llm_input, args, retry_prompt)
        retry_parsed = parse_json_object(retry_response)
        result, warnings = validate_summary(retry_parsed, llm_input)
        warnings.append("검증 경고로 1회 재생성함")

    model_name = normalize_gemini_model_name(args.gemini_model) if args.provider == "gemini" else args.model
    result["요약생성모델"] = model_name
    result["요약생성제공자"] = args.provider
    result["요약생성일시"] = now_utc_iso()
    result["검증경고"] = warnings
    result["입력정책"] = {
        "reason_policy": llm_input["input_policy"]["reason_policy"],
        "reason_original_chars": llm_input["input_policy"]["reason_original_chars"],
        "reason_sent_chars": llm_input["input_policy"]["reason_sent_chars"],
    }
    return result


def build_progress_summary(index: int, total: int, stats: RunStats, started_monotonic: float) -> str:
    """현재 진행 상황과 예상 남은 시간을 문자열로 만든다."""
    elapsed = time.monotonic() - started_monotonic
    handled = stats.generated_count + stats.skipped_existing_count + stats.failure_count
    average = elapsed / handled if handled else 0
    remaining = max(0, total - index)
    return (
        f"진행 {index}/{total} "
        f"성공 {stats.generated_count}건 "
        f"스킵 {stats.skipped_existing_count}건 "
        f"실패 {stats.failure_count}건 "
        f"경과 {format_duration(elapsed)} "
        f"평균 {average:.1f}초/건 "
        f"예상남음 {format_duration(average * remaining if average else 0)}"
    )


def write_manifest(stats: RunStats, args: argparse.Namespace, paths: RunPaths, run_name: str) -> None:
    """실행 조건과 결과 통계를 manifest JSON으로 저장한다."""
    model_name = normalize_gemini_model_name(args.gemini_model) if args.provider == "gemini" else args.model
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "started_at": stats.started_at,
        "finished_at": now_utc_iso(),
        "run_name": run_name,
        "target": args.target,
        "provider": args.provider,
        "model": model_name,
        "input_policy": {
            "fields_sent_to_llm": ["판례일련번호", "사건명", "판시사항", "판결요지", "주문", "청구취지", "이유"],
            "fields_not_sent_to_llm": ["사건번호", "법원명", "선고일자"],
            "reason_full_if_lte_chars": args.max_full_reason_chars,
            "reason_excerpt_if_gt_chars": args.max_full_reason_chars,
            "max_excerpt_reason_chars": args.max_excerpt_reason_chars,
            "max_field_chars": args.max_field_chars,
            "summary_length_rule": {
                "default": f"{MIN_SUMMARY_CHARS}~{STANDARD_MAX_SUMMARY_CHARS}자",
                "extended": f"복잡한 판례만 최대 {EXTENDED_MAX_SUMMARY_CHARS}자",
            },
        },
        "paths": {
            "inputs": display_path(paths.inputs),
            "results": display_path(paths.results),
            "failures": display_path(paths.failures),
            "manifest": display_path(paths.manifest),
        },
        "stats": {
            "total_targets": stats.total_targets,
            "generated_count": stats.generated_count,
            "skipped_existing_count": stats.skipped_existing_count,
            "failure_count": stats.failure_count,
            "stopped_reason": stats.stopped_reason,
            "input_policy_counts": dict(stats.input_policy_counts),
            "warning_counts": dict(stats.warning_counts),
        },
    }
    write_json(paths.manifest, manifest)


def main() -> int:
    """선택한 provider로 판례 생성요약을 만든다."""
    args = parse_args()
    ensure_collection_dirs()
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)

    run_name = build_run_name(args)
    paths = build_run_paths(run_name)
    stats = RunStats(started_at=now_utc_iso())
    target_ids = select_target_ids(args)
    stats.total_targets = len(target_ids)

    processed_ids = set() if args.overwrite else load_processed_ids(paths.results)
    if args.overwrite:
        paths.inputs.write_text("", encoding="utf-8")
        paths.results.write_text("", encoding="utf-8")
        paths.failures.write_text("", encoding="utf-8")
    else:
        paths.inputs.touch(exist_ok=True)
        paths.results.touch(exist_ok=True)
        paths.failures.touch(exist_ok=True)

    model_name = normalize_gemini_model_name(args.gemini_model) if args.provider == "gemini" else args.model
    print(
        "요약 생성 시작: "
        f"target={args.target}, provider={args.provider}, model={model_name}, "
        f"대상 {len(target_ids)}건, run_name={run_name}",
        flush=True,
    )

    started_monotonic = time.monotonic()
    try:
        for index, precedent_id in enumerate(target_ids, start=1):
            if precedent_id in processed_ids:
                stats.skipped_existing_count += 1
                continue

            case_path = PROCESSED_CASES_DIR / f"{precedent_id}.json"
            item_started = time.monotonic()
            try:
                if not case_path.exists():
                    raise FileNotFoundError(f"전처리 판례 파일 없음: {case_path}")
                llm_input = build_llm_input(read_json(case_path), args)
                stats.input_policy_counts[llm_input["input_policy"]["reason_policy"]] += 1
                append_jsonl(paths.inputs, llm_input)

                result = generate_summary(llm_input, args)
                append_jsonl(paths.results, result)
                stats.generated_count += 1
                for warning in result.get("검증경고", []):
                    stats.warning_counts[warning] += 1

                if args.progress_every > 0 and (
                    stats.generated_count % args.progress_every == 0 or index == len(target_ids)
                ):
                    elapsed = time.monotonic() - item_started
                    print(
                        f"[{now_kst_text()}] 요약 저장: "
                        f"판례일련번호={precedent_id} "
                        f"소요 {elapsed:.1f}초 "
                        f"글자수={result.get('요약글자수')} "
                        f"{build_progress_summary(index, len(target_ids), stats, started_monotonic)}",
                        flush=True,
                    )
                time.sleep(args.delay)
            except Exception as exc:
                stats.failure_count += 1
                error_text = str(exc)
                append_jsonl(
                    paths.failures,
                    {
                        "created_at": now_utc_iso(),
                        "판례일련번호": precedent_id,
                        "provider": args.provider,
                        "model": model_name,
                        "error": error_text,
                    },
                )
                print(
                    f"[{now_kst_text()}] 요약 실패: "
                    f"판례일련번호={precedent_id} error={exc} "
                    f"{build_progress_summary(index, len(target_ids), stats, started_monotonic)}",
                    flush=True,
                )
                if args.provider == "gemini" and is_terminal_gemini_error(exc):
                    stats.stopped_reason = f"terminal_gemini_error: {error_text[:500]}"
                    write_manifest(stats, args, paths, run_name)
                    print(
                        "Gemini 잔액/쿼터/결제 계열 오류로 중단합니다. "
                        "이미 저장된 결과는 유지되며, 같은 명령을 다시 실행하면 이어서 시작합니다.",
                        flush=True,
                    )
                    return 2
    except KeyboardInterrupt:
        stats.stopped_reason = "keyboard_interrupt"
        write_manifest(stats, args, paths, run_name)
        print(
            "중단 요약: "
            f"성공 {stats.generated_count}건, "
            f"스킵 {stats.skipped_existing_count}건, "
            f"실패 {stats.failure_count}건",
            flush=True,
        )
        return 130

    write_manifest(stats, args, paths, run_name)
    print(
        f"완료: manifest={paths.manifest}\n"
        f"요약: 대상 {stats.total_targets}건, "
        f"성공 {stats.generated_count}건, "
        f"스킵 {stats.skipped_existing_count}건, "
        f"실패 {stats.failure_count}건",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
