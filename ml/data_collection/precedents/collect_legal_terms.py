# collect_legal_terms.py
"""
Description: 국가법령정보센터의 공식 법령용어 목록을 페이지별 원본과
통합 JSONL로 수집하고, 중단 후 재실행 가능한 manifest를 관리한다.
Author: choeminju
Date: 2026-09-20
Before:
    - LAW_API_KEY가 설정되어 있고 공식 법령용어 후보 목록이 없는 상태.

After:
    - local_data/precedents/legal_terms/official_catalog/에 원본 페이지, 통합 목록, manifest가 생성.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml.data_collection.statutes.law_api_common import (  # noqa: E402
    fetch_json,
    load_law_api_key,
    read_json,
    redact_secret,
    write_json,
)


SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT
    / "local_data"
    / "precedents"
    / "legal_terms"
    / "official_catalog"
)
RAW_PAGES_DIR_NAME = "raw_pages"
CATALOG_FILENAME = "official_legal_terms.jsonl"
MANIFEST_FILENAME = "manifest.json"
API_TARGET = "lstrm"
API_RESPONSE_TYPE = "JSON"
DEFAULT_PAGE_SIZE = 100
DEFAULT_DELAY_SECONDS = 0.2
SCHEMA_VERSION = "precedent_legal_term_catalog.v0.1"


def now_utc_iso() -> str:
    """현재 UTC 시각을 ISO 문자열로 반환한다."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_args() -> argparse.Namespace:
    """법령용어 목록 수집 옵션을 정의한다."""
    parser = argparse.ArgumentParser(
        description="국가법령정보센터의 공식 법령용어 목록을 수집합니다."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="페이지 원본, 통합 JSONL, manifest를 저장할 폴더.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help="페이지당 요청 건수. 국가법령정보센터 최대값은 100.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="API 요청 사이 대기 시간(초).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="테스트용 최대 수집 페이지 수. 전체 수집 시 생략한다.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="저장된 페이지 원본을 사용하지 않고 API에서 다시 받는다.",
    )
    parser.add_argument(
        "--allow-test-key",
        action="store_true",
        help="LAW_API_KEY가 없을 때 공식 샘플 인증값 test를 사용한다.",
    )
    return parser.parse_args()


def validate_page_size(page_size: int) -> None:
    """API가 허용하는 페이지 크기인지 확인한다."""
    if not 1 <= page_size <= 100:
        raise ValueError("page-size는 1 이상 100 이하여야 합니다.")


def get_search_root(payload: dict[str, Any]) -> dict[str, Any]:
    """법령용어 검색 응답의 실제 결과 객체를 반환한다."""
    root = payload.get("LsTrmSearch")
    if not isinstance(root, dict):
        raise ValueError("응답에 LsTrmSearch 객체가 없습니다.")
    result_code = str(root.get("resultCode", "")).strip()
    if result_code and result_code != "00":
        raise ValueError(
            f"법령용어 API 오류: {result_code} {root.get('resultMsg', '')}".strip()
        )
    return root


