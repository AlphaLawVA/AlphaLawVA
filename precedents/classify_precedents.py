#!/usr/bin/env python3
"""전처리된 판례 JSON을 LLM 분류용 데이터로 정리하고 LLM 분류를 실행한다.

이 파일은 `local_data/precedents/processed/classification_cases/*.json`
분류용 판례를 기준으로 관련성 분류 입력을 만든다. 원본 raw JSON과
전처리 JSON은 삭제하거나 덮어쓰지 않는다.

Before:
    local_data/precedents/processed/classification_cases/{판례일련번호}.json
    - 사건명, 판시사항, 판결요지와 후검수용 keyword_diagnosis를 담은 분류용 파일이다.

After:
    local_data/precedents/processed/llm_inputs.jsonl
    - LLM이 사건명, 판시사항, 판결요지만 보고 관련성 3분류를 판단할 입력 데이터다.
    local_data/precedents/processed/classification_results.jsonl
    - `--mode classify` 실행 시 Ollama 또는 Gemini가 반환한 분류 결과다.
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
from urllib.error import URLError
from urllib.request import Request, urlopen

from precedent_config import (
    CLASSIFICATION_CASES_DIR,
    CLASSIFICATION_FAILURES_PATH,
    CLASSIFICATION_MANIFEST_PATH,
    CLASSIFICATION_RESULTS_PATH,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_PRECEDENT_LLM_MODEL,
    GEMINI_GENERATE_URL_TEMPLATE,
    LLM_INPUTS_PATH,
    PROJECT_ROOT,
    ensure_collection_dirs,
    load_env_file,
    now_utc_iso,
)


RELATED_TERMS = [
    "주택임대차",
    "임대차보증금",
    "보증금반환",
    "보증금 반환",
    "전세",
    "월세",
    "차임",
    "대항력",
    "우선변제권",
    "최우선변제권",
    "확정일자",
    "임차권등기",
    "계약갱신",
    "묵시적 갱신",
    "건물인도",
    "건물명도",
    "소유권이전등기",
    "매매대금",
    "계약금반환",
    "중개대상물 확인",
    "중개사",
    "담보책임",
    "근저당권",
    "전세권",
    "신탁등기",
]

OUT_OF_SCOPE_SIGNAL_TERMS = [
    "상가",
    "권리금",
    "농지",
    "임야",
    "토지수용",
    "재개발",
    "재건축",
    "정비사업",
    "산업단지",
    "공장",
    "창고",
    "특허",
    "법인세",
    "부가가치세",
    "양도소득세",
    "사기",
    "횡령",
    "폭행",
    "살인",
]

HIGH_PRIORITY_CASE_TYPES = {"민사"}
REVIEW_PRIORITY_CASE_TYPES = {"일반행정", "가사", "세무", "형사"}
VALID_RELATED_VALUES = {"related", "unrelated", "uncertain"}
VALID_DOMAINS = {
    "sale",
    "jeonse",
    "monthly",
    "lease_common",
    "real_estate_common",
    "out_of_scope",
    "uncertain",
}
VALID_CONFIDENCE_VALUES = {"high", "medium", "low"}


@dataclass
class RunStats:
    """분류 스크립트 실행 중 누적되는 통계."""

    started_at: str
    mode: str
    detail_count: int = 0
    prepared_count: int = 0
    skipped_existing_count: int = 0
    classified_count: int = 0
    failure_count: int = 0
    filtered_out_count: int = 0
    provider_counts: Counter[str] = field(default_factory=Counter)


def parse_args() -> argparse.Namespace:
    """커맨드라인 옵션을 정의한다."""
    load_env_file()
    parser = argparse.ArgumentParser(
        description="Prepare and classify precedent detail JSON files."
    )
    parser.add_argument(
        "--mode",
        choices=["prepare", "classify"],
        default="prepare",
        help="prepare는 LLM 입력만 만들고, classify는 로컬 LLM 분류까지 실행한다.",
    )
    parser.add_argument(
        "--provider",
        choices=["auto", "ollama", "gemini"],
        default=os.environ.get("PRECEDENT_CLASSIFICATION_PROVIDER", "gemini"),
        help="분류에 사용할 LLM provider. 현재 1차 분류 기본값은 gemini다.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("PRECEDENT_LLM_MODEL", DEFAULT_PRECEDENT_LLM_MODEL),
        help="Ollama에서 사용할 로컬 LLM 모델명.",
    )
    parser.add_argument(
        "--gemini-model",
        default=os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        help="Gemini API에서 사용할 모델명.",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
        help="Ollama base URL. 기본값은 http://127.0.0.1:11434.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="처리할 판례 수 제한. 테스트할 때 사용한다.",
    )
    parser.add_argument(
        "--case-ids",
        default=None,
        help="쉼표로 구분한 판례일련번호만 처리한다. 혼합 샘플 테스트할 때 사용한다.",
    )
    parser.add_argument(
        "--max-text-chars",
        type=int,
        default=12000,
        help="LLM에 넣을 판례 텍스트 최대 글자 수.",
    )
    parser.add_argument(
        "--local-max-reason-chars",
        type=int,
        default=int(os.environ.get("PRECEDENT_LOCAL_MAX_REASON_CHARS", "5000")),
        help="auto 모드에서 이 이유 글자 수 이하이면 Ollama를 사용한다.",
    )
    parser.add_argument(
        "--reason-lt",
        type=int,
        default=None,
        help="이유 글자 수가 이 값보다 작은 판례만 처리한다. 예: 로컬 LLM은 --reason-lt 5000.",
    )
    parser.add_argument(
        "--reason-gte",
        type=int,
        default=None,
        help="이유 글자 수가 이 값 이상인 판례만 처리한다. 예: Gemini 긴 판례는 --reason-gte 5000.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.1,
        help="LLM 호출 사이 대기 시간.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="N건마다 진행 로그를 출력한다.",
    )
    parser.add_argument(
        "--ollama-num-ctx",
        type=int,
        default=int(os.environ.get("PRECEDENT_OLLAMA_NUM_CTX", "8192")),
        help="Ollama 모델에 전달할 context window 크기.",
    )
    parser.add_argument(
        "--ollama-num-predict",
        type=int,
        default=int(os.environ.get("PRECEDENT_OLLAMA_NUM_PREDICT", "1200")),
        help="Ollama가 생성할 최대 토큰 수. JSON 응답이 중간에 끊기는 것을 줄이기 위해 사용한다.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="이미 분류된 판례도 다시 분류한다.",
    )
    args = parser.parse_args()
    if args.provider == "ollama" and args.reason_lt is None and args.reason_gte is None:
        args.reason_lt = args.local_max_reason_chars
    return args


def read_json(path: Path) -> dict[str, Any]:
    """UTF-8 JSON 파일을 딕셔너리로 읽는다."""
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    """JSONL 파일에 한 줄짜리 JSON 객체를 추가한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """딕셔너리를 보기 좋은 UTF-8 JSON 파일로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def normalize_text(value: Any) -> str:
    """API의 HTML 줄바꿈과 공백을 사람이 읽기 쉬운 일반 텍스트로 바꾼다."""
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
    """LLM 입력 길이가 너무 길면 앞부분 기준으로 자르고 잘림 여부를 반환한다."""
    if len(value) <= max_chars:
        return value, False
    return value[:max_chars].rstrip(), True


def find_terms(text: str, terms: list[str]) -> list[str]:
    """텍스트에 등장하는 신호 단어를 중복 없이 찾는다."""
    found = []
    for term in terms:
        if term in text and term not in found:
            found.append(term)
    return found


def display_path(path: Path) -> str:
    """프로젝트 안 파일은 상대경로로 표시해 결과 JSON이 컴퓨터별 절대경로에 덜 묶이게 한다."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def build_rule_signals(case: dict[str, Any], source_text: str) -> dict[str, Any]:
    """후검수용 참고 신호를 만든다.

    이 값은 예전 실험과 호환하기 위해 유지하지만, 현재 LLM 프롬프트에는 넣지 않는다.
    """
    case_type = normalize_text(case.get("사건종류명"))
    related_terms = find_terms(source_text, RELATED_TERMS)
    out_terms = find_terms(source_text, OUT_OF_SCOPE_SIGNAL_TERMS)

    if case_type in HIGH_PRIORITY_CASE_TYPES:
        case_type_priority = "high"
    elif case_type in REVIEW_PRIORITY_CASE_TYPES:
        case_type_priority = "review"
    else:
        case_type_priority = "unknown"

    return {
        "case_type_priority": case_type_priority,
        "possible_related_terms": related_terms,
        "possible_out_of_scope_terms": out_terms,
        "note": "이 값은 삭제 기준이 아니라 LLM/사람 검토용 참고 신호다.",
    }


