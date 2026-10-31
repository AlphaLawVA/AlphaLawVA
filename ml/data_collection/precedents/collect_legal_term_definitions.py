# collect_legal_term_definitions.py
"""
Description: 정제된 판례 법률용어 후보의 국가법령정보센터 공식 정의를
ID 묶음 단위로 수집하고, 중단 후 재실행 가능한 캐시와 manifest를 관리한다.
Author: choeminju
Date: 2026-09-21
Before:
    - 판례 노출 필드와 매칭한 공식 법률용어 후보가 정제되어 있는 상태.

After:
    - local_data/precedents/legal_terms/official_definitions/에 API 원본 응답,
      용어별 공식 정의, 오류 목록과 manifest가 생성.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml.data_collection.precedents.collect_legal_terms import (  # noqa: E402
    redact_payload_secrets,
)
from ml.data_collection.statutes.law_api_common import (  # noqa: E402
    fetch_json,
    load_law_api_key,
    read_json,
    redact_secret,
    write_json,
)


SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"
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
    / "official_definitions"
)
RAW_RESPONSES_DIR_NAME = "raw_responses"
DEFINITIONS_FILENAME = "official_term_definitions.jsonl"
ERRORS_FILENAME = "errors.jsonl"
MANIFEST_FILENAME = "manifest.json"
API_TARGET = "lstrm"
API_RESPONSE_TYPE = "JSON"
DEFINITION_DICTIONARY_CODE = "011402"
DEFAULT_IDS_PER_REQUEST = 50
DEFAULT_DELAY_SECONDS = 0.2
DEFAULT_CHECKPOINT_INTERVAL = 25
DEFAULT_MAX_CONSECUTIVE_ERRORS = 5
SCHEMA_VERSION = "precedent_legal_term_definitions.v0.1"
HTML_TAG_RE = re.compile(r"<[^>]+>")
BREAK_TAG_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"[^\S\r\n]+")


class DefinitionNotFoundError(ValueError):
    """목록에는 있지만 상세 API에서 찾을 수 없는 공식 용어 ID 오류."""


def parse_args() -> argparse.Namespace:
    """공식 법률용어 정의 수집 옵션을 정의한다."""
    parser = argparse.ArgumentParser(
        description="정제된 판례 법률용어 후보의 공식 정의를 수집합니다."
    )
    parser.add_argument(
        "--candidates-path",
        type=Path,
        default=DEFAULT_CANDIDATES_PATH,
        help="정제된 법률용어 후보 JSONL 경로.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="원본 응답, 정의 JSONL, 오류와 manifest 저장 폴더.",
    )
    parser.add_argument(
        "--ids-per-request",
        type=int,
        default=DEFAULT_IDS_PER_REQUEST,
        help="한 요청에 묶을 공식 용어 ID 개수.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="실제 API 요청 사이 대기 시간(초).",
    )
    parser.add_argument(
        "--checkpoint-interval",
        type=int,
        default=DEFAULT_CHECKPOINT_INTERVAL,
        help="중간 결과와 manifest를 다시 쓰는 용어 그룹 간격.",
    )
    parser.add_argument(
        "--max-consecutive-errors",
        type=int,
        default=DEFAULT_MAX_CONSECUTIVE_ERRORS,
        help="연속 오류로 수집을 안전 중단할 기준.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="테스트용 후보 용어 그룹 개수 제한.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="저장된 원본 응답을 재사용하지 않고 다시 요청.",
    )
    parser.add_argument(
        "--allow-test-key",
        action="store_true",
        help="LAW_API_KEY가 없을 때 공식 샘플 인증값 test를 사용.",
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


def validate_positive(value: int, name: str) -> None:
    """양수여야 하는 실행 옵션을 검증한다."""
    if value <= 0:
        raise ValueError(f"{name}은 1 이상이어야 합니다.")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 객체 목록을 읽는다."""
    rows = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL {line_number}번째 줄이 객체가 아닙니다: {path}")
            rows.append(value)
    return rows


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


def chunked(values: list[str], size: int) -> list[list[str]]:
    """공식 용어 ID를 요청 크기에 맞춰 나눈다."""
    return [values[index : index + size] for index in range(0, len(values), size)]


def request_cache_path(raw_dir: Path, source_term_ids: list[str]) -> Path:
    """요청 ID 조합에 대응하는 안정적인 캐시 경로를 만든다."""
    signature = hashlib.sha256(",".join(source_term_ids).encode()).hexdigest()[:24]
    return raw_dir / f"detail_{signature}.json"


