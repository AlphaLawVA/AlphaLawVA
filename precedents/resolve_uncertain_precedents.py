#!/usr/bin/env python3
"""uncertain 판례를 이유까지 참고해서 related/unrelated로 최종 재분류한다.

이 파일은 기존 1차 분류에서 `uncertain`으로 남은 판례만 골라 Gemini에
다시 보낸다. 이번 단계에서는 `uncertain`을 허용하지 않고, AlphaLawVA
판례 검색/RAG에 사용할지 여부를 `related` 또는 `unrelated` 둘 중 하나로
분류한다. raw JSON과 전처리 JSON, 기존 1차 분류 결과는 수정하지 않는다.

Before:
    local_data/precedents/processed/classification_results.jsonl
    local_data/precedents/processed/basic_field_classification_results.jsonl
    - 1차 분류 결과이며, 일부 판례가 uncertain으로 남아 있다.

    local_data/precedents/processed/cases/{판례일련번호}.json
    - 주문, 청구취지, 판시사항, 판결요지, 이유 등이 분리된 전처리 판례다.

After:
    local_data/precedents/processed/uncertain_resolution_inputs.jsonl
    - Gemini에 전달한 입력 기록이다.

    local_data/precedents/processed/uncertain_resolution_results.jsonl
    - Gemini가 반환한 related/unrelated 재분류 결과다.

    local_data/precedents/processed/uncertain_resolution_failures.jsonl
    - 실패한 판례와 오류 내용을 저장한다.

    local_data/precedents/processed/uncertain_resolution_manifest.json
    - 실행 조건, 중단 사유, 처리 통계를 저장한다.
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
    GEMINI_GENERATE_URL_TEMPLATE,
    PROCESSED_CASES_DIR,
    PROCESSED_DIR,
    PROJECT_ROOT,
    ensure_collection_dirs,
    load_env_file,
    now_utc_iso,
)


UNCERTAIN_RESOLUTION_INPUTS_PATH = PROCESSED_DIR / "uncertain_resolution_inputs.jsonl"
UNCERTAIN_RESOLUTION_RESULTS_PATH = PROCESSED_DIR / "uncertain_resolution_results.jsonl"
UNCERTAIN_RESOLUTION_FAILURES_PATH = PROCESSED_DIR / "uncertain_resolution_failures.jsonl"
UNCERTAIN_RESOLUTION_MANIFEST_PATH = PROCESSED_DIR / "uncertain_resolution_manifest.json"

VALID_RELATED_VALUES = {"related", "unrelated"}
VALID_CONFIDENCE_VALUES = {"high", "medium", "low"}

REASON_HEADING_PATTERNS = [
    r"기초\s*사실",
    r"인정\s*사실",
    r"인정되는\s*사실",
    r"처분의\s*경위",
    r"공소\s*사실",
    r"범죄\s*사실",
    r"청구\s*원인",
    r"당사자(?:의)?\s*주장",
    r"원고(?:의)?\s*주장",
    r"피고(?:의)?\s*주장",
    r"항소\s*이유",
    r"상고\s*이유",
    r"판\s*단",
    r"살피건대",
]

RELEVANT_REASON_KEYWORDS = [
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
    "오피스텔",
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
]


@dataclass
class RunStats:
    """uncertain 재분류 실행 통계."""

    started_at: str
    total_candidates: int = 0
    skipped_existing_count: int = 0
    classified_count: int = 0
    failure_count: int = 0
    stopped_reason: str | None = None
    source_counts: Counter[str] = field(default_factory=Counter)
    classification_counts: Counter[str] = field(default_factory=Counter)
    confidence_counts: Counter[str] = field(default_factory=Counter)
    input_policy_counts: Counter[str] = field(default_factory=Counter)


def parse_args() -> argparse.Namespace:
    """커맨드라인 옵션을 정의한다."""
    load_env_file()
    parser = argparse.ArgumentParser(
        description="Resolve uncertain precedent classifications with Gemini."
    )
    parser.add_argument(
        "--gemini-model",
        default=os.environ.get("UNCERTAIN_RESOLUTION_GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        help="Gemini API에서 사용할 모델명.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="테스트용 처리 개수 제한.",
    )
    parser.add_argument(
        "--case-ids",
        default=None,
        help="쉼표로 구분한 판례일련번호만 처리한다.",
    )
    parser.add_argument(
        "--max-reason-chars",
        type=int,
        default=5000,
        help="이유 필드가 이 글자 수를 넘으면 핵심 구간 발췌로 줄인다.",
    )
    parser.add_argument(
        "--max-field-chars",
        type=int,
        default=2500,
        help="주문, 청구취지, 판시사항, 판결요지 각 필드 최대 입력 글자 수.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.05,
        help="Gemini 호출 사이 대기 시간.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=25,
        help="N건마다 진행 로그를 출력한다.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="이미 재분류된 판례도 다시 분류한다.",
    )
    return parser.parse_args()


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


def truncate_text(value: str, max_chars: int) -> tuple[str, bool]:
    """문자열을 최대 글자 수로 자르고 잘림 여부를 반환한다."""
    if len(value) <= max_chars:
        return value, False
    return value[:max_chars].rstrip(), True


def display_path(path: Path) -> str:
    """프로젝트 내부 파일은 상대경로로 저장한다."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def now_kst_text() -> str:
    """로그에서 보기 쉬운 현재 시각 문자열을 만든다."""
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


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


