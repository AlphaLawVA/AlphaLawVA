# collect_urimalsaem_definitions.py
"""
Description: 정제된 판례 법률용어를 우리말샘에서 정확 일치로 조회하고,
모든 뜻과 분야 정보를 기존 사전 결과와 분리된 파일로 수집한다.
Author: choeminju
Date: 2026-09-22
Before:
    - 정제된 판례 법률용어 후보와 우리말샘 API 인증키가 준비된 상태.

After:
    - local_data/precedents/legal_terms/urimalsaem_definitions/에 원본 응답,
      용어별 뜻 후보, 검토 CSV, 오류와 manifest가 생성.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CANDIDATES_PATH = (
    PROJECT_ROOT
    / "local_data"
    / "precedents"
    / "legal_terms"
    / "matched_candidates_noise_filtered"
    / "matched_legal_terms.jsonl"
)
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT
    / "local_data"
    / "precedents"
    / "legal_terms"
    / "urimalsaem_definitions"
)
SERVICE_URL = "https://opendict.korean.go.kr/api/search"
RAW_RESPONSES_DIR_NAME = "raw_responses"
RESULT_FILENAME = "urimalsaem_term_definitions.jsonl"
REVIEW_FILENAME = "urimalsaem_term_definitions_review.csv"
NOT_FOUND_FILENAME = "urimalsaem_not_found_terms.jsonl"
ERRORS_FILENAME = "errors.jsonl"
MANIFEST_FILENAME = "manifest.json"
SCHEMA_VERSION = "precedent_urimalsaem_definitions.v0.1"
DEFAULT_DELAY_SECONDS = 0.15
DEFAULT_CHECKPOINT_INTERVAL = 25
DEFAULT_MAX_CONSECUTIVE_ERRORS = 5
HTML_TAG_RE = re.compile(r"<[^>]+>")
SEPARATOR_RE = re.compile(r"[\s\-\^·ㆍ]+")
LEGAL_CATEGORY_KEYWORDS = ("법률", "법학")
REAL_ESTATE_CONTEXT_KEYWORDS = (
    "임대",
    "임차",
    "전세",
    "월세",
    "주택",
    "부동산",
    "토지",
    "건물",
    "등기",
    "소유권",
    "보증금",
    "담보",
    "채권",
    "채무",
    "매매",
    "매수",
    "매도",
)


class UrimalsaemApiError(RuntimeError):
    """우리말샘 API 요청 또는 응답 오류."""


def parse_args() -> argparse.Namespace:
    """우리말샘 정의 수집 옵션을 정의한다."""
    parser = argparse.ArgumentParser(
        description="정제된 판례 법률용어의 우리말샘 뜻을 별도로 수집합니다."
    )
    parser.add_argument(
        "--candidates-path",
        type=Path,
        default=DEFAULT_CANDIDATES_PATH,
        help="정제된 판례 법률용어 후보 JSONL 경로.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="우리말샘 전용 결과를 저장할 새 폴더.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="테스트용 후보 용어 개수 제한. 생략하면 전체.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="새 API 요청 사이 대기 시간(초).",
    )
    parser.add_argument(
        "--checkpoint-interval",
        type=int,
        default=DEFAULT_CHECKPOINT_INTERVAL,
        help="중간 결과를 다시 저장할 용어 간격.",
    )
    parser.add_argument(
        "--max-consecutive-errors",
        type=int,
        default=DEFAULT_MAX_CONSECUTIVE_ERRORS,
        help="연속 오류로 안전 중단할 기준.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="기존 결과와 원본 응답을 사용하지 않고 처음부터 다시 요청.",
    )
    return parser.parse_args()


def now_utc_iso() -> str:
    """현재 UTC 시각을 ISO 문자열로 반환한다."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def project_relative_path(path: Path) -> str:
    """프로젝트 내부 경로는 상대경로로 기록한다."""
    resolved = path.resolve()
    if resolved.is_relative_to(PROJECT_ROOT):
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    return resolved.as_posix()


def validate_args(args: argparse.Namespace) -> None:
    """실행 옵션의 기본 범위를 검증한다."""
    if args.limit is not None and args.limit < 1:
        raise ValueError("limit은 1 이상이어야 합니다.")
    if args.delay < 0:
        raise ValueError("delay는 0 이상이어야 합니다.")
    if args.checkpoint_interval < 1:
        raise ValueError("checkpoint-interval은 1 이상이어야 합니다.")
    if args.max_consecutive_errors < 1:
        raise ValueError("max-consecutive-errors는 1 이상이어야 합니다.")


