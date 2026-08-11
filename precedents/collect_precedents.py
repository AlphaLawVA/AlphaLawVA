#!/usr/bin/env python3
"""국가법령정보 공동활용 API 판례 원본 수집기.

키워드로 판례 목록을 조회하고, 목록에서 얻은 판례일련번호로 본문조회 API를
호출해 raw JSON을 local_data/precedents 아래에 저장한다.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from precedent_config import (
    API_RESPONSE_TYPE,
    API_TARGET,
    CANDIDATES_PATH,
    COLLECTION_MANIFEST_PATH,
    DATA_SOURCE_NAME,
    DEFAULT_DELAY_SECONDS,
    DEFAULT_DISPLAY,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    DETAIL_URL,
    RAW_DETAILS_DIR,
    RAW_SEARCHES_DIR,
    RETRY_QUEUE_PATH,
    SEARCH_URL,
    USER_AGENT,
    ensure_collection_dirs,
    get_law_api_key,
    now_utc_iso,
)
from precedent_keywords import get_collection_queries


@dataclass
class CollectionStats:
    """이번 실행에서 쌓이는 수집 통계."""

    started_at: str
    finished_at: str | None = None
    query_count: int = 0
    list_request_count: int = 0
    list_cached_page_count: int = 0
    list_item_count: int = 0
    unique_candidate_count: int = 0
    detail_success_count: int = 0
    detail_skipped_existing_count: int = 0
    detail_failure_count: int = 0
    interrupted: bool = False
    searches: list[dict[str, Any]] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    """커맨드라인 옵션을 정의한다."""
    parser = argparse.ArgumentParser(description="Collect raw precedent JSON files.")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run a tiny collection check: 1 case-name query, 1 body query, about 10 details.",
    )
    parser.add_argument(
        "--allow-test-key",
        action="store_true",
        help="Use official sample OC=test when LAW_API_KEY is empty. Format checks only.",
    )
    parser.add_argument(
        "--display",
        type=int,
        default=DEFAULT_DISPLAY,
        help="List results per page. Open Law API max is 100.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="Delay seconds between API requests.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="HTTP timeout seconds.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help="Maximum retries for timeout, 429, and 5xx responses.",
    )
    parser.add_argument(
        "--overwrite-details",
        action="store_true",
        help="Refetch detail JSON even when local file already exists.",
    )
    parser.add_argument(
        "--overwrite-searches",
        action="store_true",
        help="Refetch search page JSON even when local file already exists.",
    )
    return parser.parse_args()


def ensure_list(value: Any) -> list[Any]:
    """API가 1건은 객체, 여러 건은 배열로 줄 수 있어서 항상 리스트로 맞춘다."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def slugify(value: str) -> str:
    """검색어를 파일 경로에 안전한 이름으로 바꾼다."""
    cleaned = re.sub(r"\s+", "_", value.strip())
    return re.sub(r"[^0-9A-Za-z가-힣_.-]", "", cleaned) or "query"