def build_classification_text(
    case: dict[str, Any],
    max_text_chars: int,
) -> tuple[str, bool]:
    """LLM이 판단할 수 있도록 판례 주요 필드를 한 덩어리 텍스트로 만든다."""
    fields = [
        ("사건명", case.get("사건명")),
        ("판시사항", case.get("판시사항")),
        ("판결요지", case.get("판결요지")),
    ]
    lines = []
    for label, value in fields:
        normalized = normalize_text(value)
        if normalized:
            lines.append(f"{label}:\n{normalized}")
    return truncate_text("\n\n".join(lines), max_text_chars)


def build_llm_input(case_path: Path, max_text_chars: int) -> dict[str, Any]:
    """전처리 JSON 하나를 LLM 입력용 구조로 변환한다."""
    case = read_json(case_path)
    classification_text, truncated = build_classification_text(case, max_text_chars)
    precedent_id = str(case.get("판례일련번호") or case_path.stem)
    keyword_diagnosis = case.get("keyword_diagnosis")
    if not isinstance(keyword_diagnosis, dict):
        keyword_diagnosis = {}
    return {
        "schema_version": "precedent_llm_input.v1",
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
            "접수연도": case.get("접수연도"),
            "사건유형": normalize_text(case.get("사건유형")),
            "이유_글자수": len(normalize_text(case.get("이유"))),
            "이유_포함여부": bool(normalize_text(case.get("이유"))),
        },
        "rule_signals": build_rule_signals(case, classification_text),
        "keyword_diagnosis": keyword_diagnosis,
        "input_truncated": truncated,
        "classification_text": classification_text,
    }


