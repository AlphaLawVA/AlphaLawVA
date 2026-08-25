#!/usr/bin/env python3
"""판시사항/판결요지가 없는 판례를 기본정보·주문·청구취지만으로 1차 분류한다.

이 파일은 판시사항과 판결요지가 모두 없는 전처리 판례 중에서, `청구취지`가
있는 판례만 골라 로컬 LLM 또는 Gemini로 1차 관련성 분류를 실행한다.
기존 raw JSON, 전처리 JSON, 판시사항/판결요지 기반 분류 결과는 수정하지 않는다.

Before:
    local_data/precedents/processed/cases/{판례일련번호}.json
    - 주문, 청구취지, 이유, 판시사항, 판결요지 등이 분리된 전처리 판례다.

After:
    local_data/precedents/processed/basic_field_classification_inputs.jsonl
    - LLM에 전달한 기본정보·주문·청구취지 입력 기록이다.
    local_data/precedents/processed/basic_field_classification_results.jsonl
    - 로컬 LLM 또는 Gemini가 반환한 1차 분류 결과다.
    local_data/precedents/processed/basic_field_classification_failures.jsonl
    - 실패한 판례와 오류 내용을 저장한다.
    local_data/precedents/processed/basic_field_classification_manifest.json
    - 실행 조건과 처리 통계를 저장한다.
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
    BASIC_FIELD_CLASSIFICATION_FAILURES_PATH,
    BASIC_FIELD_CLASSIFICATION_INPUTS_PATH,
    BASIC_FIELD_CLASSIFICATION_MANIFEST_PATH,
    BASIC_FIELD_CLASSIFICATION_RESULTS_PATH,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_PRECEDENT_LLM_MODEL,
    GEMINI_GENERATE_URL_TEMPLATE,
    PROCESSED_CASES_DIR,
    PROJECT_ROOT,
    ensure_collection_dirs,
    load_env_file,
    now_utc_iso,
)
from prepare_classification_cases import diagnose_keywords


VALID_RELATED_VALUES = {"related", "unrelated", "uncertain"}
VALID_CONFIDENCE_VALUES = {"high", "medium", "low"}


@dataclass
class RunStats:
    """기본정보 기반 1차 분류 실행 통계."""

    started_at: str
    total_candidates: int = 0
    skipped_existing_count: int = 0
    classified_count: int = 0
    failure_count: int = 0
    stopped_reason: str | None = None
    excluded_counts: Counter[str] = field(default_factory=Counter)
    provider_counts: Counter[str] = field(default_factory=Counter)


def parse_args() -> argparse.Namespace:
    """커맨드라인 옵션을 정의한다."""
    load_env_file()
    parser = argparse.ArgumentParser(
        description="Classify precedents without issue/summary using basic fields only."
    )
    parser.add_argument(
        "--provider",
        choices=["ollama", "gemini"],
        default=os.environ.get("BASIC_FIELD_CLASSIFICATION_PROVIDER", "ollama"),
        help="기본정보 1차 분류에 사용할 LLM provider. 비용 절감을 위해 기본값은 ollama다.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("BASIC_FIELD_LLM_MODEL", "llama3.1:8b"),
        help="Ollama에서 사용할 로컬 LLM 모델명.",
    )
    parser.add_argument(
        "--gemini-model",
        default=os.environ.get("BASIC_FIELD_GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        help="Gemini API에서 사용할 모델명.",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
        help="Ollama base URL.",
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
        "--max-order-chars",
        type=int,
        default=1000,
        help="주문 필드 최대 입력 글자 수.",
    )
    parser.add_argument(
        "--max-claim-chars",
        type=int,
        default=2500,
        help="청구취지 필드 최대 입력 글자 수.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.05,
        help="LLM 호출 사이 대기 시간.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="N건마다 진행 로그를 출력한다.",
    )
    parser.add_argument(
        "--ollama-num-ctx",
        type=int,
        default=int(os.environ.get("BASIC_FIELD_OLLAMA_NUM_CTX", "4096")),
        help="Ollama context window 크기.",
    )
    parser.add_argument(
        "--ollama-num-predict",
        type=int,
        default=int(os.environ.get("BASIC_FIELD_OLLAMA_NUM_PREDICT", "700")),
        help="Ollama가 생성할 최대 토큰 수.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="이미 분류된 판례도 다시 분류한다.",
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


def has_text(value: Any) -> bool:
    """값이 비어 있지 않은 텍스트인지 확인한다."""
    return bool(normalize_text(value))


def keyword_text_for_case(case: dict[str, Any]) -> str:
    """비관련 키워드 진단용 텍스트를 만든다."""
    return "\n".join(
        normalize_text(case.get(field))
        for field in ["사건명", "사건번호", "사건종류명", "주문", "청구취지", "이유"]
        if normalize_text(case.get(field))
    )


def is_low_substance_reason(reason: str) -> bool:
    """RAG 근거로 쓸 실질 판단 내용이 거의 없는 짧은 이유인지 판별한다."""
    return any(
        [
            len(reason) <= 1200
            and bool(re.search(r"(상고장|항소장).*(각하|보정|인지대|송달료|기간 내|기간내)", reason)),
            len(reason) <= 1200
            and bool(re.search(r"(판결문|판결).*명백한 오류|판결선고일.*경정|직권으로 주문과 같이 결정", reason)),
            len(reason) <= 1500
            and bool(re.search(r"청구의 표시|별지 청구원인|무변론 판결|자백간주", reason)),
            len(reason) <= 1200
            and bool(re.search(r"(제1심|원심).*이유.*(그대로 )?인용|민사소송법 제420조", reason)),
        ]
    )


def should_include_case(case: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """이번 기본정보 1차 분류 대상인지 판단한다."""
    if has_text(case.get("판시사항")) or has_text(case.get("판결요지")):
        return False, "has_issue_or_summary", {}
    if not has_text(case.get("이유")):
        return False, "missing_reason", {}
    if not has_text(case.get("청구취지")):
        return False, "missing_claim", {}

    keyword_diagnosis = diagnose_keywords(keyword_text_for_case(case))
    if keyword_diagnosis.get("label") == "strong_unrelated_signal":
        return False, "strong_unrelated_signal", keyword_diagnosis
    if is_low_substance_reason(normalize_text(case.get("이유"))):
        return False, "low_substance_reason", keyword_diagnosis
    return True, "included", keyword_diagnosis


def build_basic_field_text(
    case: dict[str, Any],
    max_order_chars: int,
    max_claim_chars: int,
) -> tuple[str, dict[str, bool]]:
    """LLM에 넣을 기본정보·주문·청구취지 텍스트를 만든다."""
    order, order_truncated = truncate_text(normalize_text(case.get("주문")), max_order_chars)
    claim, claim_truncated = truncate_text(normalize_text(case.get("청구취지")), max_claim_chars)
    fields = [
        ("판례일련번호", case.get("판례일련번호")),
        ("사건번호", case.get("사건번호")),
        ("사건명", case.get("사건명")),
        ("법원명", case.get("법원명")),
        ("선고일자", case.get("선고일자")),
        ("사건종류명", case.get("사건종류명")),
        ("판결유형", case.get("판결유형")),
        ("주문", order),
        ("청구취지", claim),
    ]
    lines = []
    for label, value in fields:
        text = normalize_text(value)
        if text:
            lines.append(f"{label}:\n{text}")
    return "\n\n".join(lines), {
        "order_truncated": order_truncated,
        "claim_truncated": claim_truncated,
    }


def build_llm_input(case_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    """전처리 판례 하나를 기본정보 기반 LLM 입력 구조로 바꾼다."""
    case = read_json(case_path)
    include, exclusion_reason, keyword_diagnosis = should_include_case(case)
    if not include:
        raise ValueError(f"분류 대상이 아닙니다: {exclusion_reason}")

    basic_field_text, truncation = build_basic_field_text(
        case,
        args.max_order_chars,
        args.max_claim_chars,
    )
    precedent_id = str(case.get("판례일련번호") or case_path.stem)
    return {
        "schema_version": "precedent_basic_field_llm_input.v1",
        "created_at": now_utc_iso(),
        "precedent_id": precedent_id,
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
            "청구취지_글자수": len(normalize_text(case.get("청구취지"))),
            "주문_글자수": len(normalize_text(case.get("주문"))),
        },
        "classification_basis": "basic_fields_only",
        "keyword_diagnosis": keyword_diagnosis,
        "input_truncation": truncation,
        "classification_text": basic_field_text,
    }


def build_prompt(llm_input: dict[str, Any]) -> str:
    """기본정보·주문·청구취지만 보는 1차 선별 프롬프트를 만든다."""
    return f"""