def load_uncertain_targets() -> list[dict[str, str]]:
    """1차 분류 결과에서 uncertain 판례 ID와 출처 파일을 모은다."""
    targets: list[dict[str, str]] = []
    seen: set[str] = set()
    sources = [
        ("issue_summary_classification", CLASSIFICATION_RESULTS_PATH),
        ("basic_field_classification", BASIC_FIELD_CLASSIFICATION_RESULTS_PATH),
    ]
    for source_name, path in sources:
        for row in iter_jsonl(path):
            if row.get("is_related") != "uncertain":
                continue
            precedent_id = str(row.get("precedent_id") or "").strip()
            if not precedent_id or precedent_id in seen:
                continue
            seen.add(precedent_id)
            targets.append({"precedent_id": precedent_id, "source": source_name})
    return targets


def load_processed_ids(path: Path) -> set[str]:
    """기존 JSONL 결과에서 이미 처리된 판례 ID를 읽는다."""
    processed_ids: set[str] = set()
    for row in iter_jsonl(path):
        precedent_id = row.get("precedent_id")
        if precedent_id:
            processed_ids.add(str(precedent_id))
    return processed_ids


def find_windows(text: str, patterns: list[str], before: int, after: int) -> list[tuple[int, int]]:
    """정규식/키워드가 나온 위치 주변의 발췌 구간을 찾는다."""
    windows = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            windows.append((max(0, match.start() - before), min(len(text), match.end() + after)))
    return windows