def fetch_definition_payload(api_key: str, source_term_ids: list[str]) -> dict[str, Any]:
    """공식 API에서 용어 ID 묶음의 상세 정보를 조회한다."""
    return fetch_json(
        SERVICE_URL,
        {
            "OC": api_key,
            "target": API_TARGET,
            "type": API_RESPONSE_TYPE,
            "trmSeqs": ",".join(source_term_ids),
        },
    )


def load_or_fetch_response(
    api_key: str,
    path: Path,
    source_term_ids: list[str],
    refresh: bool,
) -> tuple[dict[str, Any], str]:
    """ID 묶음 원본을 재사용하거나 API에서 받아 인증값 없이 저장한다."""
    if path.exists() and not refresh:
        wrapper = read_json(path)
        payload = wrapper.get("payload")
        if not isinstance(payload, dict):
            raise ValueError(f"캐시 payload가 객체가 아닙니다: {path}")
        sanitized = redact_payload_secrets(payload, api_key)
        if sanitized != payload:
            wrapper["payload"] = sanitized
            write_json(path, wrapper)
        return sanitized, "cache"

    payload = redact_payload_secrets(
        fetch_definition_payload(api_key, source_term_ids),
        api_key,
    )
    write_json(
        path,
        {
            "requested_source_term_ids": source_term_ids,
            "collected_at": now_utc_iso(),
            "payload": payload,
        },
    )
    return payload, "api"


def as_column_values(value: Any, length: int) -> list[Any]:
    """단일 값과 배열이 섞인 API 열을 행 수에 맞춘다."""
    if isinstance(value, list):
        return value + [""] * max(0, length - len(value))
    return [value] * length


def extract_service_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """열 지향 법령용어 상세 응답을 행 목록으로 변환한다."""
    root = payload.get("LsTrmService")
    if not isinstance(root, dict):
        message = str(payload.get("Law") or "").strip()
        if "일치하는 법령용어가 없습니다" in message:
            raise DefinitionNotFoundError(message)
        raise ValueError("응답에 LsTrmService 객체가 없습니다.")
    length = max(
        (len(value) if isinstance(value, list) else 1 for value in root.values()),
        default=0,
    )
    if not length:
        return []
    columns = {key: as_column_values(value, length) for key, value in root.items()}
    return [
        {key: values[index] for key, values in columns.items()}
        for index in range(length)
    ]