너는 AlphaLawVA의 판례 데이터 1차 선별 보조자다.
목표는 판시사항과 판결요지가 없는 판례를 기본정보, 주문, 청구취지만 보고 1차로 나누는 것이다.

중요한 한계:
- 너에게는 이유 전문이 제공되지 않는다.
- 이 단계는 최종 관련 판정이 아니라, 비용 절감을 위한 1차 선별이다.
- related를 남발하지 말고, 근거가 부족하면 uncertain을 선택한다.
- unrelated는 AlphaLawVA 주거용 부동산 매매·전세·월세 분쟁과 명백히 먼 경우에만 선택한다.
- 금전 지급 청구액이 크다는 이유만으로 부동산 거래 맥락이라고 추정하지 않는다.
- 청구취지가 단순 금전 지급만 말하고 부동산·임대차·매매 목적물이 보이지 않으면 related로 두지 않는다.
- 사건종류명이나 사건명이 세무, 조세, 형사범죄, 회사·주식, 노동, 특허, 의료, 가사 사건을 명확히 가리키면 특별한 부동산 매매·임대차 청구취지가 없는 한 unrelated로 둔다.

판단 기준:
- related: 기본정보/청구취지/주문만으로도 주거용 또는 일반 부동산 매매·임대차 분쟁에 직접 쓸 가능성이 높은 경우.
  예: 임대차보증금, 전세금 반환, 월세·차임, 건물인도/명도, 주택 인도, 부동산 매매대금, 계약금 반환, 소유권이전등기 청구가 주택 거래 맥락으로 보이는 경우.