def load_processed_ids(path: Path) -> set[str]:
    """기존 JSONL 결과에서 이미 처리된 판례 ID를 읽어온다."""
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


def build_prompt(llm_input: dict[str, Any]) -> str:
    """판례 관련성 분류를 위한 LLM 프롬프트를 만든다."""
    return f"""
너는 AlphaLawVA의 판례 데이터 검수 보조자다.
목표는 공식 판례의 사건명, 판시사항, 판결요지만 보고 AlphaLawVA 판례 검색/RAG에 넣을지 1차로 나누는 것이다.

중요 규칙:
- 판단값은 is_related 하나만 중요하다. 세부 분야나 분쟁유형은 이번 단계에서 분류하지 않는다.
- 사건명, 판시사항, 판결요지에 실제로 드러난 사건의 핵심을 본다.
- related는 주거용 부동산 매매·전세·월세 분쟁 검색/설명에 직접 쓸 수 있는 판례일 때 선택한다.
- 주거용 여부가 명시되지 않아도 일반 주택 거래에서 자주 발생하는 매매계약, 임대차보증금, 대항력, 임차권등기, 중개사 책임, 소유권이전등기 분쟁이면 related로 둘 수 있다.
- unrelated는 부동산 단어가 있어도 사건의 핵심이 AlphaLawVA 사용자의 주거용 매매·임대차 분쟁과 동떨어진 경우 선택한다.
- 예를 들어 군부계엄·국가 불법행위, 상속재산 점유, 교회·법인 재산, 일반 채권양도·배당, 회사·주식 매매, 세금, 형사범죄 자체가 핵심이면 unrelated다.
- uncertain은 관련 가능성은 있지만 주거용 매매·임대차 분쟁에 직접 쓸 수 있는지 원문만으로 확정하기 어려울 때 선택한다.
- 없는 사실, 사건번호, 법률효과, 결론을 만들지 않는다.
- 원문에 없는 주거용 여부나 임대차·매매 사실을 추측하지 않는다.
- is_related와 confidence는 아래 후보 중 정확히 하나만 고른다. "A | B"처럼 여러 값을 섞지 않는다.
- needs_human_review는 is_related가 uncertain이거나 confidence가 low일 때 true로 둔다. 그 외에는 특별한 이유가 있을 때만 true로 둔다.
- relevance_reason에는 어떤 필드 때문에 related/unrelated/uncertain으로 판단했는지 한 문장으로 쓴다.
- 판례 입력에 없는 키워드 진단, 이유 전문, 주문, 청구취지, 참조조문은 추측하지 않는다.
- 반드시 JSON 객체만 출력한다.

출력 JSON 스키마:
{{
  "schema_version": "precedent_llm_classification.v1",
  "precedent_id": "{llm_input["precedent_id"]}",
  "is_related": "related 또는 unrelated 또는 uncertain 중 하나",
  "relevance_reason": "관련/비관련/불확실 판단 이유와 근거 필드",
  "exclusion_reason": "범위 밖이면 이유, 아니면 null",
  "confidence": "high | medium | low",
  "needs_human_review": true,
  "evidence_fields": ["사건명", "판시사항", "판결요지"],
  "warnings": []
}}

판례 입력:
{llm_input["classification_text"]}
""".strip()


