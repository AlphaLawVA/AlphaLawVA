import argparse
import csv
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from law_api_common import (
    PROJECT_ROOT,
    as_list,
    fetch_json,
    load_law_api_key,
    read_json,
    redact_secret,
    write_json,
)

SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
DEFAULT_KEYWORD_FILE = PROJECT_ROOT / "config/statute_discovery_keywords_v01.json"
DEFAULT_BASE_MANIFEST = (
    PROJECT_ROOT / "local_data/statutes/manifests/seed_collection.json"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "local_data/statutes"
PAGE_SIZE = 100


def safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", value).strip("_")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_config(path: Path) -> dict:
    config = read_json(path)
    searches = config.get("searches")
    if not isinstance(searches, list) or not searches:
        raise ValueError("키워드 설정의 searches는 비어 있지 않은 배열이어야 합니다.")
    for search in searches:
        if not isinstance(search, dict):
            raise ValueError("각 검색 설정은 객체여야 합니다.")
        if not search.get("query") or search.get("search") not in {1, 2}:
            raise ValueError("각 검색 설정에는 query와 search(1 또는 2)가 필요합니다.")
    return config


def extract_search_items(payload: dict) -> list[dict]:
    root = payload.get("LawSearch", {})
    if not isinstance(root, dict):
        return []
    return [item for item in as_list(root.get("law")) if isinstance(item, dict)]


def extract_total_count(payload: dict) -> int:
    root = payload.get("LawSearch", {})
    if not isinstance(root, dict):
        return 0
    try:
        return int(root.get("totalCnt", 0))
    except (TypeError, ValueError):
        return 0


def load_or_fetch_page(
    path: Path,
    api_key: str,
    query: str,
    search_type: int,
    page: int,
    refresh: bool,
) -> tuple[dict, str]:
    if path.exists() and not refresh:
        return read_json(path), "cache"

    payload = fetch_json(
        SEARCH_URL,
        {
            "OC": api_key,
            "target": "eflaw",
            "type": "JSON",
            "search": search_type,
            "query": query,
            "nw": "3",
            "display": PAGE_SIZE,
            "page": page,
        },
    )
    write_json(path, payload)
    return payload, "api"


def merge_candidate(
    candidates: dict[str, dict],
    item: dict,
    search_config: dict,
) -> None:
    law_id = str(item.get("법령ID", "")).strip()
    if not law_id:
        return

    candidate = candidates.setdefault(
        law_id,
        {
            "law_id": law_id,
            "law_serial_number": item.get("법령일련번호"),
            "law_name": item.get("법령명한글"),
            "law_type": item.get("법령구분명"),
            "effective_date": item.get("시행일자"),
            "promulgation_date": item.get("공포일자"),
            "ministry_name": item.get("소관부처명"),
            "matched_queries": [],
            "areas": [],
        },
    )
    query_match = {
        "query": search_config["query"],
        "search": search_config["search"],
        "level": search_config.get("level", ""),
    }
    if query_match not in candidate["matched_queries"]:
        candidate["matched_queries"].append(query_match)
    candidate["areas"] = sorted(
        set(candidate["areas"]) | set(search_config.get("areas", []))
    )


def score_candidate(candidate: dict) -> int:
    level_scores = {"핵심": 3, "조합": 2, "확장": 1}
    score = 0
    for match in candidate["matched_queries"]:
        score += level_scores.get(match.get("level"), 0)
        if match.get("search") == 1:
            score += 2
    return score


def finalize_candidates(
    candidates: dict[str, dict],
    existing_ids: set[str],
    existing_names: set[str],
    warning_terms: list[str],
) -> list[dict]:
    finalized = []
    for candidate in candidates.values():
        law_name = candidate.get("law_name") or ""
        candidate["existing_candidate"] = (
            candidate["law_id"] in existing_ids or law_name in existing_names
        )
        candidate["scope_warnings"] = [
            term for term in warning_terms if term in law_name
        ]
        candidate["name_match_count"] = sum(
            match["search"] == 1 for match in candidate["matched_queries"]
        )
        candidate["body_match_count"] = sum(
            match["search"] == 2 for match in candidate["matched_queries"]
        )
        candidate["discovery_score"] = score_candidate(candidate)
        finalized.append(candidate)

    return sorted(
        finalized,
        key=lambda item: (
            item["existing_candidate"],
            bool(item["scope_warnings"]),
            -item["discovery_score"],
            item.get("law_name") or "",
        ),
    )


def write_review_csv(path: Path, candidates: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "법령명",
        "법령ID",
        "법령종류",
        "소관부처",
        "관련영역",
        "발견점수",
        "법령명검색일치수",
        "본문검색일치수",
        "검색키워드",
        "기존22개여부",
        "범위외경고",
        "검토결정",
        "결정근거",
        "비고",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(
                {
                    "법령명": candidate.get("law_name", ""),
                    "법령ID": candidate["law_id"],
                    "법령종류": candidate.get("law_type", ""),
                    "소관부처": candidate.get("ministry_name", ""),
                    "관련영역": ", ".join(candidate["areas"]),
                    "발견점수": candidate["discovery_score"],
                    "법령명검색일치수": candidate["name_match_count"],
                    "본문검색일치수": candidate["body_match_count"],
                    "검색키워드": ", ".join(
                        match["query"] for match in candidate["matched_queries"]
                    ),
                    "기존22개여부": "기존" if candidate["existing_candidate"] else "신규",
                    "범위외경고": ", ".join(candidate["scope_warnings"]),
                    "검토결정": "",
                    "결정근거": "",
                    "비고": "",
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="키워드로 AlphaLawVA 현행 법령 누락 후보를 탐색합니다."
    )
    parser.add_argument("--keyword-file", type=Path, default=DEFAULT_KEYWORD_FILE)
    parser.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="저장된 검색 페이지를 재사용하지 않고 다시 조회합니다.",
    )
    args = parser.parse_args()

    config = load_config(args.keyword_file.resolve())
    base_manifest = read_json(args.base_manifest.resolve())
    existing_results = base_manifest.get("results", [])
    existing_ids = {
        str(item.get("law_id", "")) for item in existing_results if item.get("law_id")
    }
    existing_names = {
        item.get("law_name") for item in existing_results if item.get("law_name")
    }
    output_dir = args.output_dir.resolve()
    api_key = load_law_api_key()
    candidates: dict[str, dict] = {}
    query_reports = []

    searches = config["searches"]
    for index, search_config in enumerate(searches, start=1):
        query = search_config["query"]
        search_type = search_config["search"]
        search_directory = "law_name" if search_type == 1 else "body"
        query_dir = (
            output_dir
            / "raw/list_searches"
            / search_directory
            / safe_name(query)
        )
        print(f"[{index}/{len(searches)}] search={search_type}, query={query}")
        try:
            first_path = query_dir / "page_0001.json"
            first_payload, first_source = load_or_fetch_page(
                first_path,
                api_key,
                query,
                search_type,
                1,
                args.refresh,
            )
            total_count = extract_total_count(first_payload)
            total_pages = max(1, math.ceil(total_count / PAGE_SIZE))
            page_sources = [first_source]
            for item in extract_search_items(first_payload):
                merge_candidate(candidates, item, search_config)

            for page in range(2, total_pages + 1):
                page_path = query_dir / f"page_{page:04d}.json"
                payload, source = load_or_fetch_page(
                    page_path,
                    api_key,
                    query,
                    search_type,
                    page,
                    args.refresh,
                )
                page_sources.append(source)
                for item in extract_search_items(payload):
                    merge_candidate(candidates, item, search_config)
                time.sleep(max(args.delay, 0))

            query_reports.append(
                {
                    **search_config,
                    "status": "completed",
                    "total_count": total_count,
                    "total_pages": total_pages,
                    "sources": sorted(set(page_sources)),
                }
            )
        except Exception as error:
            query_reports.append(
                {
                    **search_config,
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error_message": redact_secret(str(error), api_key),
                }
            )
        if index < len(searches):
            time.sleep(max(args.delay, 0))

    finalized = finalize_candidates(
        candidates,
        existing_ids,
        existing_names,
        config.get("scope_warning_terms", []),
    )
    manifest = {
        "keyword_file": str(args.keyword_file.resolve()),
        "base_manifest": str(args.base_manifest.resolve()),
        "query_count": len(searches),
        "completed_query_count": sum(
            report["status"] == "completed" for report in query_reports
        ),
        "failed_query_count": sum(
            report["status"] == "failed" for report in query_reports
        ),
        "discovered_law_count": len(finalized),
        "existing_law_count": sum(item["existing_candidate"] for item in finalized),
        "new_law_count": sum(not item["existing_candidate"] for item in finalized),
        "generated_at": utc_timestamp(),
        "query_reports": query_reports,
        "candidates": finalized,
    }
    manifest_path = output_dir / "manifests/discovery_candidates.json"
    review_path = output_dir / "reports/discovery_candidates.csv"
    write_json(manifest_path, manifest)
    write_review_csv(review_path, finalized)

    print(f"후보 명세: {manifest_path}")
    print(f"검토표: {review_path}")
    print(
        f"발견 {len(finalized)}건 / 기존 {manifest['existing_law_count']}건 / "
        f"신규 {manifest['new_law_count']}건"
    )


if __name__ == "__main__":
    main()