- unrelated: 사건의 핵심이 형사범죄, 세금, 회사·주식, 특허, 노동, 의료, 가사, 선거, 국가보안, 군사 등으로 명백히 AlphaLawVA 범위 밖인 경우.
- unrelated 예: 증여세부과처분취소, 법인세부과처분취소, 조세범처벌법위반, 주식매매대금, 특정경제범죄가중처벌, 배임·횡령, 특허침해, 부당해고.
- uncertain: 부동산 단어는 있지만 주거용 매매·임대차 분쟁인지 기본정보만으로 확정하기 어려운 경우.
  예: 소유권이전등기, 배당이의, 매매대금, 손해배상처럼 관련/비관련이 섞이는 사건명인데 청구취지만으로 맥락이 불충분한 경우.

출력 규칙:
- 반드시 JSON 객체만 출력한다.
- is_related와 confidence는 허용값 중 하나만 쓴다.
- needs_human_review는 uncertain 또는 low confidence이면 true다.
- relevance_reason에는 어떤 필드 때문에 판단했는지 한 문장으로 쓴다.
- 이유 전문, 판시사항, 판결요지, 법령 효과를 추측하지 않는다.

출력 JSON 스키마:
{{
  "schema_version": "precedent_basic_field_classification.v1",
  "precedent_id": "{llm_input["precedent_id"]}",
  "is_related": "related 또는 unrelated 또는 uncertain 중 하나",
  "relevance_reason": "판단 이유",
  "exclusion_reason": "unrelated이면 범위 밖 이유, 아니면 null",
  "confidence": "high | medium | low",
  "needs_human_review": true,
  "evidence_fields": ["사건명", "주문", "청구취지"],
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


def validate_classification(classification: dict[str, Any], llm_input: dict[str, Any]) -> dict[str, Any]:
    """LLM 결과를 저장 전에 보수적으로 검증하고 보정한다."""
    warnings = classification.get("warnings")
    if not isinstance(warnings, list):
        warnings = []

    is_related = normalize_choice(classification.get("is_related"), VALID_RELATED_VALUES, "uncertain")
    confidence = normalize_choice(classification.get("confidence"), VALID_CONFIDENCE_VALUES, "low")
    relevance_reason = normalize_text(classification.get("relevance_reason"))
    if not relevance_reason:
        raise ValueError("필수 필드 누락: relevance_reason")

    needs_human_review = classification.get("needs_human_review")
    if not isinstance(needs_human_review, bool):
        needs_human_review = is_related == "uncertain" or confidence == "low"
        warnings.append("needs_human_review가 bool이 아니어서 규칙으로 보정함")
    if is_related == "uncertain" or confidence == "low":
        needs_human_review = True

    evidence_fields = classification.get("evidence_fields")
    if not isinstance(evidence_fields, list):
        evidence_fields = []

    if is_related == "unrelated":
        classification["exclusion_reason"] = classification.get("exclusion_reason") or relevance_reason
    elif not classification.get("exclusion_reason"):
        classification["exclusion_reason"] = None

    classification["schema_version"] = "precedent_basic_field_classification.v1"
    classification["precedent_id"] = str(classification.get("precedent_id") or llm_input["precedent_id"])
    classification["is_related"] = is_related
    classification["relevance_reason"] = relevance_reason
    classification["confidence"] = confidence
    classification["needs_human_review"] = needs_human_review
    classification["evidence_fields"] = [normalize_text(field) for field in evidence_fields if normalize_text(field)]
    classification["warnings"] = warnings
    return classification


def load_processed_ids(path: Path) -> set[str]:
    """기존 JSONL 결과에서 이미 처리된 판례 ID를 읽는다."""
    processed_ids: set[str] = set()
    if not path.exists():
        return processed_ids
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        precedent_id = item.get("precedent_id")
        if precedent_id:
            processed_ids.add(str(precedent_id))
    return processed_ids


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


def call_ollama(llm_input: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """Ollama 로컬 LLM에 기본정보 분류 프롬프트를 보내고 JSON 결과를 받는다."""
    endpoint = args.ollama_url.rstrip("/") + "/api/generate"
    payload = {
        "model": args.model,
        "prompt": build_prompt(llm_input),
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "num_ctx": args.ollama_num_ctx,
            "num_predict": args.ollama_num_predict,
        },
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=180) as response:
            body = response.read().decode("utf-8")
    except URLError as exc:
        raise RuntimeError(f"Ollama 호출 실패: {exc}") from exc

    response_payload = json.loads(body)
    raw_response = response_payload.get("response", "")
    classification = validate_classification(parse_json_object(raw_response), llm_input)
    classification["model"] = args.model
    classification["provider"] = "ollama"
    return classification


def call_gemini(llm_input: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """Gemini API에 기본정보 분류 프롬프트를 보내고 JSON 결과를 받는다."""
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
        with urlopen(request, timeout=180) as response:
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


def classify_input(llm_input: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """설정된 provider로 판례 하나를 분류한다."""
    if args.provider == "ollama":
        return call_ollama(llm_input, args)
    return call_gemini(llm_input, args)


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


def iter_processed_case_paths() -> list[Path]:
    """전처리 판례 JSON 경로를 판례일련번호 순서로 반환한다."""
    return sorted(
        PROCESSED_CASES_DIR.glob("*.json"),
        key=lambda path: int(path.stem) if path.stem.isdigit() else path.stem,
    )


def select_case_paths(args: argparse.Namespace, stats: RunStats) -> list[Path]:
    """이번 1차 분류 대상 경로를 고른다."""
    all_paths = iter_processed_case_paths()
    case_id_filter = None
    if args.case_ids:
        case_id_filter = {case_id.strip() for case_id in args.case_ids.split(",") if case_id.strip()}

    selected = []
    for path in all_paths:
        if case_id_filter is not None and path.stem not in case_id_filter:
            continue
        case = read_json(path)
        include, reason, _keyword_diagnosis = should_include_case(case)
        if include:
            selected.append(path)
        else:
            stats.excluded_counts[reason] += 1

    if case_id_filter is not None:
        found = {path.stem for path in selected}
        missing = sorted(case_id_filter - found)
        if missing:
            print(f"주의: 지정했지만 이번 대상이 아닌 판례 ID {len(missing)}건: {', '.join(missing[:20])}")

    if args.limit is not None:
        selected = selected[: args.limit]
    stats.total_candidates = len(selected)
    return selected


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


def write_manifest(stats: RunStats, args: argparse.Namespace) -> None:
    """실행 조건과 결과 통계를 manifest JSON으로 저장한다."""
    manifest = {
        "schema_version": "precedent_basic_field_classification_manifest.v1",
        "started_at": stats.started_at,
        "finished_at": now_utc_iso(),
        "provider": args.provider,
        "model": args.model if args.provider == "ollama" else args.gemini_model,
        "input_policy": {
            "source": "processed/cases",
            "include": [
                "판시사항 없음",
                "판결요지 없음",
                "이유 있음",
                "청구취지 있음",
                "strong_unrelated_signal 아님",
                "low_substance_reason 아님",
            ],
            "fields_sent_to_llm": ["기본정보", "주문", "청구취지"],
            "max_order_chars": args.max_order_chars,
            "max_claim_chars": args.max_claim_chars,
        },
        "paths": {
            "inputs": str(BASIC_FIELD_CLASSIFICATION_INPUTS_PATH),
            "results": str(BASIC_FIELD_CLASSIFICATION_RESULTS_PATH),
            "failures": str(BASIC_FIELD_CLASSIFICATION_FAILURES_PATH),
        },
        "stats": {
            "total_candidates": stats.total_candidates,
            "skipped_existing_count": stats.skipped_existing_count,
            "classified_count": stats.classified_count,
            "failure_count": stats.failure_count,
            "stopped_reason": stats.stopped_reason,
            "excluded_counts": dict(stats.excluded_counts),
            "provider_counts": dict(stats.provider_counts),
        },
    }
    write_json(BASIC_FIELD_CLASSIFICATION_MANIFEST_PATH, manifest)


def main() -> int:
    """기본정보 기반 1차 LLM 분류를 실행한다."""
    args = parse_args()
    ensure_collection_dirs()
    stats = RunStats(started_at=now_utc_iso())
    case_paths = select_case_paths(args, stats)
    processed_ids = set() if args.overwrite else load_processed_ids(BASIC_FIELD_CLASSIFICATION_RESULTS_PATH)
    if args.overwrite:
        BASIC_FIELD_CLASSIFICATION_RESULTS_PATH.write_text("", encoding="utf-8")
        BASIC_FIELD_CLASSIFICATION_FAILURES_PATH.write_text("", encoding="utf-8")
        BASIC_FIELD_CLASSIFICATION_INPUTS_PATH.write_text("", encoding="utf-8")

    print(
        "시작: "
        f"provider={args.provider}, "
        f"model={args.model if args.provider == 'ollama' else args.gemini_model}, "
        f"대상 {len(case_paths)}건, "
        f"limit={args.limit}",
        flush=True,
    )

    started_monotonic = time.monotonic()
    try:
        for index, case_path in enumerate(case_paths, start=1):
            llm_input = build_llm_input(case_path, args)
            precedent_id = llm_input["precedent_id"]
            if precedent_id in processed_ids:
                stats.skipped_existing_count += 1
                continue

            append_jsonl(BASIC_FIELD_CLASSIFICATION_INPUTS_PATH, llm_input)
            item_started = time.monotonic()
            try:
                classification = classify_input(llm_input, args)
                classification["classified_at"] = now_utc_iso()
                classification["metadata"] = llm_input["metadata"]
                classification["classification_basis"] = llm_input["classification_basis"]
                classification["input_truncation"] = llm_input["input_truncation"]
                classification["keyword_diagnosis"] = llm_input["keyword_diagnosis"]
                append_jsonl(BASIC_FIELD_CLASSIFICATION_RESULTS_PATH, classification)
                stats.classified_count += 1
                stats.provider_counts[args.provider] += 1
                if args.progress_every > 0 and (
                    stats.classified_count % args.progress_every == 0 or index == len(case_paths)
                ):
                    elapsed = time.monotonic() - item_started
                    print(
                        f"[{now_kst_text()}] 분류 저장: "
                        f"precedent_id={precedent_id} "
                        f"소요 {elapsed:.1f}초 "
                        f"related={classification.get('is_related')} "
                        f"confidence={classification.get('confidence')} "
                        f"{build_progress_summary(index, len(case_paths), stats, started_monotonic)}",
                        flush=True,
                    )
                time.sleep(args.delay)
            except Exception as exc:
                stats.failure_count += 1
                error_text = str(exc)
                append_jsonl(
                    BASIC_FIELD_CLASSIFICATION_FAILURES_PATH,
                    {
                        "created_at": now_utc_iso(),
                        "precedent_id": precedent_id,
                        "source_processed_path": llm_input["source_processed_path"],
                        "provider": args.provider,
                        "model": args.model if args.provider == "ollama" else args.gemini_model,
                        "error": error_text,
                    },
                )
                print(
                    f"[{now_kst_text()}] 분류 실패: "
                    f"precedent_id={precedent_id} error={exc} "
                    f"{build_progress_summary(index, len(case_paths), stats, started_monotonic)}",
                    flush=True,
                )
                if args.provider == "gemini" and is_terminal_gemini_error(exc):
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
            f"대상 {len(case_paths)}건, "
            f"성공 {stats.classified_count}건, "
            f"스킵 {stats.skipped_existing_count}건, "
            f"실패 {stats.failure_count}건",
            flush=True,
        )
        return 130

    write_manifest(stats, args)
    print(
        "완료: "
        f"대상 {len(case_paths)}건, "
        f"성공 {stats.classified_count}건, "
        f"스킵 {stats.skipped_existing_count}건, "
        f"실패 {stats.failure_count}건",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
