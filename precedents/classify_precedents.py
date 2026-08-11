#!/usr/bin/env python3
"""판례 원본 JSON을 LLM 분류용 데이터로 정리하고 로컬 LLM 분류를 실행한다.

이 파일은 수집된 `local_data/precedents/raw/details/*.json` 원본을 삭제하거나
덮어쓰지 않는다. 각 판례에서 사건명, 판시사항, 판결요지, 판례내용 같은
필드를 뽑아 LLM 입력 JSONL을 만들고, 옵션에 따라 Ollama 로컬 LLM으로
요약과 관련성 분류 결과를 생성한다.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from precedent_config import (
    CLASSIFICATION_FAILURES_PATH,
    CLASSIFICATION_MANIFEST_PATH,
    CLASSIFICATION_RESULTS_PATH,
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_PRECEDENT_LLM_MODEL,
    LLM_INPUTS_PATH,
    RAW_DETAILS_DIR,
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
        "--model",
        default=os.environ.get("PRECEDENT_LLM_MODEL", DEFAULT_PRECEDENT_LLM_MODEL),
        help="Ollama에서 사용할 로컬 LLM 모델명.",
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
        "--max-text-chars",
        type=int,
        default=12000,
        help="LLM에 넣을 판례 텍스트 최대 글자 수.",
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
        "--overwrite",
        action="store_true",
        help="이미 분류된 판례도 다시 분류한다.",
    )
    return parser.parse_args()


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


def extract_prec_service(raw_payload: dict[str, Any]) -> dict[str, Any]:
    """상세 API 응답에서 PrecService 본문 객체를 꺼낸다."""
    response = raw_payload.get("response", {})
    service = response.get("PrecService", {})
    if not isinstance(service, dict):
        return {}
    return service


def find_terms(text: str, terms: list[str]) -> list[str]:
    """텍스트에 등장하는 신호 단어를 중복 없이 찾는다."""
    found = []
    for term in terms:
        if term in text and term not in found:
            found.append(term)
    return found


def build_rule_signals(service: dict[str, Any], source_text: str) -> dict[str, Any]:
    """최종 판정이 아니라 LLM과 사람이 참고할 사전 신호를 만든다."""
    case_type = normalize_text(service.get("사건종류명"))
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
    service: dict[str, Any],
    matched_queries: list[dict[str, Any]],
    max_text_chars: int,
) -> tuple[str, bool]:
    """LLM이 판단할 수 있도록 판례 주요 필드를 한 덩어리 텍스트로 만든다."""
    fields = [
        ("사건명", service.get("사건명")),
        ("사건번호", service.get("사건번호")),
        ("법원명", service.get("법원명")),
        ("선고일자", service.get("선고일자")),
        ("사건종류명", service.get("사건종류명")),
        ("판결유형", service.get("판결유형")),
        ("판시사항", service.get("판시사항")),
        ("판결요지", service.get("판결요지")),
        ("참조조문", service.get("참조조문")),
        ("판례내용", service.get("판례내용")),
    ]
    matched_query_text = ", ".join(
        str(match.get("query"))
        for match in matched_queries
        if match.get("query")
    )
    lines = [f"수집검색어: {matched_query_text}"]
    for label, value in fields:
        normalized = normalize_text(value)
        if normalized:
            lines.append(f"{label}:\n{normalized}")
    return truncate_text("\n\n".join(lines), max_text_chars)


def build_llm_input(raw_path: Path, max_text_chars: int) -> dict[str, Any]:
    """상세 원본 JSON 하나를 LLM 입력용 구조로 변환한다."""
    raw_payload = read_json(raw_path)
    service = extract_prec_service(raw_payload)
    matched_queries = raw_payload.get("matched_queries", [])
    classification_text, truncated = build_classification_text(
        service,
        matched_queries if isinstance(matched_queries, list) else [],
        max_text_chars,
    )
    return {
        "schema_version": "precedent_llm_input.v1",
        "created_at": now_utc_iso(),
        "precedent_id": str(raw_payload.get("precedent_id") or raw_path.stem),
        "source_detail_path": str(raw_path),
        "metadata": {
            "사건명": normalize_text(service.get("사건명")),
            "사건번호": normalize_text(service.get("사건번호")),
            "법원명": normalize_text(service.get("법원명")),
            "선고일자": normalize_text(service.get("선고일자")),
            "사건종류명": normalize_text(service.get("사건종류명")),
            "판결유형": normalize_text(service.get("판결유형")),
        },
        "matched_queries": matched_queries if isinstance(matched_queries, list) else [],
        "rule_signals": build_rule_signals(service, classification_text),
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
목표는 공식 판례 원문이 주거용 부동산 매매·임대차 분쟁 서비스에 쓸 수 있는지 판단하는 것이다.

중요 규칙:
- 키워드만 보고 판단하지 말고 사건명, 판시사항, 판결요지, 판례내용을 종합해 판단한다.
- 상가, 농지, 세금, 형사 사건이어도 주거용 전세·월세·매매 분쟁 설명에 직접 유용하면 uncertain 또는 related로 둘 수 있다.
- 확실하지 않으면 unrelated 대신 uncertain을 사용한다.
- 없는 사실, 사건번호, 법률효과, 결론을 만들지 않는다.
- 반드시 JSON 객체만 출력한다.

출력 JSON 스키마:
{{
  "schema_version": "precedent_llm_classification.v1",
  "precedent_id": "{llm_input["precedent_id"]}",
  "is_related": "related | unrelated | uncertain",
  "domain": "sale | jeonse | monthly | lease_common | real_estate_common | out_of_scope | uncertain",
  "dispute_types": ["보증금반환", "건물인도", "등기", "중개사책임" 등],
  "case_summary": "당사자, 사건 배경, 쟁점, 결론을 2~4문장으로 요약",
  "relevance_reason": "관련/비관련/불확실 판단 이유와 근거 필드",
  "exclusion_reason": "범위 밖이면 이유, 아니면 null",
  "confidence": "high | medium | low",
  "needs_human_review": true,
  "evidence_fields": ["사건명", "판시사항", "판결요지", "판례내용"],
  "warnings": []
}}

참고 신호:
{json.dumps(llm_input["rule_signals"], ensure_ascii=False)}

판례 입력:
{llm_input["classification_text"]}
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


def call_ollama(
    llm_input: dict[str, Any],
    model: str,
    ollama_url: str,
    timeout: float = 180,
) -> dict[str, Any]:
    """Ollama 로컬 LLM에 분류 프롬프트를 보내고 JSON 결과를 받는다."""
    endpoint = ollama_url.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "prompt": build_prompt(llm_input),
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
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
    classification = parse_json_object(raw_response)
    classification["precedent_id"] = str(
        classification.get("precedent_id") or llm_input["precedent_id"]
    )
    classification["model"] = model
    classification["classified_at"] = now_utc_iso()
    classification["metadata"] = llm_input["metadata"]
    classification["matched_queries"] = llm_input["matched_queries"]
    classification["rule_signals"] = llm_input["rule_signals"]
    return classification


def iter_detail_paths(limit: int | None) -> list[Path]:
    """수집된 상세 JSON 파일 경로를 정렬해서 반환한다."""
    paths = sorted(RAW_DETAILS_DIR.glob("*.json"))
    if limit is None:
        return paths
    return paths[:limit]


def write_manifest(stats: RunStats, args: argparse.Namespace) -> None:
    """분류 실행의 요약 정보를 manifest JSON으로 저장한다."""
    manifest = {
        "schema_version": "precedent_classification_manifest.v1",
        "started_at": stats.started_at,
        "finished_at": now_utc_iso(),
        "mode": stats.mode,
        "model": args.model if args.mode == "classify" else None,
        "paths": {
            "raw_details": str(RAW_DETAILS_DIR),
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
        },
    }
    write_json(CLASSIFICATION_MANIFEST_PATH, manifest)


def prepare_inputs(detail_paths: list[Path], args: argparse.Namespace, stats: RunStats) -> None:
    """상세 JSON들을 LLM 입력 JSONL로 변환해 저장한다."""
    LLM_INPUTS_PATH.write_text("", encoding="utf-8")
    for index, raw_path in enumerate(detail_paths, start=1):
        llm_input = build_llm_input(raw_path, args.max_text_chars)
        append_jsonl(LLM_INPUTS_PATH, llm_input)
        stats.prepared_count += 1
        if args.progress_every > 0 and index % args.progress_every == 0:
            print(f"입력 생성 {index}/{len(detail_paths)}", flush=True)


def classify_inputs(detail_paths: list[Path], args: argparse.Namespace, stats: RunStats) -> None:
    """상세 JSON들을 읽어 로컬 LLM 분류 결과 JSONL을 만든다."""
    processed_ids = set() if args.overwrite else load_processed_ids(CLASSIFICATION_RESULTS_PATH)
    if args.overwrite:
        CLASSIFICATION_RESULTS_PATH.write_text("", encoding="utf-8")

    for index, raw_path in enumerate(detail_paths, start=1):
        llm_input = build_llm_input(raw_path, args.max_text_chars)
        precedent_id = llm_input["precedent_id"]
        if precedent_id in processed_ids:
            stats.skipped_existing_count += 1
            if (
                args.progress_every > 0
                and stats.skipped_existing_count % args.progress_every == 0
            ):
                print(
                    f"분류 스킵 {index}/{len(detail_paths)}: "
                    f"스킵 {stats.skipped_existing_count}건",
                    flush=True,
                )
            continue

        try:
            classification = call_ollama(llm_input, args.model, args.ollama_url)
            append_jsonl(CLASSIFICATION_RESULTS_PATH, classification)
            stats.classified_count += 1
            print(
                f"분류 저장 {index}/{len(detail_paths)}: "
                f"precedent_id={precedent_id} "
                f"related={classification.get('is_related')} "
                f"confidence={classification.get('confidence')}",
                flush=True,
            )
            time.sleep(args.delay)
        except Exception as exc:
            stats.failure_count += 1
            append_jsonl(
                CLASSIFICATION_FAILURES_PATH,
                {
                    "created_at": now_utc_iso(),
                    "precedent_id": precedent_id,
                    "source_detail_path": str(raw_path),
                    "error": str(exc),
                },
            )
            print(
                f"분류 실패 {index}/{len(detail_paths)}: "
                f"precedent_id={precedent_id} error={exc}",
                flush=True,
            )


def main() -> int:
    """분류 입력 생성 또는 로컬 LLM 분류 실행을 시작한다."""
    args = parse_args()
    load_env_file()
    ensure_collection_dirs()
    detail_paths = iter_detail_paths(args.limit)
    stats = RunStats(
        started_at=now_utc_iso(),
        mode=args.mode,
        detail_count=len(detail_paths),
    )

    if args.mode == "prepare":
        prepare_inputs(detail_paths, args, stats)
    else:
        classify_inputs(detail_paths, args, stats)

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