def clean_definition_text(value: Any) -> str:
    """공식 정의의 HTML 표시만 제거하고 문장과 줄바꿈을 보존한다."""
    text = BREAK_TAG_RE.sub("\n", str(value or ""))
    text = html.unescape(HTML_TAG_RE.sub(" ", text))
    lines = [WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def normalize_definition_entry(item: dict[str, Any]) -> dict[str, Any]:
    """공식 상세 응답 한 건을 내부 정의 스키마로 변환한다."""
    return {
        "source_term_id": str(item.get("법령용어일련번호") or "").strip(),
        "term_korean": str(item.get("법령용어명_한글") or "").strip(),
        "term_hanja": str(item.get("법령용어명_한자") or "").strip(),
        "dictionary_type_code": str(item.get("법령용어코드") or "").strip(),
        "dictionary_type_name": str(item.get("법령용어코드명") or "").strip(),
        "definition": clean_definition_text(item.get("법령용어정의")),
        "source": clean_definition_text(item.get("출처")),
    }


def merge_identical_definitions(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """정의 문장이 완전히 같은 공식 항목만 하나로 묶고 출처는 모두 보존한다."""
    grouped: dict[str, dict[str, set[str]]] = {}
    for entry in entries:
        definition = entry["definition"]
        if not definition:
            continue
        group = grouped.setdefault(
            definition,
            {
                "source_term_ids": set(),
                "term_korean_variants": set(),
                "term_hanja_variants": set(),
                "dictionary_type_codes": set(),
                "dictionary_type_names": set(),
                "sources": set(),
            },
        )
        mappings = (
            ("source_term_id", "source_term_ids"),
            ("term_korean", "term_korean_variants"),
            ("term_hanja", "term_hanja_variants"),
            ("dictionary_type_code", "dictionary_type_codes"),
            ("dictionary_type_name", "dictionary_type_names"),
            ("source", "sources"),
        )
        for source_key, target_key in mappings:
            value = entry[source_key]
            if value:
                group[target_key].add(value)

    rows = []
    for definition, group in grouped.items():
        rows.append(
            {
                "definition": definition,
                **{key: sorted(values) for key, values in group.items()},
            }
        )
    return sorted(rows, key=lambda row: (row["definition"], row["source_term_ids"]))


def build_definition_row(
    candidate: dict[str, Any],
    entries: list[dict[str, Any]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    """후보 용어 하나의 공식 정의와 수집 상태를 구성한다."""
    official_entries = [
        entry
        for entry in entries
        if entry["dictionary_type_code"] == DEFINITION_DICTIONARY_CODE
    ]
    definitions = merge_identical_definitions(official_entries)
    expected = DEFINITION_DICTIONARY_CODE in candidate.get("dictionary_type_codes", [])
    fatal_errors = [
        error for error in errors if error.get("error_type") != "not_found"
    ]
    if definitions:
        status = "defined"
    elif fatal_errors:
        status = "error"
    elif expected:
        status = "no_definition_returned"
    else:
        status = "no_official_definition_type"
    return {
        "match_key": candidate["match_key"],
        "term": candidate["term"],
        "official_variants": candidate.get("official_variants", []),
        "candidate_source_term_ids": candidate.get("source_term_ids", []),
        "candidate_dictionary_type_codes": candidate.get(
            "dictionary_type_codes", []
        ),
        "definition_status": status,
        "official_definitions": definitions,
        "definition_count": len(definitions),
        "collection_errors": errors,
    }


def write_checkpoint(
    *,
    output_dir: Path,
    candidates_path: Path,
    candidates: list[dict[str, Any]],
    entries_by_key: dict[str, list[dict[str, Any]]],
    errors_by_key: dict[str, list[dict[str, str]]],
    processed_keys: set[str],
    status: str,
    api_requests: int,
    cached_requests: int,
    started_at: float,
    stop_reason: str | None = None,
) -> None:
    """현재까지의 정의 결과, 오류와 manifest를 원자적으로 저장한다."""
    definitions_path = output_dir / DEFINITIONS_FILENAME
    errors_path = output_dir / ERRORS_FILENAME
    manifest_path = output_dir / MANIFEST_FILENAME
    rows = [
        build_definition_row(
            candidate,
            entries_by_key.get(candidate["match_key"], []),
            errors_by_key.get(candidate["match_key"], []),
        )
        for candidate in candidates
        if candidate["match_key"] in processed_keys
        or DEFINITION_DICTIONARY_CODE
        not in candidate.get("dictionary_type_codes", [])
    ]
    write_jsonl(definitions_path, rows)
    error_rows = [
        {"match_key": match_key, **error}
        for match_key, errors in errors_by_key.items()
        for error in errors
    ]
    write_jsonl(errors_path, error_rows)
    status_counts = Counter(row["definition_status"] for row in rows)
    definition_counts = Counter(
        definition["dictionary_type_codes"][0]
        for row in rows
        for definition in row["official_definitions"]
        if definition["dictionary_type_codes"]
    )
    error_type_counts = Counter(
        row.get("error_type", "unknown") for row in error_rows
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source": "국가법령정보센터 법령용어 본문 조회 API",
        "source_url": SERVICE_URL,
        "api_target": API_TARGET,
        "definition_dictionary_code": DEFINITION_DICTIONARY_CODE,
        "updated_at": now_utc_iso(),
        "status": status,
        "stop_reason": stop_reason,
        "candidates_path": project_relative_path(candidates_path),
        "candidate_count": len(candidates),
        "definition_target_count": sum(
            DEFINITION_DICTIONARY_CODE in row.get("dictionary_type_codes", [])
            for row in candidates
        ),
        "processed_definition_target_count": len(processed_keys),
        "result_row_count": len(rows),
        "status_counts": dict(status_counts),
        "definition_dictionary_counts": dict(definition_counts),
        "api_request_count": api_requests,
        "cached_request_count": cached_requests,
        "error_count": len(error_rows),
        "error_type_counts": dict(error_type_counts),
        "elapsed_seconds": round(time.monotonic() - started_at, 1),
        "outputs": {
            "definitions": project_relative_path(definitions_path),
            "errors": project_relative_path(errors_path),
        },
        "output_sha256": {
            definitions_path.name: file_sha256(definitions_path),
            errors_path.name: file_sha256(errors_path),
        },
        "usage_notice": (
            "법령정의사전의 동일 표기 용어에는 서로 다른 법령 문맥의 정의가 함께 "
            "포함될 수 있으므로 판례 문맥에 맞는 정의를 선택하기 전에는 생성 프롬프트에 "
            "직접 주입하지 않는다."
        ),
    }
    write_json(manifest_path, manifest)


def main() -> None:
    """정제된 판례 법률용어 후보의 공식 정의를 수집한다."""
    args = parse_args()
    validate_positive(args.ids_per_request, "ids-per-request")
    validate_positive(args.checkpoint_interval, "checkpoint-interval")
    validate_positive(args.max_consecutive_errors, "max-consecutive-errors")
    candidates_path = args.candidates_path.resolve()
    output_dir = args.output_dir.resolve()
    raw_dir = output_dir / RAW_RESPONSES_DIR_NAME
    raw_dir.mkdir(parents=True, exist_ok=True)
    candidates = read_jsonl(candidates_path)
    if args.limit is not None:
        candidates = candidates[: args.limit]
    api_key = "test" if args.allow_test_key else load_law_api_key()
    entries_by_key: dict[str, list[dict[str, Any]]] = {}
    errors_by_key: dict[str, list[dict[str, str]]] = {}
    processed_keys: set[str] = set()
    api_requests = 0
    cached_requests = 0
    consecutive_errors = 0
    started_at = time.monotonic()
    targets = [
        row
        for row in candidates
        if DEFINITION_DICTIONARY_CODE in row.get("dictionary_type_codes", [])
    ]
    status = "completed"
    stop_reason = None

    try:
        for index, candidate in enumerate(targets, start=1):
            match_key = candidate["match_key"]
            source_term_ids = sorted(
                {str(value) for value in candidate.get("source_term_ids", []) if value},
                key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value),
            )
            group_entries: list[dict[str, Any]] = []
            group_errors: list[dict[str, str]] = []
            request_queue = chunked(source_term_ids, args.ids_per_request)
            while request_queue:
                request_ids = request_queue.pop(0)
                path = request_cache_path(raw_dir, request_ids)
                try:
                    payload, source = load_or_fetch_response(
                        api_key,
                        path,
                        request_ids,
                        args.refresh,
                    )
                    api_requests += int(source == "api")
                    cached_requests += int(source == "cache")
                    group_entries.extend(
                        normalize_definition_entry(item)
                        for item in extract_service_entries(payload)
                    )
                    consecutive_errors = 0
                    if source == "api":
                        time.sleep(max(args.delay, 0))
                except Exception as exc:
                    if len(request_ids) > 1 and isinstance(
                        exc, (DefinitionNotFoundError, ValueError)
                    ):
                        middle = len(request_ids) // 2
                        request_queue[0:0] = [
                            request_ids[:middle],
                            request_ids[middle:],
                        ]
                        continue
                    message = redact_secret(str(exc), api_key)
                    group_errors.append(
                        {
                            "requested_source_term_ids": ",".join(request_ids),
                            "error_type": (
                                "not_found"
                                if isinstance(exc, DefinitionNotFoundError)
                                else "request_or_parse_error"
                            ),
                            "error": message,
                        }
                    )
                    if isinstance(exc, DefinitionNotFoundError):
                        consecutive_errors = 0
                    else:
                        consecutive_errors += 1
                    if consecutive_errors >= args.max_consecutive_errors:
                        status = "stopped_on_errors"
                        stop_reason = (
                            f"연속 API/파싱 오류 {consecutive_errors}회로 안전 중단"
                        )
                        break

            entries_by_key[match_key] = group_entries
            errors_by_key[match_key] = group_errors
            processed_keys.add(match_key)
            if index % args.checkpoint_interval == 0 or index == len(targets):
                elapsed = time.monotonic() - started_at
                average = elapsed / index
                remaining = average * (len(targets) - index)
                print(
                    f"진행 {index}/{len(targets)} 용어 "
                    f"API {api_requests} 캐시 {cached_requests} "
                    f"오류 {sum(map(len, errors_by_key.values()))} "
                    f"경과 {elapsed:.1f}초 예상남음 {remaining:.1f}초",
                    flush=True,
                )
                write_checkpoint(
                    output_dir=output_dir,
                    candidates_path=candidates_path,
                    candidates=candidates,
                    entries_by_key=entries_by_key,
                    errors_by_key=errors_by_key,
                    processed_keys=processed_keys,
                    status="collecting" if status == "completed" else status,
                    api_requests=api_requests,
                    cached_requests=cached_requests,
                    started_at=started_at,
                    stop_reason=stop_reason,
                )
            if status != "completed":
                break
    except KeyboardInterrupt:
        status = "interrupted"
        stop_reason = "사용자 중단"
    finally:
        write_checkpoint(
            output_dir=output_dir,
            candidates_path=candidates_path,
            candidates=candidates,
            entries_by_key=entries_by_key,
            errors_by_key=errors_by_key,
            processed_keys=processed_keys,
            status=status,
            api_requests=api_requests,
            cached_requests=cached_requests,
            started_at=started_at,
            stop_reason=stop_reason,
        )

    print(
        f"완료 상태 {status}: 후보 {len(candidates):,}개, "
        f"정의 대상 {len(targets):,}개, 처리 {len(processed_keys):,}개, "
        f"API {api_requests:,}회, 캐시 {cached_requests:,}회",
        flush=True,
    )


if __name__ == "__main__":
    main()