def extract_total_count(payload: dict[str, Any]) -> int:
    """법령용어 검색 응답에서 전체 건수를 읽는다."""
    root = get_search_root(payload)
    try:
        return int(root.get("totalCnt") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("법령용어 API totalCnt가 정수가 아닙니다.") from exc


def extract_terms(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """법령용어 검색 응답의 단일 객체 또는 배열을 목록으로 정규화한다."""
    value = get_search_root(payload).get("lstrm")
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def normalize_term(item: dict[str, Any]) -> dict[str, Any]:
    """공식 응답 필드를 내부 용어 목록 스키마로 변환한다."""
    term_id = str(item.get("법령용어ID") or item.get("id") or "").strip()
    term = str(item.get("법령용어명") or "").strip()
    if not term_id or not term:
        raise ValueError(f"법령용어 식별자 또는 이름이 없습니다: {item}")
    return {
        "term_id": term_id,
        "source_term_ids": [value.strip() for value in term_id.split(",")],
        "source_result_id": str(item.get("id") or "").strip(),
        "term": term,
        "dictionary_type_code": str(item.get("사전구분코드") or "").strip(),
        "law_type_code": str(item.get("법령종류코드") or "").strip(),
        "detail_search": str(item.get("법령용어상세검색") or "").strip(),
        "detail_url": str(item.get("법령용어상세링크") or "").strip(),
    }


def redact_payload_secrets(value: Any, secret: str) -> Any:
    """API 응답 내부 문자열에서 인증값을 제거한다."""
    if isinstance(value, dict):
        return {
            key: redact_payload_secrets(item, secret)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_payload_secrets(item, secret) for item in value]
    if isinstance(value, str):
        return redact_secret(value, secret)
    return value


def page_path(raw_pages_dir: Path, page: int) -> Path:
    """페이지 번호에 대응하는 원본 JSON 경로를 만든다."""
    return raw_pages_dir / f"page_{page:04d}.json"


def fetch_page(api_key: str, page: int, page_size: int) -> dict[str, Any]:
    """공식 API에서 법령용어 목록 한 페이지를 받는다."""
    return fetch_json(
        SEARCH_URL,
        {
            "OC": api_key,
            "target": API_TARGET,
            "type": API_RESPONSE_TYPE,
            "display": page_size,
            "page": page,
            "sort": "lasc",
        },
    )


def load_or_fetch_page(
    api_key: str,
    path: Path,
    page: int,
    page_size: int,
    refresh: bool,
) -> tuple[dict[str, Any], str]:
    """저장된 페이지를 재사용하거나 API에서 새로 받아 원본을 저장한다."""
    if path.exists() and not refresh:
        payload = read_json(path)
        redacted_payload = redact_payload_secrets(payload, api_key)
        if redacted_payload != payload:
            write_json(path, redacted_payload)
        return redacted_payload, "cache"
    payload = redact_payload_secrets(fetch_page(api_key, page, page_size), api_key)
    write_json(path, payload)
    return payload, "api"


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """통합 용어 목록을 UTF-8 JSONL로 원자적으로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary_path.replace(path)


def file_sha256(path: Path) -> str:
    """파일의 SHA-256 해시를 계산한다."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_catalog(raw_pages_dir: Path, collected_pages: list[int]) -> list[dict[str, Any]]:
    """수집된 페이지 원본을 하나의 중복 제거된 용어 목록으로 합친다."""
    by_record: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for page in collected_pages:
        payload = read_json(page_path(raw_pages_dir, page))
        for item in extract_terms(payload):
            normalized = normalize_term(item)
            record_key = (
                normalized["term_id"],
                normalized["term"],
                normalized["dictionary_type_code"],
                normalized["law_type_code"],
            )
            by_record.setdefault(record_key, normalized)
    return sorted(
        by_record.values(),
        key=lambda row: (row["term"], row["term_id"], row["source_result_id"]),
    )


def write_manifest(
    path: Path,
    *,
    total_count: int,
    page_size: int,
    expected_pages: int,
    requested_pages: int,
    collected_pages: list[int],
    api_pages: int,
    cached_pages: int,
    catalog_path: Path | None,
    catalog: list[dict[str, Any]],
    status: str,
    error: str | None = None,
) -> None:
    """현재 수집 진행 상태와 통합 목록 통계를 manifest에 기록한다."""
    dictionary_counts = Counter(row["dictionary_type_code"] for row in catalog)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source": "국가법령정보센터 법령용어 목록 API",
        "source_url": SEARCH_URL,
        "api_target": API_TARGET,
        "collected_at": now_utc_iso(),
        "status": status,
        "total_count_reported_by_api": total_count,
        "page_size": page_size,
        "expected_pages": expected_pages,
        "requested_pages": requested_pages,
        "collected_page_count": len(collected_pages),
        "last_collected_page": max(collected_pages, default=0),
        "api_page_count": api_pages,
        "cached_page_count": cached_pages,
        "catalog_record_count": len(catalog),
        "dictionary_type_counts": dict(sorted(dictionary_counts.items())),
        "catalog_path": (
            str(catalog_path.relative_to(PROJECT_ROOT))
            if catalog_path and catalog_path.is_relative_to(PROJECT_ROOT)
            else str(catalog_path) if catalog_path else None
        ),
        "catalog_sha256": (
            file_sha256(catalog_path) if catalog_path and catalog_path.exists() else None
        ),
        "error": error,
    }
    write_json(path, payload)


def main() -> None:
    """공식 법령용어 목록을 페이지 단위로 수집하고 통합한다."""
    args = parse_args()
    validate_page_size(args.page_size)
    output_dir = args.output_dir.resolve()
    raw_pages_dir = output_dir / RAW_PAGES_DIR_NAME
    catalog_path = output_dir / CATALOG_FILENAME
    manifest_path = output_dir / MANIFEST_FILENAME
    raw_pages_dir.mkdir(parents=True, exist_ok=True)

    api_key = "test" if args.allow_test_key else load_law_api_key()
    first_path = page_path(raw_pages_dir, 1)
    first_payload, first_source = load_or_fetch_page(
        api_key,
        first_path,
        1,
        args.page_size,
        args.refresh,
    )
    total_count = extract_total_count(first_payload)
    expected_pages = math.ceil(total_count / args.page_size) if total_count else 0
    requested_pages = min(expected_pages, args.max_pages or expected_pages)
    collected_pages = [1] if requested_pages else []
    api_pages = int(first_source == "api")
    cached_pages = int(first_source == "cache")
    started_at = time.monotonic()

    try:
        for page in range(2, requested_pages + 1):
            path = page_path(raw_pages_dir, page)
            _, source = load_or_fetch_page(
                api_key,
                path,
                page,
                args.page_size,
                args.refresh,
            )
            collected_pages.append(page)
            api_pages += int(source == "api")
            cached_pages += int(source == "cache")
            if page % 10 == 0 or page == requested_pages:
                elapsed = time.monotonic() - started_at
                print(
                    f"진행 {page}/{requested_pages}페이지 "
                    f"API {api_pages} 캐시 {cached_pages} 경과 {elapsed:.1f}초",
                    flush=True,
                )
                partial_catalog = build_catalog(raw_pages_dir, collected_pages)
                write_manifest(
                    manifest_path,
                    total_count=total_count,
                    page_size=args.page_size,
                    expected_pages=expected_pages,
                    requested_pages=requested_pages,
                    collected_pages=collected_pages,
                    api_pages=api_pages,
                    cached_pages=cached_pages,
                    catalog_path=None,
                    catalog=partial_catalog,
                    status="collecting",
                )
            if source == "api" and page < requested_pages:
                time.sleep(max(args.delay, 0))
    except KeyboardInterrupt:
        partial_catalog = build_catalog(raw_pages_dir, collected_pages)
        write_manifest(
            manifest_path,
            total_count=total_count,
            page_size=args.page_size,
            expected_pages=expected_pages,
            requested_pages=requested_pages,
            collected_pages=collected_pages,
            api_pages=api_pages,
            cached_pages=cached_pages,
            catalog_path=None,
            catalog=partial_catalog,
            status="interrupted",
        )
        print("사용자 중단: 저장된 페이지부터 다시 실행하면 이어서 수집합니다.")
        return
    except Exception as exc:
        partial_catalog = build_catalog(raw_pages_dir, collected_pages)
        write_manifest(
            manifest_path,
            total_count=total_count,
            page_size=args.page_size,
            expected_pages=expected_pages,
            requested_pages=requested_pages,
            collected_pages=collected_pages,
            api_pages=api_pages,
            cached_pages=cached_pages,
            catalog_path=None,
            catalog=partial_catalog,
            status="failed",
            error=redact_secret(str(exc), api_key),
        )
        raise

    catalog = build_catalog(raw_pages_dir, collected_pages)
    write_jsonl(catalog_path, catalog)
    status = "completed" if requested_pages == expected_pages else "sample_completed"
    write_manifest(
        manifest_path,
        total_count=total_count,
        page_size=args.page_size,
        expected_pages=expected_pages,
        requested_pages=requested_pages,
        collected_pages=collected_pages,
        api_pages=api_pages,
        cached_pages=cached_pages,
        catalog_path=catalog_path,
        catalog=catalog,
        status=status,
    )
    print(f"완료: {len(catalog):,}건, 상태 {status}, 저장 {catalog_path}")


if __name__ == "__main__":
    main()