def merge_windows(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """겹치거나 맞닿은 발췌 구간을 하나로 합친다."""
    if not windows:
        return []
    merged = []
    for start, end in sorted(windows):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def excerpt_reason(reason: str, max_chars: int) -> tuple[str, dict[str, Any]]:
    """긴 이유는 앞부분, 핵심 키워드 주변, 뒷부분으로 줄여 입력 비용을 낮춘다."""
    if len(reason) <= max_chars:
        return reason, {
            "reason_policy": "full",
            "reason_original_chars": len(reason),
            "reason_sent_chars": len(reason),
            "reason_truncated": False,
            "reason_windows": [],
        }

    head_chars = min(1800, max_chars // 3)
    tail_chars = min(900, max_chars // 5)
    windows = [(0, head_chars), (max(0, len(reason) - tail_chars), len(reason))]
    windows.extend(find_windows(reason, REASON_HEADING_PATTERNS, before=500, after=1200))
    windows.extend(find_windows(reason, RELEVANT_REASON_KEYWORDS, before=450, after=850)[:8])

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


def build_case_text(case: dict[str, Any], args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    """Gemini에 넣을 판례 입력 텍스트와 입력 축약 정보를 만든다."""
    field_names = [
        "판례일련번호",
        "사건번호",
        "사건명",
        "법원명",
        "선고일자",
        "사건종류명",
        "판결유형",
        "주문",
        "청구취지",
        "원심판결",
        "판시사항",
        "판결요지",
        "참조조문",
        "참조판례",
    ]
    lines = []
    truncation: dict[str, Any] = {}
    for field_name in field_names:
        value = normalize_text(case.get(field_name))
        if not value:
            continue
        if field_name not in {"판례일련번호", "사건번호", "사건명", "법원명", "선고일자", "사건종류명", "판결유형"}:
            value, truncated = truncate_text(value, args.max_field_chars)
            truncation[f"{field_name}_truncated"] = truncated
        lines.append(f"{field_name}:\n{value}")

    reason, reason_truncation = excerpt_reason(normalize_text(case.get("이유")), args.max_reason_chars)
    if reason:
        lines.append(f"이유:\n{reason}")
    truncation.update(reason_truncation)
    return "\n\n".join(lines), truncation


def build_llm_input(target: dict[str, str], args: argparse.Namespace) -> dict[str, Any]:
    """전처리 판례와 uncertain 출처를 합쳐 Gemini 입력 구조로 바꾼다."""
    precedent_id = target["precedent_id"]
    case_path = PROCESSED_CASES_DIR / f"{precedent_id}.json"
    if not case_path.exists():
        raise FileNotFoundError(f"전처리 판례 파일을 찾지 못했습니다: {case_path}")

    case = read_json(case_path)
    classification_text, truncation = build_case_text(case, args)
    return {
        "schema_version": "precedent_uncertain_resolution_input.v1",
        "created_at": now_utc_iso(),
        "precedent_id": precedent_id,
        "source_uncertain_classification": target["source"],
        "source_processed_path": display_path(case_path),
        "source_detail_path": case.get("원본파일경로"),
        "metadata": {
            "사건명": normalize_text(case.get("사건명")),
            "사건번호": normalize_text(case.get("사건번호")),
            "법원명": normalize_text(case.get("법원명")),
            "선고일자": normalize_text(case.get("선고일자")),
            "사건종류명": normalize_text(case.get("사건종류명")),
            "판결유형": normalize_text(case.get("판결유형")),
            "이유_글자수": len(normalize_text(case.get("이유"))),
            "판시사항_존재": bool(normalize_text(case.get("판시사항"))),
            "판결요지_존재": bool(normalize_text(case.get("판결요지"))),
            "청구취지_존재": bool(normalize_text(case.get("청구취지"))),
        },
        "classification_basis": "uncertain_resolution_with_reason",
        "input_truncation": truncation,
        "classification_text": classification_text,
    }


def build_prompt(llm_input: dict[str, Any]) -> str:
    """이유까지 보는 uncertain 최종 재분류 프롬프트를 만든다."""
    return f"""
너는 AlphaLawVA의 판례 데이터 최종 선별 보조자다.

목표:
기존 1차 분류에서 uncertain으로 남은 판례를, 제공된 기본정보와 판례 이유를 보고
AlphaLawVA 판례 검색/RAG에 사용할지 여부를 related 또는 unrelated 둘 중 하나로 분류한다.

중요:
- 이번 단계에서는 uncertain을 출력하지 않는다.
- 반드시 related 또는 unrelated 중 하나를 선택한다.
- 판단이 애매하면, 사용자가 주거용 부동산 매매·전세·월세 분쟁을 검색할 때 실제로 참고할 가능성이 더 큰 쪽을 고른다.
- 다만 단순히 "부동산", "등기", "매매", "경매"라는 단어가 있다는 이유만으로 related로 분류하지 않는다.
- 판례의 핵심 쟁점과 사실관계가 AlphaLawVA 서비스 범위에 들어오는지 본다.
- 원문에 없는 사실, 사건번호, 법률효과, 주거용 여부를 지어내지 않는다.

AlphaLawVA 서비스 범위:
- 주거용 부동산 매매
- 전세
- 월세
- 임대차보증금 반환
- 건물인도/건물명도
- 차임/연체차임
- 임대차 종료
- 계약 해제/계약금 반환
- 소유권이전등기
- 근저당권, 전세권, 경매, 배당, 대항력, 우선변제권
- 공인중개사 책임
- 신탁등기, 가등기 등 주거용 거래 위험과 관련된 분쟁

related 기준:
다음 중 하나에 해당하면 related로 분류한다.
- 임대인/임차인, 임대차계약, 보증금, 전세금, 월세, 차임, 건물인도/명도 등이 핵심 쟁점인 경우
- 아파트, 주택, 다세대주택, 빌라, 오피스텔 등 주거용 또는 주거 가능 부동산 거래가 중심인 경우
- 부동산 매매계약, 계약금, 중도금, 잔금, 소유권이전등기, 매매계약 해제 등이 핵심 쟁점인 경우
- 경매/배당 사건이라도 임차인 보증금, 대항력, 우선변제권, 전세권, 근저당권 등 주거용 거래 위험 설명에 직접 도움이 되는 경우
- 주거용이라고 명시되지 않아도, 일반 개인의 부동산 매매·임대차 분쟁에 직접 참고할 수 있는 경우

unrelated 기준:
다음에 해당하면 unrelated로 분류한다.
- 사건의 핵심이 세금, 조세, 법인세, 증여세, 상속세 부과처분인 경우
- 회사, 주식, 증권, 합병, 주주, 이사 책임 등이 핵심인 경우
- 형사범죄 자체가 핵심이고 부동산은 범행 수단이나 배경으로만 등장하는 경우
- 상속, 이혼, 유류분, 종중, 교회/법인 재산 등 개인 주거 거래와 거리가 먼 재산 분쟁인 경우
- 농지, 임야, 토지수용, 재개발·재건축 행정처분, 공익사업 보상 등 일반 주거용 매매·임대차와 직접 관련이 약한 경우
- 단순 대여금, 구상금, 손해배상, 부당이득금 반환 사건이고 부동산 매매·임대차 쟁점이 핵심으로 드러나지 않는 경우

판단 방식:
1. 사건명과 사건종류명으로 대략적인 사건 분야를 확인한다.
2. 청구취지와 주문에서 무엇을 요구했는지 확인한다.
3. 이유에서 실제 분쟁의 사실관계와 법원의 판단 쟁점을 확인한다.
4. AlphaLawVA 사용자가 비슷한 상황을 입력했을 때 이 판례가 검색 결과로 나와도 유용한지 판단한다.
5. related/unrelated 중 더 타당한 하나를 고른다.

출력 규칙:
- 반드시 JSON 객체만 출력한다.
- is_related는 "related" 또는 "unrelated" 중 하나만 쓴다.
- confidence는 "high", "medium", "low" 중 하나만 쓴다.
- 판단이 애매했지만 둘 중 하나를 고른 경우 confidence는 medium 또는 low로 둔다.
- relevance_reason에는 이유 본문에서 어떤 점을 근거로 판단했는지 한 문장으로 쓴다.
- exclusion_reason은 unrelated이면 범위 밖 이유를 쓰고, related이면 null로 둔다.

출력 JSON 스키마:
{{
  "schema_version": "precedent_uncertain_resolution.v1",
  "precedent_id": "{llm_input["precedent_id"]}",
  "is_related": "related 또는 unrelated",
  "relevance_reason": "판단 이유",
  "exclusion_reason": "unrelated이면 범위 밖 이유, related이면 null",
  "confidence": "high | medium | low",
  "evidence_fields": ["사건명", "청구취지", "주문", "이유"],
  "warnings": []
}}

판례 입력:
{llm_input["classification_text"]}
""".strip()


def parse_json_object(text: str) -> dict[str, Any]:
    """LLM 응답에서 JSON 객체만 뽑아 파싱한다."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        start = stripped.find("{")
        if start == -1:
            raise
        try:
            parsed, _end = decoder.raw_decode(stripped[start:])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        end = stripped.rfind("}")
        if end == -1 or start >= end:
            raise
        return json.loads(stripped[start : end + 1])


def normalize_choice(value: Any, valid_values: set[str], default: str) -> str:
    """LLM 문자열 출력을 허용 enum 값 하나로 정리한다."""
    text = str(value or "").strip()
    if text in valid_values:
        return text
    for separator in ["|", "/", ",", " 또는 ", " or "]:
        if separator in text:
            first = text.split(separator, 1)[0].strip()
            if first in valid_values:
                return first
    for valid_value in valid_values:
        if valid_value in text:
            return valid_value
    return default


def validate_classification(
    classification: dict[str, Any],
    llm_input: dict[str, Any],
) -> dict[str, Any]:
    """LLM 결과를 저장 전에 검증하고 related/unrelated 이진값으로 보정한다."""
    warnings = classification.get("warnings")
    if not isinstance(warnings, list):
        warnings = []

    is_related = normalize_choice(classification.get("is_related"), VALID_RELATED_VALUES, "related")
    confidence = normalize_choice(classification.get("confidence"), VALID_CONFIDENCE_VALUES, "low")
    relevance_reason = normalize_text(classification.get("relevance_reason"))
    if not relevance_reason:
        raise ValueError("필수 필드 누락: relevance_reason")

    evidence_fields = classification.get("evidence_fields")
    if not isinstance(evidence_fields, list):
        evidence_fields = []

    if is_related == "unrelated":
        classification["exclusion_reason"] = classification.get("exclusion_reason") or relevance_reason
    elif not classification.get("exclusion_reason"):
        classification["exclusion_reason"] = None

    classification["schema_version"] = "precedent_uncertain_resolution.v1"
    classification["precedent_id"] = str(classification.get("precedent_id") or llm_input["precedent_id"])
    classification["is_related"] = is_related
    classification["relevance_reason"] = relevance_reason
    classification["confidence"] = confidence
    classification["evidence_fields"] = [normalize_text(field) for field in evidence_fields if normalize_text(field)]
    classification["warnings"] = warnings
    return classification


def get_gemini_api_key() -> str:
    """Gemini API 키를 환경변수에서 읽는다."""
    load_env_file()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(".env 또는 환경변수에 GEMINI_API_KEY를 설정해야 Gemini 분류를 실행할 수 있다.")
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


def call_gemini(llm_input: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """Gemini API에 재분류 프롬프트를 보내고 JSON 결과를 받는다."""
    api_key = get_gemini_api_key()
    model_name = normalize_gemini_model_name(args.gemini_model)
    endpoint = GEMINI_GENERATE_URL_TEMPLATE.format(model=model_name)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": build_prompt(llm_input)}]}],
        "generationConfig": {
            "temperature": 0,
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
        with urlopen(request, timeout=240) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini 호출 실패: HTTP {exc.code}; body={error_body[:1200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Gemini 호출 실패: {exc}") from exc

    response_payload = json.loads(body)
    raw_response = extract_gemini_text(response_payload)
    classification = validate_classification(parse_json_object(raw_response), llm_input)
    classification["model"] = model_name
    classification["provider"] = "gemini"
    return classification


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


def select_targets(args: argparse.Namespace, stats: RunStats) -> list[dict[str, str]]:
    """실행할 uncertain 대상 목록을 만든다."""
    targets = load_uncertain_targets()
    case_id_filter = None
    if args.case_ids:
        case_id_filter = {case_id.strip() for case_id in args.case_ids.split(",") if case_id.strip()}
        targets = [target for target in targets if target["precedent_id"] in case_id_filter]
    if args.limit is not None:
        targets = targets[: args.limit]
    for target in targets:
        stats.source_counts[target["source"]] += 1
    stats.total_candidates = len(targets)
    return targets


def build_progress_summary(index: int, total: int, stats: RunStats, started_monotonic: float) -> str:
    """현재 진행 상황과 예상 남은 시간을 문자열로 만든다."""
    elapsed = time.monotonic() - started_monotonic
    handled = stats.classified_count + stats.skipped_existing_count + stats.failure_count
    average = elapsed / handled if handled else 0
    remaining = max(0, total - index)
    return (
        f"진행 {index}/{total} "
        f"성공 {stats.classified_count}건 "
        f"스킵 {stats.skipped_existing_count}건 "
        f"실패 {stats.failure_count}건 "
        f"경과 {format_duration(elapsed)} "
        f"평균 {average:.1f}초/건 "
        f"예상남음 {format_duration(average * remaining if average else 0)}"
    )


def write_summary(stats: RunStats) -> None:
    """결과 JSONL을 다시 읽어 최종 통계를 보강한다."""
    stats.classification_counts.clear()
    stats.confidence_counts.clear()
    for row in iter_jsonl(UNCERTAIN_RESOLUTION_RESULTS_PATH):
        stats.classification_counts[str(row.get("is_related") or "")] += 1
        stats.confidence_counts[str(row.get("confidence") or "")] += 1


def write_manifest(stats: RunStats, args: argparse.Namespace) -> None:
    """실행 조건과 결과 통계를 manifest JSON으로 저장한다."""
    write_summary(stats)
    manifest = {
        "schema_version": "precedent_uncertain_resolution_manifest.v1",
        "started_at": stats.started_at,
        "finished_at": now_utc_iso(),
        "provider": "gemini",
        "model": args.gemini_model,
        "input_policy": {
            "source_results": [
                str(CLASSIFICATION_RESULTS_PATH),
                str(BASIC_FIELD_CLASSIFICATION_RESULTS_PATH),
            ],
            "include": "기존 1차 분류 결과에서 is_related == uncertain인 판례",
            "fields_sent_to_llm": [
                "기본정보",
                "주문",
                "청구취지",
                "원심판결",
                "판시사항",
                "판결요지",
                "참조조문",
                "참조판례",
                "이유",
            ],
            "max_reason_chars": args.max_reason_chars,
            "max_field_chars": args.max_field_chars,
            "labels": ["related", "unrelated"],
        },
        "paths": {
            "inputs": str(UNCERTAIN_RESOLUTION_INPUTS_PATH),
            "results": str(UNCERTAIN_RESOLUTION_RESULTS_PATH),
            "failures": str(UNCERTAIN_RESOLUTION_FAILURES_PATH),
        },
        "stats": {
            "total_candidates": stats.total_candidates,
            "skipped_existing_count": stats.skipped_existing_count,
            "classified_count": stats.classified_count,
            "failure_count": stats.failure_count,
            "stopped_reason": stats.stopped_reason,
            "source_counts": dict(stats.source_counts),
            "classification_counts": dict(stats.classification_counts),
            "confidence_counts": dict(stats.confidence_counts),
            "input_policy_counts": dict(stats.input_policy_counts),
        },
    }
    write_json(UNCERTAIN_RESOLUTION_MANIFEST_PATH, manifest)


def main() -> int:
    """uncertain 판례를 Gemini로 related/unrelated 이진 재분류한다."""
    args = parse_args()
    ensure_collection_dirs()
    stats = RunStats(started_at=now_utc_iso())
    targets = select_targets(args, stats)
    processed_ids = set() if args.overwrite else load_processed_ids(UNCERTAIN_RESOLUTION_RESULTS_PATH)
    UNCERTAIN_RESOLUTION_INPUTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNCERTAIN_RESOLUTION_INPUTS_PATH.touch(exist_ok=True)
    UNCERTAIN_RESOLUTION_RESULTS_PATH.touch(exist_ok=True)
    UNCERTAIN_RESOLUTION_FAILURES_PATH.touch(exist_ok=True)
    if args.overwrite:
        UNCERTAIN_RESOLUTION_RESULTS_PATH.write_text("", encoding="utf-8")
        UNCERTAIN_RESOLUTION_FAILURES_PATH.write_text("", encoding="utf-8")
        UNCERTAIN_RESOLUTION_INPUTS_PATH.write_text("", encoding="utf-8")

    print(
        "시작: "
        f"provider=gemini, "
        f"model={args.gemini_model}, "
        f"대상 {len(targets)}건, "
        f"max_reason_chars={args.max_reason_chars}, "
        f"limit={args.limit}",
        flush=True,
    )

    started_monotonic = time.monotonic()
    try:
        for index, target in enumerate(targets, start=1):
            precedent_id = target["precedent_id"]
            if precedent_id in processed_ids:
                stats.skipped_existing_count += 1
                continue

            item_started = time.monotonic()
            try:
                llm_input = build_llm_input(target, args)
                stats.input_policy_counts[llm_input["input_truncation"]["reason_policy"]] += 1
                append_jsonl(UNCERTAIN_RESOLUTION_INPUTS_PATH, llm_input)
                classification = call_gemini(llm_input, args)
                classification["classified_at"] = now_utc_iso()
                classification["metadata"] = llm_input["metadata"]
                classification["classification_basis"] = llm_input["classification_basis"]
                classification["source_uncertain_classification"] = llm_input["source_uncertain_classification"]
                classification["input_truncation"] = llm_input["input_truncation"]
                append_jsonl(UNCERTAIN_RESOLUTION_RESULTS_PATH, classification)
                stats.classified_count += 1
                stats.classification_counts[classification["is_related"]] += 1
                stats.confidence_counts[classification["confidence"]] += 1

                if args.progress_every > 0 and (
                    stats.classified_count % args.progress_every == 0 or index == len(targets)
                ):
                    elapsed = time.monotonic() - item_started
                    print(
                        f"[{now_kst_text()}] 재분류 저장: "
                        f"precedent_id={precedent_id} "
                        f"소요 {elapsed:.1f}초 "
                        f"related={classification.get('is_related')} "
                        f"confidence={classification.get('confidence')} "
                        f"{build_progress_summary(index, len(targets), stats, started_monotonic)}",
                        flush=True,
                    )
                time.sleep(args.delay)
            except Exception as exc:
                stats.failure_count += 1
                error_text = str(exc)
                append_jsonl(
                    UNCERTAIN_RESOLUTION_FAILURES_PATH,
                    {
                        "created_at": now_utc_iso(),
                        "precedent_id": precedent_id,
                        "source_uncertain_classification": target["source"],
                        "provider": "gemini",
                        "model": args.gemini_model,
                        "error": error_text,
                    },
                )
                print(
                    f"[{now_kst_text()}] 재분류 실패: "
                    f"precedent_id={precedent_id} error={exc} "
                    f"{build_progress_summary(index, len(targets), stats, started_monotonic)}",
                    flush=True,
                )
                if is_terminal_gemini_error(exc):
                    stats.stopped_reason = f"terminal_gemini_error: {error_text[:500]}"
                    write_manifest(stats, args)
                    print(
                        "Gemini 잔액/쿼터/결제 계열 오류로 중단합니다. "
                        "이미 저장된 결과는 유지되며, 같은 명령을 다시 실행하면 "
                        "저장된 판례 ID를 건너뛰고 이어서 시작합니다.",
                        flush=True,
                    )
                    return 2
    except KeyboardInterrupt:
        write_manifest(stats, args)
        print(
            "중단 요약: "
            f"대상 {len(targets)}건, "
            f"성공 {stats.classified_count}건, "
            f"스킵 {stats.skipped_existing_count}건, "
            f"실패 {stats.failure_count}건",
            flush=True,
        )
        return 130

    write_manifest(stats, args)
    print(
        "완료: "
        f"대상 {len(targets)}건, "
        f"성공 {stats.classified_count}건, "
        f"스킵 {stats.skipped_existing_count}건, "
        f"실패 {stats.failure_count}건",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
