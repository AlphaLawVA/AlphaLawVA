# collect_statute_xmls.py
"""
Description: 최종 선정 법령 목록을 기준으로 현행 법령 본문 XML을 수집하고
법령 식별정보를 검증한 뒤 수집 결과를 기록한다.
Author: ooheunsu
Date: 2026-08-17
Before:
    - 최종 선정 법령 목록과 LAW_API_KEY가 준비된 상태.

After:
    - 법령별 XML 원본과 전체 수집 결과 manifest가 생성.
"""

import argparse
import hashlib
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from law_api_common import (
    PROJECT_ROOT,
    fetch_json,
    load_law_api_key,
    read_json,
    redact_secret,
    write_json,
)

SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"
DEFAULT_SELECTION_FILE = (
    PROJECT_ROOT / "data/statutes/metadata/statute_inclusion_list_v01.json"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/statutes/raw_xmls/details"
DEFAULT_MANIFEST = PROJECT_ROOT / "data/statutes/manifests/xml_collection_v01.json"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def as_list(value: object) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def load_selected_laws(path: Path) -> list[dict]:
    payload = read_json(path)
    laws = payload.get("laws")
    if not isinstance(laws, list):
        raise ValueError("선정 목록의 laws는 배열이어야 합니다.")

    selected = []
    seen_ids = set()
    for law in laws:
        if not isinstance(law, dict):
            raise ValueError("선정 법령은 객체여야 합니다.")
        law_id = str(law.get("law_id", "")).strip()
        law_name = str(law.get("law_name", "")).strip()
        if not law_id or not law_name:
            raise ValueError("선정 법령에 law_id 또는 law_name이 없습니다.")
        if law_id in seen_ids:
            raise ValueError(f"선정 목록에 중복 법령 ID가 있습니다: {law_id}")
        seen_ids.add(law_id)
        selected.append(law)

    declared_total = payload.get("total")
    if declared_total != len(selected):
        raise ValueError(
            f"선정 목록 total과 실제 개수가 다릅니다: "
            f"{declared_total} != {len(selected)}"
        )
    return selected


def find_current_law(payload: dict, law_id: str, law_name: str) -> dict:
    search_result = payload.get("LawSearch")
    if not isinstance(search_result, dict):
        message = payload.get("msg") or payload.get("result") or "응답 형식 오류"
        raise RuntimeError(f"목록 조회 실패: {message}")

    matches = [
        item
        for item in as_list(search_result.get("law"))
        if item.get("법령명한글") == law_name
        and str(item.get("법령ID", "")).strip() == law_id
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"현행 목록에서 법령명과 ID가 일치하는 결과를 "
            f"하나로 확정하지 못했습니다: {len(matches)}건"
        )

    match = matches[0]
    if not match.get("법령일련번호") or not match.get("시행일자"):
        raise RuntimeError("현행 목록 결과에 MST 또는 시행일자가 없습니다.")
    return match


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child_text(parent: ElementTree.Element, name: str) -> str:
    for child in list(parent):
        if local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def validate_xml(xml_bytes: bytes, law_id: str, law_name: str) -> None:
    root = ElementTree.fromstring(xml_bytes)
    if local_name(root.tag) == "Response":
        message = child_text(root, "msg") or child_text(root, "result")
        raise RuntimeError(message or "XML API 오류 응답")
    if local_name(root.tag) != "법령":
        raise ValueError(f"XML 최상위 요소가 법령이 아닙니다: {local_name(root.tag)}")

    basic_info = next(
        (child for child in list(root) if local_name(child.tag) == "기본정보"),
        None,
    )
    if basic_info is None:
        raise ValueError("XML에 기본정보가 없습니다.")
    actual_id = child_text(basic_info, "법령ID")
    actual_name = child_text(basic_info, "법령명_한글")
    if actual_id != law_id or actual_name != law_name:
        raise ValueError(
            f"XML 법령 식별자가 선정 목록과 다릅니다: "
            f"{actual_id}/{actual_name}"
        )


def fetch_xml(
    mst: str,
    effective_date: str,
    api_key: str,
    attempts: int = 3,
) -> bytes:
    params = {
        "OC": api_key,
        "target": "eflaw",
        "type": "XML",
        "MST": mst,
        "efYd": effective_date,
    }
    request_url = f"{SERVICE_URL}?{urlencode(params)}"
    request = Request(
        request_url,
        headers={
            "Accept": "application/xml,text/xml",
            "User-Agent": "AlphaLawVA/0.1",
        },
    )
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=60) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError):
            if attempt == attempts:
                raise
            time.sleep(2 ** (attempt - 1))
    raise RuntimeError("XML API 재시도 처리가 비정상적으로 종료되었습니다.")


