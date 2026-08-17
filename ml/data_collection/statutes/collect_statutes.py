import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from law_api_common import (
    PROJECT_ROOT,
    STATUTE_CONFIG_DIR,
    STATUTE_DATA_DIR,
    as_list,
    fetch_json,
    load_law_api_key,
    read_json,
    redact_secret,
    write_json,
)

SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"
DEFAULT_CANDIDATE_FILE = STATUTE_CONFIG_DIR / "statute_candidates_v01.json"
DEFAULT_OUTPUT_DIR = STATUTE_DATA_DIR


def safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", value).strip("_")


def load_candidates(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("후보 파일의 candidates는 배열이어야 합니다.")
    for candidate in candidates:
        if not isinstance(candidate, dict) or not candidate.get("law_name"):
            raise ValueError("모든 후보에는 law_name이 필요합니다.")
    return candidates


def select_candidates(
    candidates: list[dict],
    only_names: list[str] | None,
    collect_all: bool,
) -> list[dict]:
    if collect_all:
        return candidates

    requested = set(only_names or [])
    selected = [item for item in candidates if item["law_name"] in requested]
    missing = sorted(requested - {item["law_name"] for item in selected})
    if missing:
        raise ValueError(f"후보 파일에서 찾지 못한 법령명: {', '.join(missing)}")
    return selected


def extract_search_items(payload: dict) -> list[dict]:
    root = payload.get("LawSearch", {})
    if not isinstance(root, dict):
        return []
    return [item for item in as_list(root.get("law")) if isinstance(item, dict)]


def find_exact_match(items: list[dict], law_name: str) -> dict | None:
    matches = [item for item in items if item.get("법령명한글") == law_name]
    if len(matches) == 1:
        return matches[0]
    return None


def validate_detail(payload: dict, expected_law_name: str) -> tuple[bool, str]:
    law = payload.get("법령")
    if not isinstance(law, dict):
        return False, "최상위 법령 객체가 없습니다."

    basic = law.get("기본정보")
    if not isinstance(basic, dict):
        return False, "기본정보가 없습니다."
    if basic.get("법령명_한글") != expected_law_name:
        return False, "상세 응답의 법령명이 요청한 법령명과 다릅니다."

    articles = law.get("조문")
    if not isinstance(articles, dict) or not as_list(articles.get("조문단위")):
        return False, "조문단위가 없거나 비어 있습니다."
    return True, "ok"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_or_fetch(
    path: Path,
    url: str,
    params: dict,
    refresh: bool,
) -> tuple[dict, str]:
    if path.exists() and not refresh:
        return read_json(path), "cache"
    payload = fetch_json(url, params)
    write_json(path, payload)
    return payload, "api"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_one(
    candidate: dict,
    api_key: str,
    output_dir: Path,
    refresh: bool,
) -> dict:
    law_name = candidate["law_name"]
    search_path = (
        output_dir / "raw_jsons/list_searches/seed" / f"{safe_name(law_name)}.json"
    )
    search_payload, search_source = load_or_fetch(
        search_path,
        SEARCH_URL,
        {
            "OC": api_key,
            "target": "eflaw",
            "type": "JSON",
            "query": law_name,
            "nw": "3",
        },
        refresh,
    )

    items = extract_search_items(search_payload)
    exact_matches = [item for item in items if item.get("법령명한글") == law_name]
    match = find_exact_match(items, law_name)
    if match is None:
        return {
            **candidate,
            "status": "ambiguous" if exact_matches else "not_found",
            "exact_match_count": len(exact_matches),
            "search_result_count": len(items),
            "collected_at": utc_timestamp(),
        }

    law_id = str(match.get("법령ID", "")).strip()
    if not law_id:
        return {
            **candidate,
            "status": "missing_law_id",
            "collected_at": utc_timestamp(),
        }

    detail_path = output_dir / "raw_jsons/details" / f"{law_id}.json"
    detail_payload, detail_source = load_or_fetch(
        detail_path,
        SERVICE_URL,
        {
            "OC": api_key,
            "target": "eflaw",
            "type": "JSON",
            "ID": law_id,
        },
        refresh,
    )
    valid, validation_message = validate_detail(detail_payload, law_name)

    return {
        **candidate,
        "law_id": law_id,
        "law_serial_number": match.get("법령일련번호"),
        "law_type": match.get("법령구분명"),
        "effective_date": match.get("시행일자"),
        "promulgation_date": match.get("공포일자"),
        "ministry_name": match.get("소관부처명"),
        "status": "collected" if valid else "invalid_detail",
        "validation_message": validation_message,
        "search_source": search_source,
        "detail_source": detail_source,
        "detail_sha256": file_sha256(detail_path),
        "search_file": str(search_path.relative_to(PROJECT_ROOT)),
        "detail_file": str(detail_path.relative_to(PROJECT_ROOT)),
        "collected_at": utc_timestamp(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AlphaLawVA 법령 후보를 검색하고 현행 본문 원본 JSON을 저장합니다."
    )
    parser.add_argument(
        "--candidate-file",
        type=Path,
        default=DEFAULT_CANDIDATE_FILE,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--only", nargs="+", metavar="법령명")
    selection.add_argument("--all", action="store_true")
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="기존 원본 JSON을 재사용하지 않고 API에서 다시 수집합니다.",
    )
    args = parser.parse_args()

    candidate_file = args.candidate_file.resolve()
    output_dir = args.output_dir.resolve()
    candidates = select_candidates(
        load_candidates(candidate_file),
        args.only,
        args.all,
    )
    api_key = load_law_api_key()

    results = []
    for index, candidate in enumerate(candidates, start=1):
        law_name = candidate["law_name"]
        print(f"[{index}/{len(candidates)}] {law_name} 수집 중")
        try:
            result = collect_one(candidate, api_key, output_dir, args.refresh)
        except Exception as error:
            result = {
                **candidate,
                "status": "failed",
                "error_type": type(error).__name__,
                "error_message": redact_secret(str(error), api_key),
                "collected_at": utc_timestamp(),
            }
        results.append(result)
        print(f"  결과: {result['status']}")
        if index < len(candidates):
            time.sleep(max(args.delay, 0))

    manifest = {
        "candidate_file": str(candidate_file),
        "total": len(results),
        "counts": {
            status: sum(item["status"] == status for item in results)
            for status in sorted({item["status"] for item in results})
        },
        "collected_at": utc_timestamp(),
        "results": results,
    }
    manifest_path = output_dir / "manifests/seed_collection.json"
    write_json(manifest_path, manifest)
    print(f"수집 명세 저장 완료: {manifest_path}")


if __name__ == "__main__":
    main()
