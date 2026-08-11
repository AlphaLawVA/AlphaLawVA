import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

from collect_statutes import file_sha256, validate_detail
from law_api_common import (
    PROJECT_ROOT,
    fetch_json,
    load_law_api_key,
    read_json,
    redact_secret,
    write_json,
)

SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"
DEFAULT_DISCOVERY_MANIFEST = (
    PROJECT_ROOT
    / "local_data/statutes/manifests/discovery_candidates.json"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "local_data/statutes"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_new_candidates(path: Path) -> list[dict]:
    manifest = read_json(path)
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("발견 후보 명세의 candidates는 배열이어야 합니다.")
    return [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict)
        and candidate.get("law_id")
        and not candidate.get("existing_candidate")
    ]


def collect_candidate(
    candidate: dict,
    api_key: str,
    output_dir: Path,
    refresh: bool,
) -> dict:
    law_id = str(candidate["law_id"])
    law_name = candidate.get("law_name") or ""
    detail_path = output_dir / "raw/details" / f"{law_id}.json"

    if detail_path.exists() and not refresh:
        detail_payload = read_json(detail_path)
        source = "cache"
    else:
        detail_payload = fetch_json(
            SERVICE_URL,
            {
                "OC": api_key,
                "target": "eflaw",
                "type": "JSON",
                "ID": law_id,
            },
        )
        write_json(detail_path, detail_payload)
        source = "api"

    valid, validation_message = validate_detail(detail_payload, law_name)
    return {
        "law_id": law_id,
        "law_name": law_name,
        "law_type": candidate.get("law_type"),
        "effective_date": candidate.get("effective_date"),
        "matched_queries": candidate.get("matched_queries", []),
        "discovery_score": candidate.get("discovery_score"),
        "scope_warnings": candidate.get("scope_warnings", []),
        "status": "collected" if valid else "invalid_detail",
        "validation_message": validation_message,
        "source": source,
        "detail_file": str(detail_path.relative_to(PROJECT_ROOT)),
        "detail_sha256": file_sha256(detail_path),
        "processed_at": utc_timestamp(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="키워드 탐색에서 발견한 신규 현행 법령 원문을 수집합니다."
    )
    parser.add_argument(
        "--discovery-manifest",
        type=Path,
        default=DEFAULT_DISCOVERY_MANIFEST,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--limit",
        type=int,
        help="정렬된 신규 후보 중 앞에서부터 지정한 개수만 처리합니다.",
    )
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="이미 저장된 상세 원본을 다시 수집합니다.",
    )
    args = parser.parse_args()

    candidates = load_new_candidates(args.discovery_manifest.resolve())
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit은 1 이상이어야 합니다.")
        candidates = candidates[: args.limit]

    output_dir = args.output_dir.resolve()
    api_key = load_law_api_key()
    results = []
    for index, candidate in enumerate(candidates, start=1):
        print(f"[{index}/{len(candidates)}] {candidate.get('law_name')} 수집 중")
        try:
            result = collect_candidate(candidate, api_key, output_dir, args.refresh)
        except Exception as error:
            result = {
                "law_id": candidate.get("law_id"),
                "law_name": candidate.get("law_name"),
                "status": "failed",
                "error_type": type(error).__name__,
                "error_message": redact_secret(str(error), api_key),
                "processed_at": utc_timestamp(),
            }
        results.append(result)
        print(f"  결과: {result['status']}")
        if index < len(candidates):
            time.sleep(max(args.delay, 0))

    manifest = {
        "discovery_manifest": str(args.discovery_manifest.resolve()),
        "requested_count": len(candidates),
        "counts": {
            status: sum(item["status"] == status for item in results)
            for status in sorted({item["status"] for item in results})
        },
        "generated_at": utc_timestamp(),
        "results": results,
    }
    manifest_path = output_dir / "manifests/expanded_collection.json"
    write_json(manifest_path, manifest)
    print(f"확장 수집 명세: {manifest_path}")


if __name__ == "__main__":
    main()