def write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_bytes(content)
    temporary_path.replace(path)


def collect_one(
    law: dict,
    output_dir: Path,
    api_key: str,
    refresh: bool,
) -> dict:
    law_id = str(law["law_id"])
    law_name = str(law["law_name"])
    output_path = output_dir / f"{law_id}.xml"

    if output_path.exists() and not refresh:
        xml_bytes = output_path.read_bytes()
        validate_xml(xml_bytes, law_id, law_name)
        source = "cache"
        mst = None
        effective_date = law.get("effective_date")
    else:
        search_payload = fetch_json(
            SEARCH_URL,
            {
                "OC": api_key,
                "target": "eflaw",
                "type": "JSON",
                "query": law_name,
                "nw": "3",
            },
        )
        current_law = find_current_law(search_payload, law_id, law_name)
        mst = str(current_law["법령일련번호"])
        effective_date = str(current_law["시행일자"])
        xml_bytes = fetch_xml(mst, effective_date, api_key)
        validate_xml(xml_bytes, law_id, law_name)
        write_bytes(output_path, xml_bytes)
        source = "api"

    return {
        "law_id": law_id,
        "law_name": law_name,
        "status": "collected",
        "source": source,
        "mst": mst,
        "effective_date": effective_date,
        "xml_file": output_path.relative_to(PROJECT_ROOT).as_posix(),
        "sha256": hashlib.sha256(xml_bytes).hexdigest(),
        "size_bytes": len(xml_bytes),
        "collected_at": utc_timestamp(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="최종 선정된 법령의 현행 본문 XML을 수집합니다."
    )
    parser.add_argument(
        "--selection-file",
        type=Path,
        default=DEFAULT_SELECTION_FILE,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    laws = load_selected_laws(args.selection_file.resolve())
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit은 1 이상이어야 합니다.")
        laws = laws[: args.limit]

    api_key = load_law_api_key()
    results = []
    for index, law in enumerate(laws, start=1):
        law_id = str(law["law_id"])
        law_name = str(law["law_name"])
        print(f"[{index}/{len(laws)}] {law_name} ({law_id})")
        try:
            result = collect_one(
                law,
                args.output_dir.resolve(),
                api_key,
                args.refresh,
            )
        except Exception as error:
            result = {
                "law_id": law_id,
                "law_name": law_name,
                "status": "failed",
                "error": redact_secret(str(error), api_key),
                "collected_at": utc_timestamp(),
            }
            print(f"  실패: {result['error']}")
        results.append(result)
        if index < len(laws) and args.delay > 0:
            time.sleep(args.delay)

    counts = Counter(result["status"] for result in results)
    write_json(
        args.manifest.resolve(),
        {
            "version": "0.1",
            "selection_file": args.selection_file.resolve()
            .relative_to(PROJECT_ROOT)
            .as_posix(),
            "requested_count": len(laws),
            "counts": dict(counts),
            "completed_at": utc_timestamp(),
            "results": results,
        },
    )
    print(f"수집 결과: {dict(counts)}")
    print(f"수집 manifest: {args.manifest.resolve()}")
    if counts.get("failed", 0):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