def get_gemini_api_key() -> str:
    """Gemini API 키를 환경변수에서 읽는다."""
    load_env_file()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(".env 또는 환경변수에 GEMINI_API_KEY를 설정해야 Gemini 분류를 실행할 수 있다.")
    return api_key


def normalize_gemini_model_name(model: str) -> str:
    """Gemini 모델명을 REST URL에 넣을 수 있는 짧은 이름으로 정리한다."""
    cleaned = model.strip()
    if cleaned.startswith("models/"):
        return cleaned.removeprefix("models/")
    return cleaned


def parse_json_object(text: str) -> dict[str, Any]:
    """LLM 응답에서 JSON 객체만 안전하게 뽑아 파싱한다."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        if stripped.startswith("{") and not stripped.endswith("}"):
            try:
                return json.loads(stripped + "}")
            except json.JSONDecodeError:
                pass
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or start >= end:
            raise
        return json.loads(stripped[start : end + 1])


def normalize_choice(value: Any, valid_values: set[str], default: str) -> str:
    """LLM이 낸 문자열을 허용된 enum 값 하나로 정리한다."""
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


def normalize_dispute_types(value: Any) -> list[str]:
    """분쟁유형 값을 문자열 배열로 정리한다.

    현재 1차 분류 결과에는 저장하지 않지만, 후속 세부 태깅 실험에서
    다시 쓸 수 있도록 보존해둔다.
    """
    if not isinstance(value, list):
        return []
    normalized = []
    for item in value:
        text = normalize_text(item)
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def validate_classification(
    classification: dict[str, Any],
    llm_input: dict[str, Any],
) -> dict[str, Any]:
    """LLM 분류 결과를 저장 전에 보수적으로 검증하고 보정한다."""
    warnings = classification.get("warnings")
    if not isinstance(warnings, list):
        warnings = []

    is_related = normalize_choice(
        classification.get("is_related"),
        VALID_RELATED_VALUES,
        "uncertain",
    )
    confidence = normalize_choice(
        classification.get("confidence"),
        VALID_CONFIDENCE_VALUES,
        "low",
    )

    if is_related == "unrelated":
        classification["exclusion_reason"] = classification.get("exclusion_reason") or classification.get(
            "relevance_reason"
        )

    needs_human_review = classification.get("needs_human_review")
    if not isinstance(needs_human_review, bool):
        needs_human_review = is_related == "uncertain" or confidence == "low"
        warnings.append("needs_human_review가 bool이 아니어서 규칙으로 보정함")
    if is_related == "uncertain" or confidence == "low":
        needs_human_review = True

    evidence_fields = classification.get("evidence_fields")
    if not isinstance(evidence_fields, list):
        evidence_fields = []

    case_summary = normalize_text(classification.get("case_summary"))
    relevance_reason = normalize_text(classification.get("relevance_reason"))
    if not relevance_reason:
        raise ValueError("필수 필드 누락: relevance_reason")
    if case_summary and not 150 <= len(case_summary) <= 200:
        warnings.append(f"case_summary 길이가 150~200자를 벗어남: {len(case_summary)}자")

    classification["schema_version"] = "precedent_llm_classification.v1"
    classification["precedent_id"] = str(
        classification.get("precedent_id") or llm_input["precedent_id"]
    )
    classification["is_related"] = is_related
    classification.pop("domain", None)
    classification.pop("dispute_types", None)
    if case_summary:
        classification["case_summary"] = case_summary
    else:
        classification.pop("case_summary", None)
    classification["relevance_reason"] = relevance_reason
    classification["confidence"] = confidence
    classification["needs_human_review"] = needs_human_review
    classification["evidence_fields"] = [normalize_text(field) for field in evidence_fields if normalize_text(field)]
    classification["warnings"] = warnings
    return classification


def call_ollama(
    llm_input: dict[str, Any],
    model: str,
    ollama_url: str,
    num_ctx: int,
    num_predict: int,
    timeout: float = 180,
) -> dict[str, Any]:
    """Ollama 로컬 LLM에 분류 프롬프트를 보내고 JSON 결과를 받는다."""
    endpoint = ollama_url.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "prompt": build_prompt(llm_input),
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except URLError as exc:
        raise RuntimeError(f"Ollama 호출 실패: {exc}") from exc

    response_payload = json.loads(body)
    raw_response = response_payload.get("response", "")
    try:
        classification = parse_json_object(raw_response)
    except json.JSONDecodeError as exc:
        preview = raw_response[:1000].replace("\n", " ")
        raise RuntimeError(f"Ollama JSON 파싱 실패: {exc}; raw_response_preview={preview}") from exc
    classification = validate_classification(classification, llm_input)
    classification["model"] = model
    classification["provider"] = "ollama"
    classification["classified_at"] = now_utc_iso()
    classification["metadata"] = llm_input["metadata"]
    classification["keyword_diagnosis"] = llm_input.get("keyword_diagnosis", {})
    return classification


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


def call_gemini(
    llm_input: dict[str, Any],
    model: str,
    timeout: float = 240,
) -> dict[str, Any]:
    """Gemini API에 분류 프롬프트를 보내고 JSON 결과를 받는다."""
    api_key = get_gemini_api_key()
    model_name = normalize_gemini_model_name(model)
    endpoint = GEMINI_GENERATE_URL_TEMPLATE.format(model=model_name)
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": build_prompt(llm_input)}],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
        },
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except URLError as exc:
        raise RuntimeError(f"Gemini 호출 실패: {exc}") from exc

    response_payload = json.loads(body)
    raw_response = extract_gemini_text(response_payload)
    try:
        classification = parse_json_object(raw_response)
    except json.JSONDecodeError as exc:
        preview = raw_response[:1000].replace("\n", " ")
        raise RuntimeError(f"Gemini JSON 파싱 실패: {exc}; raw_response_preview={preview}") from exc
    classification = validate_classification(classification, llm_input)
    classification["model"] = model_name
    classification["provider"] = "gemini"
    classification["classified_at"] = now_utc_iso()
    classification["metadata"] = llm_input["metadata"]
    classification["keyword_diagnosis"] = llm_input.get("keyword_diagnosis", {})
    return classification


def select_provider(llm_input: dict[str, Any], args: argparse.Namespace) -> str:
    """설정에 따라 Ollama 또는 Gemini 중 어느 provider를 쓸지 고른다."""
    if args.provider != "auto":
        return args.provider
    if not llm_input.get("metadata", {}).get("이유_포함여부"):
        return "gemini"
    reason_length = int(llm_input.get("metadata", {}).get("이유_글자수") or 0)
    if reason_length < args.local_max_reason_chars:
        return "ollama"
    return "gemini"


def classify_with_selected_provider(
    llm_input: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    """선택된 provider로 판례 하나를 분류한다."""
    provider = select_provider(llm_input, args)
    if provider == "ollama":
        return call_ollama(
            llm_input,
            args.model,
            args.ollama_url,
            args.ollama_num_ctx,
            args.ollama_num_predict,
        )
    if provider == "gemini":
        return call_gemini(llm_input, args.gemini_model)
    raise RuntimeError(f"지원하지 않는 provider입니다: {provider}")


def get_reason_length(case_path: Path) -> int:
    """분류용 판례 JSON에서 이유 글자 수를 계산한다."""
    case = read_json(case_path)
    return len(normalize_text(case.get("이유")))


def matches_reason_filter(case_path: Path, args: argparse.Namespace) -> bool:
    """명령행에서 지정한 이유 글자 수 필터에 맞는 판례인지 확인한다."""
    reason_length = get_reason_length(case_path)
    if args.reason_lt is not None and reason_length >= args.reason_lt:
        return False
    if args.reason_gte is not None and reason_length < args.reason_gte:
        return False
    return True


def iter_case_paths() -> list[Path]:
    """분류용 판례 JSON 파일 경로를 판례일련번호 순서로 반환한다."""
    return sorted(
        CLASSIFICATION_CASES_DIR.glob("*.json"),
        key=lambda path: int(path.stem) if path.stem.isdigit() else path.stem,
    )


def select_case_paths(args: argparse.Namespace, stats: RunStats) -> list[Path]:
    """전체 분류용 판례에서 길이 필터와 limit을 적용한 실행 대상 경로를 만든다."""
    all_paths = iter_case_paths()
    if args.case_ids:
        id_order = [case_id.strip() for case_id in args.case_ids.split(",") if case_id.strip()]
        path_by_id = {path.stem: path for path in all_paths}
        missing_ids = [case_id for case_id in id_order if case_id not in path_by_id]
        if missing_ids:
            raise FileNotFoundError(f"분류용 판례 파일을 찾지 못했습니다: {', '.join(missing_ids)}")
        selected_paths = [path_by_id[case_id] for case_id in id_order]
        stats.filtered_out_count = len(all_paths) - len(selected_paths)
        return selected_paths
    filtered_paths = [path for path in all_paths if matches_reason_filter(path, args)]
    stats.filtered_out_count = len(all_paths) - len(filtered_paths)
    if args.limit is not None:
        return filtered_paths[: args.limit]
    return filtered_paths


def write_manifest(stats: RunStats, args: argparse.Namespace) -> None:
    """분류 실행의 요약 정보를 manifest JSON으로 저장한다."""
    manifest = {
        "schema_version": "precedent_classification_manifest.v1",
        "started_at": stats.started_at,
        "finished_at": now_utc_iso(),
        "mode": stats.mode,
        "model": args.model if args.mode == "classify" else None,
        "provider": args.provider,
        "gemini_model": args.gemini_model if args.mode == "classify" else None,
        "ollama_options": {
            "num_ctx": args.ollama_num_ctx,
            "num_predict": args.ollama_num_predict,
        }
        if args.mode == "classify"
        else None,
        "local_max_reason_chars": args.local_max_reason_chars,
        "filters": {
            "reason_lt": args.reason_lt,
            "reason_gte": args.reason_gte,
            "limit": args.limit,
            "case_ids": args.case_ids,
        },
        "paths": {
            "classification_cases": str(CLASSIFICATION_CASES_DIR),
            "llm_inputs": str(LLM_INPUTS_PATH),
            "classification_results": str(CLASSIFICATION_RESULTS_PATH),
            "classification_failures": str(CLASSIFICATION_FAILURES_PATH),
        },
        "stats": {
            "detail_count": stats.detail_count,
            "prepared_count": stats.prepared_count,
            "skipped_existing_count": stats.skipped_existing_count,
            "classified_count": stats.classified_count,
            "failure_count": stats.failure_count,
            "filtered_out_count": stats.filtered_out_count,
            "provider_counts": dict(stats.provider_counts),
        },
    }
    write_json(CLASSIFICATION_MANIFEST_PATH, manifest)


def build_progress_summary(
    index: int,
    total: int,
    stats: RunStats,
    started_monotonic: float,
) -> str:
    """현재 분류 진행 상황과 남은 예상 시간을 한 줄 문자열로 만든다."""
    elapsed = time.monotonic() - started_monotonic
    handled_count = stats.classified_count + stats.skipped_existing_count + stats.failure_count
    average_seconds = elapsed / handled_count if handled_count else 0
    remaining_count = max(0, total - index)
    estimated_remaining = average_seconds * remaining_count if average_seconds else 0
    return (
        f"진행 {index}/{total} "
        f"성공 {stats.classified_count}건 "
        f"스킵 {stats.skipped_existing_count}건 "
        f"실패 {stats.failure_count}건 "
        f"경과 {format_duration(elapsed)} "
        f"평균 {average_seconds:.1f}초/건 "
        f"예상남음 {format_duration(estimated_remaining)}"
    )


def prepare_inputs(case_paths: list[Path], args: argparse.Namespace, stats: RunStats) -> None:
    """전처리 JSON들을 LLM 입력 JSONL로 변환해 저장한다."""
    started_monotonic = time.monotonic()
    LLM_INPUTS_PATH.write_text("", encoding="utf-8")
    for index, case_path in enumerate(case_paths, start=1):
        llm_input = build_llm_input(case_path, args.max_text_chars)
        provider = select_provider(llm_input, args)
        llm_input["recommended_provider"] = provider
        stats.provider_counts[provider] += 1
        append_jsonl(LLM_INPUTS_PATH, llm_input)
        stats.prepared_count += 1
        if args.progress_every > 0 and index % args.progress_every == 0:
            elapsed = time.monotonic() - started_monotonic
            print(
                f"입력 생성 {index}/{len(case_paths)} "
                f"경과 {format_duration(elapsed)}",
                flush=True,
            )


def classify_inputs(case_paths: list[Path], args: argparse.Namespace, stats: RunStats) -> None:
    """전처리 JSON들을 읽어 로컬 LLM 분류 결과 JSONL을 만든다."""
    started_monotonic = time.monotonic()
    processed_ids = set() if args.overwrite else load_processed_ids(CLASSIFICATION_RESULTS_PATH)
    if args.overwrite:
        CLASSIFICATION_RESULTS_PATH.write_text("", encoding="utf-8")

    for index, case_path in enumerate(case_paths, start=1):
        llm_input = build_llm_input(case_path, args.max_text_chars)
        precedent_id = llm_input["precedent_id"]
        if precedent_id in processed_ids:
            stats.skipped_existing_count += 1
            if (
                args.progress_every > 0
                and stats.skipped_existing_count % args.progress_every == 0
            ):
                print(
                    f"[{now_kst_text()}] 분류 스킵: precedent_id={precedent_id} "
                    f"{build_progress_summary(index, len(case_paths), stats, started_monotonic)}",
                    flush=True,
                )
            continue

        provider = select_provider(llm_input, args)
        item_started = time.monotonic()
        try:
            classification = classify_with_selected_provider(llm_input, args)
            append_jsonl(CLASSIFICATION_RESULTS_PATH, classification)
            stats.classified_count += 1
            stats.provider_counts[provider] += 1
            item_elapsed = time.monotonic() - item_started
            should_log_success = (
                args.progress_every > 0
                and (stats.classified_count % args.progress_every == 0 or index == len(case_paths))
            )
            if should_log_success:
                print(
                    f"[{now_kst_text()}] 분류 저장: "
                    f"precedent_id={precedent_id} "
                    f"provider={provider} "
                    f"소요 {item_elapsed:.1f}초 "
                    f"related={classification.get('is_related')} "
                    f"confidence={classification.get('confidence')} "
                    f"{build_progress_summary(index, len(case_paths), stats, started_monotonic)}",
                    flush=True,
                )
            time.sleep(args.delay)
        except Exception as exc:
            stats.failure_count += 1
            item_elapsed = time.monotonic() - item_started
            append_jsonl(
                CLASSIFICATION_FAILURES_PATH,
                {
                    "created_at": now_utc_iso(),
                    "precedent_id": precedent_id,
                    "provider": provider,
                    "source_processed_path": display_path(case_path),
                    "source_detail_path": llm_input.get("source_detail_path"),
                    "elapsed_seconds": round(item_elapsed, 3),
                    "error": str(exc),
                },
            )
            print(
                f"[{now_kst_text()}] 분류 실패: "
                f"precedent_id={precedent_id} provider={provider} "
                f"소요 {item_elapsed:.1f}초 error={exc} "
                f"{build_progress_summary(index, len(case_paths), stats, started_monotonic)}",
                flush=True,
            )


def main() -> int:
    """분류 입력 생성 또는 로컬 LLM 분류 실행을 시작한다."""
    args = parse_args()
    load_env_file()
    ensure_collection_dirs()
    stats = RunStats(
        started_at=now_utc_iso(),
        mode=args.mode,
    )
    case_paths = select_case_paths(args, stats)
    stats.detail_count = len(case_paths)

    print(
        "시작: "
        f"mode={args.mode}, "
        f"provider={args.provider}, "
        f"대상 {stats.detail_count}건, "
        f"길이필터제외 {stats.filtered_out_count}건, "
        f"reason_lt={args.reason_lt}, "
        f"reason_gte={args.reason_gte}, "
        f"limit={args.limit}",
        flush=True,
    )

    try:
        if args.mode == "prepare":
            prepare_inputs(case_paths, args, stats)
        else:
            classify_inputs(case_paths, args, stats)
    except KeyboardInterrupt:
        write_manifest(stats, args)
        print(
            "중단 요약: "
            f"mode={args.mode}, "
            f"대상 {stats.detail_count}건, "
            f"입력생성 {stats.prepared_count}건, "
            f"분류성공 {stats.classified_count}건, "
            f"스킵 {stats.skipped_existing_count}건, "
            f"실패 {stats.failure_count}건",
            flush=True,
        )
        return 130

    write_manifest(stats, args)
    print(
        "완료: "
        f"mode={args.mode}, "
        f"대상 {stats.detail_count}건, "
        f"입력생성 {stats.prepared_count}건, "
        f"분류성공 {stats.classified_count}건, "
        f"스킵 {stats.skipped_existing_count}건, "
        f"실패 {stats.failure_count}건",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