def read_env_file(path: Path) -> dict[str, str]:
    """간단한 KEY=VALUE 형식의 로컬 환경 파일을 읽는다."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_api_key() -> str:
    """환경 변수 또는 .env에서 우리말샘 인증키를 읽는다."""
    value = os.getenv("URIMALSAEM_API_KEY") or read_env_file(
        PROJECT_ROOT / ".env"
    ).get("URIMALSAEM_API_KEY")
    if not value:
        raise RuntimeError("URIMALSAEM_API_KEY가 .env에 설정되어 있지 않습니다.")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 객체 목록을 읽는다."""
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL {line_number}번째 줄이 객체가 아닙니다: {path}")
            rows.append(value)
    return rows


def write_json(path: Path, value: Any) -> None:
    """JSON을 원자적으로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary_path.replace(path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """JSONL을 원자적으로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary_path.replace(path)


def file_sha256(path: Path) -> str:
    """파일 SHA-256을 계산한다."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean_text(value: Any) -> str:
    """HTML 표시와 중복 공백을 제거한다."""
    text = html.unescape(str(value or ""))
    text = HTML_TAG_RE.sub(" ", text)
    return " ".join(text.split())


def normalize_headword(value: Any) -> str:
    """사전 표제어의 구분 기호와 공백을 제거해 비교 키를 만든다."""
    text = unicodedata.normalize("NFKC", clean_text(value)).lower()
    return SEPARATOR_RE.sub("", text)


def raw_response_path(raw_dir: Path, term: str) -> Path:
    """용어에 대응하는 인증정보 없는 원본 응답 경로를 만든다."""
    signature = hashlib.sha256(term.encode("utf-8")).hexdigest()[:24]
    return raw_dir / f"{signature}.json"


def fetch_search_payload(api_key: str, term: str) -> dict[str, Any]:
    """우리말샘에서 표제어 정확 일치 검색 결과를 받는다."""
    params = {
        "key": api_key,
        "q": term,
        "req_type": "json",
        "advanced": "y",
        "target": "1",
        "method": "exact",
        "num": "100",
    }
    request_url = f"{SERVICE_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        request_url,
        headers={"User-Agent": "AlphaLawVA/1.0 urimalsaem-term-collector"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise UrimalsaemApiError(f"우리말샘 HTTP 오류: {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise UrimalsaemApiError("우리말샘 네트워크 요청 실패") from exc
    except json.JSONDecodeError as exc:
        raise UrimalsaemApiError("우리말샘 응답이 유효한 JSON이 아닙니다.") from exc
    if not isinstance(payload, dict):
        raise UrimalsaemApiError("우리말샘 응답이 JSON 객체가 아닙니다.")
    return payload


def load_or_fetch_payload(
    *,
    api_key: str,
    term: str,
    path: Path,
    refresh: bool,
) -> tuple[dict[str, Any], str]:
    """캐시를 재사용하거나 새 원본 응답을 인증정보 없이 저장한다."""
    if path.exists() and not refresh:
        cached = json.loads(path.read_text(encoding="utf-8"))
        payload = cached.get("payload")
        if not isinstance(payload, dict):
            raise ValueError(f"캐시 payload가 객체가 아닙니다: {path}")
        return payload, "cache"
    payload = fetch_search_payload(api_key, term)
    write_json(
        path,
        {
            "lookup_term": term,
            "collected_at": now_utc_iso(),
            "payload": payload,
        },
    )
    return payload, "api"


def extract_search_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """우리말샘 검색 응답에서 개별 표제어 결과를 꺼낸다."""
    channel = payload.get("channel")
    if not isinstance(channel, dict):
        error = payload.get("error")
        message = clean_text(error) if error else "channel 객체가 없습니다."
        raise UrimalsaemApiError(f"우리말샘 API 응답 오류: {message}")
    items = channel.get("item", []) or []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        raise UrimalsaemApiError("우리말샘 item 값이 목록이 아닙니다.")
    return [item for item in items if isinstance(item, dict)]


def extract_item_senses(item: dict[str, Any]) -> list[dict[str, Any]]:
    """표제어 한 건에서 단일 또는 복수 뜻 객체를 꺼낸다."""
    senses = item.get("sense", []) or []
    if isinstance(senses, dict):
        senses = [senses]
    if not isinstance(senses, list):
        return []
    return [sense for sense in senses if isinstance(sense, dict)]


def normalize_sense(
    item: dict[str, Any], sense: dict[str, Any], source_term: str
) -> dict[str, Any]:
    """우리말샘 뜻풀이 한 건을 내부 스키마로 변환한다."""
    headword = clean_text(item.get("word"))
    category = clean_text(sense.get("cat"))
    definition = clean_text(sense.get("definition"))
    context = " ".join((category, definition))
    matched_context = sorted(
        {keyword for keyword in REAL_ESTATE_CONTEXT_KEYWORDS if keyword in context}
    )
    is_legal_category = any(keyword in category for keyword in LEGAL_CATEGORY_KEYWORDS)
    if is_legal_category:
        review_priority = 0
        priority_reason = "법률 분야 뜻풀이"
    elif matched_context:
        review_priority = 1
        priority_reason = "부동산 관련 문맥 포함"
    else:
        review_priority = 2
        priority_reason = "일반 또는 타 분야 뜻풀이"
    return {
        "provider": "urimalsaem",
        "lookup_term": source_term,
        "headword": headword,
        "match_type": (
            "exact"
            if normalize_headword(source_term) == normalize_headword(headword)
            else "non_exact"
        ),
        "target_code": clean_text(sense.get("target_code")),
        "sense_no": clean_text(sense.get("sense_no")),
        "definition": definition,
        "part_of_speech": clean_text(sense.get("pos")),
        "category": category,
        "origin": clean_text(sense.get("origin")),
        "entry_type": clean_text(sense.get("type")),
        "source_link": clean_text(sense.get("link")),
        "is_legal_category": is_legal_category,
        "matched_context_keywords": matched_context,
        "review_priority": review_priority,
        "review_priority_reason": priority_reason,
    }


def build_result_row(
    candidate: dict[str, Any],
    items: list[dict[str, Any]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    """용어 하나의 모든 정확 일치 뜻과 수집 상태를 구성한다."""
    term = str(candidate["match_key"])
    normalized = [
        normalize_sense(item, sense, term)
        for item in items
        for sense in extract_item_senses(item)
    ]
    exact_senses = [
        row for row in normalized if row["match_type"] == "exact" and row["definition"]
    ]
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in exact_senses:
        key = (row["target_code"], row["definition"], row["category"])
        unique.setdefault(key, row)
    senses = sorted(
        unique.values(),
        key=lambda row: (
            row["review_priority"],
            row["sense_no"],
            row["target_code"],
            row["definition"],
        ),
    )
    for rank, sense in enumerate(senses, start=1):
        sense["review_rank"] = rank
    if senses:
        status = "exact"
    elif errors:
        status = "error"
    else:
        status = "not_found"
    return {
        "match_key": term,
        "term": candidate.get("term", term),
        "total_case_count": candidate.get("total_case_count", 0),
        "total_occurrence_count": candidate.get("total_occurrence_count", 0),
        "field_stats": candidate.get("field_stats", {}),
        "lookup_status": status,
        "definition_count": len(senses),
        "legal_category_definition_count": sum(
            sense["is_legal_category"] for sense in senses
        ),
        "definitions": senses,
        "selection_status": "pending_review" if senses else "unresolved",
        "collection_errors": errors,
    }


def write_review_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """용어별 모든 뜻을 사람이 검토하기 쉬운 CSV로 저장한다."""
    columns = [
        "term",
        "total_case_count",
        "lookup_status",
        "definition_count",
        "review_rank",
        "review_priority",
        "review_priority_reason",
        "headword",
        "sense_no",
        "part_of_speech",
        "category",
        "definition",
        "entry_type",
        "origin",
        "target_code",
        "source_link",
        "matched_context_keywords",
        "review_status",
        "review_note",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            definitions = row["definitions"] or [{}]
            for definition in definitions:
                writer.writerow(
                    {
                        "term": row["term"],
                        "total_case_count": row["total_case_count"],
                        "lookup_status": row["lookup_status"],
                        "definition_count": row["definition_count"],
                        "review_rank": definition.get("review_rank", ""),
                        "review_priority": definition.get("review_priority", ""),
                        "review_priority_reason": definition.get(
                            "review_priority_reason", ""
                        ),
                        "headword": definition.get("headword", ""),
                        "sense_no": definition.get("sense_no", ""),
                        "part_of_speech": definition.get("part_of_speech", ""),
                        "category": definition.get("category", ""),
                        "definition": definition.get("definition", ""),
                        "entry_type": definition.get("entry_type", ""),
                        "origin": definition.get("origin", ""),
                        "target_code": definition.get("target_code", ""),
                        "source_link": definition.get("source_link", ""),
                        "matched_context_keywords": ", ".join(
                            definition.get("matched_context_keywords", [])
                        ),
                        "review_status": "pending",
                        "review_note": "",
                    }
                )
    temporary_path.replace(path)


def write_checkpoint(
    *,
    output_dir: Path,
    candidates_path: Path,
    candidates: list[dict[str, Any]],
    results_by_key: dict[str, dict[str, Any]],
    api_requests: int,
    baseline_api_requests: int,
    cached_requests: int,
    resumed_count: int,
    started_at: float,
    status: str,
    stop_reason: str | None,
) -> None:
    """현재까지의 결과와 실행 기록을 원자적으로 저장한다."""
    results_path = output_dir / RESULT_FILENAME
    review_path = output_dir / REVIEW_FILENAME
    not_found_path = output_dir / NOT_FOUND_FILENAME
    errors_path = output_dir / ERRORS_FILENAME
    manifest_path = output_dir / MANIFEST_FILENAME
    rows = [
        results_by_key[candidate["match_key"]]
        for candidate in candidates
        if candidate["match_key"] in results_by_key
    ]
    error_rows = [
        {"match_key": row["match_key"], **error}
        for row in rows
        for error in row["collection_errors"]
    ]
    not_found_rows = [
        {
            "match_key": row["match_key"],
            "term": row["term"],
            "total_case_count": row["total_case_count"],
            "total_occurrence_count": row["total_occurrence_count"],
            "field_stats": row["field_stats"],
            "lookup_status": row["lookup_status"],
            "selection_status": row["selection_status"],
        }
        for row in rows
        if row["lookup_status"] == "not_found"
    ]
    write_jsonl(results_path, rows)
    write_review_csv(review_path, rows)
    write_jsonl(not_found_path, not_found_rows)
    write_jsonl(errors_path, error_rows)
    status_counts = Counter(row["lookup_status"] for row in rows)
    category_counts = Counter(
        definition["category"] or "미분류"
        for row in rows
        for definition in row["definitions"]
    )
    write_json(
        manifest_path,
        {
            "schema_version": SCHEMA_VERSION,
            "source": "국립국어원 우리말샘 Open API",
            "source_url": SERVICE_URL,
            "updated_at": now_utc_iso(),
            "status": status,
            "stop_reason": stop_reason,
            "search_method": "표제어 정확 일치",
            "candidates_path": project_relative_path(candidates_path),
            "candidate_count": len(candidates),
            "processed_count": len(rows),
            "remaining_count": len(candidates) - len(rows),
            "lookup_status_counts": dict(status_counts),
            "definition_category_counts": dict(category_counts),
            "api_request_count": baseline_api_requests + api_requests,
            "current_run_api_request_count": api_requests,
            "raw_response_count": sum(
                1
                for _ in (output_dir / RAW_RESPONSES_DIR_NAME).glob("*.json")
            ),
            "cached_request_count": cached_requests,
            "resumed_result_count": resumed_count,
            "error_count": len(error_rows),
            "elapsed_seconds": round(time.monotonic() - started_at, 1),
            "outputs": {
                "definitions": project_relative_path(results_path),
                "review_csv": project_relative_path(review_path),
                "not_found_terms": project_relative_path(not_found_path),
                "errors": project_relative_path(errors_path),
                "raw_responses": project_relative_path(
                    output_dir / RAW_RESPONSES_DIR_NAME
                ),
            },
            "output_sha256": {
                results_path.name: file_sha256(results_path),
                review_path.name: file_sha256(review_path),
                not_found_path.name: file_sha256(not_found_path),
                errors_path.name: file_sha256(errors_path),
            },
            "usage_notice": (
                "우리말샘의 정확 일치 뜻을 모두 보존한 수집 결과이며 자동 선택된 "
                "최종 법률 정의가 아니다. 법률 분야 표시는 검토 순서용이고, 판례 "
                "문맥과 맞지 않는 뜻은 쉬운요약 생성에 직접 사용하지 않는다."
            ),
        },
    )


def main() -> None:
    """정제된 판례 법률용어 전체를 우리말샘에서 수집한다."""
    args = parse_args()
    validate_args(args)
    candidates_path = args.candidates_path.resolve()
    output_dir = args.output_dir.resolve()
    raw_dir = output_dir / RAW_RESPONSES_DIR_NAME
    raw_dir.mkdir(parents=True, exist_ok=True)
    candidates = read_jsonl(candidates_path)
    if args.limit is not None:
        candidates = candidates[: args.limit]
    api_key = load_api_key()
    existing_rows = [] if args.refresh else read_jsonl(output_dir / RESULT_FILENAME)
    results_by_key = {row["match_key"]: row for row in existing_rows}
    resumed_count = len(results_by_key)
    existing_manifest_path = output_dir / MANIFEST_FILENAME
    existing_manifest = (
        json.loads(existing_manifest_path.read_text(encoding="utf-8"))
        if existing_manifest_path.exists() and not args.refresh
        else {}
    )
    baseline_api_requests = max(
        int(existing_manifest.get("api_request_count", 0)),
        sum(1 for _ in raw_dir.glob("*.json")),
    )
    api_requests = 0
    cached_requests = 0
    consecutive_errors = 0
    started_at = time.monotonic()
    status = "completed"
    stop_reason = None
    pending = [
        candidate
        for candidate in candidates
        if args.refresh
        or candidate["match_key"] not in results_by_key
        or results_by_key[candidate["match_key"]].get("lookup_status") == "error"
    ]

    try:
        for index, candidate in enumerate(pending, start=1):
            term = str(candidate["match_key"])
            errors: list[dict[str, str]] = []
            items: list[dict[str, Any]] = []
            source = ""
            try:
                payload, source = load_or_fetch_payload(
                    api_key=api_key,
                    term=term,
                    path=raw_response_path(raw_dir, term),
                    refresh=args.refresh,
                )
                items = extract_search_items(payload)
                api_requests += int(source == "api")
                cached_requests += int(source == "cache")
                consecutive_errors = 0
            except Exception as exc:
                errors.append(
                    {
                        "error_type": "request_or_parse_error",
                        "error": str(exc),
                    }
                )
                consecutive_errors += 1

            results_by_key[term] = build_result_row(candidate, items, errors)
            processed_now = index
            processed_total = sum(
                candidate_row["match_key"] in results_by_key
                for candidate_row in candidates
            )
            print(
                f"[{processed_total}/{len(candidates)}] {term}: "
                f"{results_by_key[term]['lookup_status']}, "
                f"뜻 {results_by_key[term]['definition_count']}건",
                flush=True,
            )
            if source == "api":
                time.sleep(args.delay)
            if consecutive_errors >= args.max_consecutive_errors:
                status = "stopped_on_errors"
                stop_reason = f"연속 API/파싱 오류 {consecutive_errors}회로 안전 중단"
            if (
                processed_now % args.checkpoint_interval == 0
                or processed_now == len(pending)
                or status != "completed"
            ):
                write_checkpoint(
                    output_dir=output_dir,
                    candidates_path=candidates_path,
                    candidates=candidates,
                    results_by_key=results_by_key,
                    api_requests=api_requests,
                    baseline_api_requests=baseline_api_requests,
                    cached_requests=cached_requests,
                    resumed_count=resumed_count,
                    started_at=started_at,
                    status="collecting" if status == "completed" else status,
                    stop_reason=stop_reason,
                )
            if status != "completed":
                break
    except KeyboardInterrupt:
        status = "interrupted"
        stop_reason = "사용자 중단"
    finally:
        if status == "completed" and any(
            row.get("lookup_status") == "error" for row in results_by_key.values()
        ):
            status = "completed_with_errors"
        write_checkpoint(
            output_dir=output_dir,
            candidates_path=candidates_path,
            candidates=candidates,
            results_by_key=results_by_key,
            api_requests=api_requests,
            baseline_api_requests=baseline_api_requests,
            cached_requests=cached_requests,
            resumed_count=resumed_count,
            started_at=started_at,
            status=status,
            stop_reason=stop_reason,
        )

    print(
        f"완료 상태 {status}: 후보 {len(candidates):,}개, "
        f"처리 {len(results_by_key):,}개, API {api_requests:,}회, "
        f"캐시 {cached_requests:,}회",
        flush=True,
    )


if __name__ == "__main__":
    main()