def mask_oc_in_text(value: str) -> str:
    """저장 데이터 안에 OC 값이 들어오면 노출되지 않게 가린다."""
    if "OC=" not in value and "OC%3D" not in value:
        return value

    parsed = urlparse(value)
    if not parsed.query:
        return value

    query_pairs = [
        (key, "***" if key.upper() == "OC" else item_value)
        for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunparse(parsed._replace(query=urlencode(query_pairs)))


def mask_secrets(value: Any) -> Any:
    """JSON 저장 전 API 키처럼 보이는 OC 파라미터를 재귀적으로 제거한다."""
    if isinstance(value, dict):
        return {key: mask_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [mask_secrets(item) for item in value]
    if isinstance(value, str):
        return mask_oc_in_text(value)
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """딕셔너리를 UTF-8 JSON 파일로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(mask_secrets(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_json(path: Path) -> dict[str, Any]:
    """저장된 UTF-8 JSON 파일을 다시 읽는다."""
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    """JSONL 파일에 한 줄짜리 JSON 객체를 추가한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(mask_secrets(payload), ensure_ascii=False) + "\n")


def request_json(
    params: dict[str, Any],
    url: str,
    timeout: float,
    max_retries: int,
) -> dict[str, Any]:
    """API를 호출하고 일시 오류는 지수 백오프로 재시도한다."""
    safe_params = {key: value for key, value in params.items() if key != "OC"}
    encoded = urlencode(params)
    request_url = f"{url}?{encoded}"

    for attempt in range(max_retries + 1):
        request = Request(request_url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
            return json.loads(body)
        except HTTPError as exc:
            should_retry = exc.code == 429 or 500 <= exc.code < 600
            if not should_retry or attempt >= max_retries:
                raise RuntimeError(
                    f"HTTP {exc.code} response for params={safe_params}"
                ) from exc
        except URLError as exc:
            if attempt >= max_retries:
                raise RuntimeError(
                    f"Network error for params={safe_params}: {exc.reason}"
                ) from exc
        except json.JSONDecodeError as exc:
            preview = body[:300].replace("\n", " ") if "body" in locals() else ""
            raise RuntimeError(
                f"JSON parse error for params={safe_params}; preview={preview}"
            ) from exc

        time.sleep(min(2**attempt, 8))

    raise RuntimeError(f"Request failed for params={safe_params}")


def parse_total_count(search_payload: dict[str, Any]) -> int:
    """목록 응답의 totalCnt 값을 정수로 변환한다."""
    raw_total = search_payload.get("totalCnt") or search_payload.get("totalcnt") or 0
    try:
        return int(raw_total)
    except (TypeError, ValueError):
        return 0


def extract_precedent_id(item: dict[str, Any]) -> str | None:
    """목록 응답에서 판례일련번호를 뽑는다."""
    value = item.get("판례일련번호")
    if value is None:
        return None
    precedent_id = str(value).strip()
    return precedent_id or None


def collect_search_pages(
    api_key: str,
    search_spec: dict[str, Any],
    args: argparse.Namespace,
    stats: CollectionStats,
) -> list[dict[str, Any]]:
    """검색어 하나에 대해 목록 API 전체 페이지를 수집한다."""
    query = str(search_spec["query"])
    search = int(search_spec["search"])
    search_type = str(search_spec["search_type"])
    query_slug = slugify(query)
    page = 1
    items: list[dict[str, Any]] = []
    search_record = {
        "query": query,
        "search": search,
        "search_type": search_type,
        "pages": [],
        "item_count": 0,
    }

    while True:
        raw_path = (
            RAW_SEARCHES_DIR
            / search_type
            / query_slug
            / f"page_{page:04d}.json"
        )
        params = {
            "OC": api_key,
            "target": API_TARGET,
            "type": API_RESPONSE_TYPE,
            "search": search,
            "query": query,
            "display": args.display,
            "page": page,
            "datSrcNm": DATA_SOURCE_NAME,
        }

        if raw_path.exists() and not args.overwrite_searches:
            saved_payload = read_json(raw_path)
            response = saved_payload.get("response", {})
            fetched_at = saved_payload.get("fetched_at")
            loaded_from_cache = True
            stats.list_cached_page_count += 1
        else:
            response = request_json(params, SEARCH_URL, args.timeout, args.max_retries)
            fetched_at = now_utc_iso()
            loaded_from_cache = False
            write_json(
                raw_path,
                {
                    "fetched_at": fetched_at,
                    "search_spec": {
                        "query": query,
                        "search": search,
                        "search_type": search_type,
                        "data_source_name": DATA_SOURCE_NAME,
                        "page": page,
                        "display": args.display,
                    },
                    "response": response,
                },
            )

        search_payload = response.get("PrecSearch", {})
        page_items = [
            item
            for item in ensure_list(search_payload.get("prec"))
            if isinstance(item, dict)
        ]
        total_count = parse_total_count(search_payload)

        if not loaded_from_cache:
            stats.list_request_count += 1
        stats.list_item_count += len(page_items)
        items.extend(page_items)
        search_record["pages"].append(
            {
                "page": page,
                "path": str(raw_path),
                "item_count": len(page_items),
                "total_count": total_count,
                "source": "cache" if loaded_from_cache else "api",
                "fetched_at": fetched_at,
            }
        )

        if args.smoke_test and page >= 2:
            break
        if not page_items:
            break
        if total_count and page * args.display >= total_count:
            break

        page += 1
        time.sleep(args.delay)

    search_record["item_count"] = len(items)
    stats.searches.append(search_record)
    return items


def merge_candidates(
    search_spec: dict[str, Any],
    items: list[dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
) -> None:
    """목록 결과를 판례일련번호 기준 후보 묶음으로 통합한다."""
    for rank, item in enumerate(items, start=1):
        precedent_id = extract_precedent_id(item)
        if not precedent_id:
            continue

        candidate = candidates.setdefault(
            precedent_id,
            {
                "precedent_id": precedent_id,
                "matched_queries": [],
                "first_list_item": item,
            },
        )
        candidate["matched_queries"].append(
            {
                "query": search_spec["query"],
                "search": search_spec["search"],
                "search_type": search_spec["search_type"],
                "rank": rank,
            }
        )


def collect_detail(
    api_key: str,
    candidate: dict[str, Any],
    args: argparse.Namespace,
    stats: CollectionStats,
) -> None:
    """후보 판례 하나의 본문조회 API 결과 전체를 저장한다."""
    precedent_id = str(candidate["precedent_id"])
    detail_path = RAW_DETAILS_DIR / f"{precedent_id}.json"
    if detail_path.exists() and not args.overwrite_details:
        stats.detail_skipped_existing_count += 1
        return

    params = {
        "OC": api_key,
        "target": API_TARGET,
        "type": API_RESPONSE_TYPE,
        "ID": precedent_id,
    }
    try:
        response = request_json(params, DETAIL_URL, args.timeout, args.max_retries)
        write_json(
            detail_path,
            {
                "fetched_at": now_utc_iso(),
                "precedent_id": precedent_id,
                "matched_queries": candidate["matched_queries"],
                "response": response,
            },
        )
        stats.detail_success_count += 1
    except Exception as exc:
        stats.detail_failure_count += 1
        append_jsonl(
            RETRY_QUEUE_PATH,
            {
                "created_at": now_utc_iso(),
                "stage": "detail",
                "precedent_id": precedent_id,
                "matched_queries": candidate["matched_queries"],
                "error": str(exc),
            },
        )


def write_candidates(candidates: dict[str, dict[str, Any]]) -> None:
    """이번 실행에서 발견한 후보 판례 목록을 JSONL로 저장한다."""
    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES_PATH.write_text("", encoding="utf-8")
    for candidate in candidates.values():
        append_jsonl(CANDIDATES_PATH, candidate)


def write_manifest(stats: CollectionStats, args: argparse.Namespace) -> None:
    """수집 실행 요약과 통계를 manifest JSON으로 저장한다."""
    stats.finished_at = now_utc_iso()
    manifest = {
        "schema_version": "precedent_collection_manifest.v1",
        "started_at": stats.started_at,
        "finished_at": stats.finished_at,
        "mode": "smoke_test" if args.smoke_test else "full",
        "api": {
            "search_url": SEARCH_URL,
            "detail_url": DETAIL_URL,
            "target": API_TARGET,
            "type": API_RESPONSE_TYPE,
            "data_source_name": DATA_SOURCE_NAME,
            "oc_saved": False,
        },
        "paths": {
            "raw_searches": str(RAW_SEARCHES_DIR),
            "raw_details": str(RAW_DETAILS_DIR),
            "candidates": str(CANDIDATES_PATH),
            "retry_queue": str(RETRY_QUEUE_PATH),
        },
        "stats": {
            "query_count": stats.query_count,
            "list_request_count": stats.list_request_count,
            "list_cached_page_count": stats.list_cached_page_count,
            "list_item_count": stats.list_item_count,
            "unique_candidate_count": stats.unique_candidate_count,
            "detail_success_count": stats.detail_success_count,
            "detail_skipped_existing_count": stats.detail_skipped_existing_count,
            "detail_failure_count": stats.detail_failure_count,
            "interrupted": stats.interrupted,
        },
        "searches": stats.searches,
    }
    write_json(COLLECTION_MANIFEST_PATH, manifest)


def main() -> int:
    """판례 목록과 상세 원본 JSON 수집을 실행한다."""
    args = parse_args()
    ensure_collection_dirs()
    api_key = get_law_api_key(args.allow_test_key)
    searches = get_collection_queries(smoke_test=args.smoke_test)
    stats = CollectionStats(started_at=now_utc_iso(), query_count=len(searches))
    candidates: dict[str, dict[str, Any]] = {}

    try:
        print(f"판례 목록 조회 시작: 검색어 {len(searches)}개")
        for search_spec in searches:
            print(
                " - "
                f"{search_spec['search_type']} search={search_spec['search']} "
                f"query='{search_spec['query']}'"
            )
            try:
                items = collect_search_pages(api_key, search_spec, args, stats)
            except Exception as exc:
                append_jsonl(
                    RETRY_QUEUE_PATH,
                    {
                        "created_at": now_utc_iso(),
                        "stage": "list",
                        "search_spec": search_spec,
                        "error": str(exc),
                    },
                )
                print(f"   목록 조회 실패 기록: query='{search_spec['query']}'")
                continue
            merge_candidates(search_spec, items, candidates)
            time.sleep(args.delay)

        stats.unique_candidate_count = len(candidates)
        write_candidates(candidates)
        print(f"본문 조회 시작: 고유 후보 {len(candidates)}건")

        detail_limit = 10 if args.smoke_test else None
        for index, candidate in enumerate(candidates.values(), start=1):
            if detail_limit is not None and index > detail_limit:
                break
            collect_detail(api_key, candidate, args, stats)
            time.sleep(args.delay)
    except KeyboardInterrupt:
        stats.interrupted = True
        print("\n사용자 중단: 현재까지의 manifest를 저장합니다.")
    finally:
        if candidates:
            stats.unique_candidate_count = len(candidates)
            write_candidates(candidates)
        else:
            try:
                stats.unique_candidate_count = sum(
                    1 for _line in CANDIDATES_PATH.read_text(encoding="utf-8").splitlines()
                )
            except FileNotFoundError:
                stats.unique_candidate_count = 0
        write_manifest(stats, args)

    if stats.interrupted:
        print(
            "중단 요약: "
            f"목록요청 {stats.list_request_count}회, "
            f"목록캐시 {stats.list_cached_page_count}회, "
            f"고유후보 {stats.unique_candidate_count}건, "
            f"본문성공 {stats.detail_success_count}건, "
            f"본문실패 {stats.detail_failure_count}건"
        )
        return 130

    print(f"완료: manifest={COLLECTION_MANIFEST_PATH}")
    print(
        "요약: "
        f"목록요청 {stats.list_request_count}회, "
        f"목록캐시 {stats.list_cached_page_count}회, "
        f"고유후보 {stats.unique_candidate_count}건, "
        f"본문성공 {stats.detail_success_count}건, "
        f"본문실패 {stats.detail_failure_count}건"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
