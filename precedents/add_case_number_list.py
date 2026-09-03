# add_case_number_list.py
"""
Description: final_cases 판례 JSON의 사건번호를 검색하기 쉬운 사건번호목록 필드로 분리한다.
콤마로 이어진 축약 사건번호에는 앞 사건번호의 연도와 사건부호를 보완하되 원본 사건번호 필드는 그대로 보존한다.
Author: choeminju
Date: 2026-09-03
Before:
    - local_data/precedents/processed/final_cases/에 생성요약까지 포함된 최종 판례 JSON이 있는 상태.

After:
    - 각 final_cases JSON에 사건번호목록 필드가 추가되고 처리 통계 report가 생성.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FINAL_CASES_DIR = PROJECT_ROOT / "local_data" / "precedents" / "processed" / "final_cases"
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "local_data" / "precedents" / "processed" / "final_cases_manifest.json"
DEFAULT_REPORT_PATH = (
    PROJECT_ROOT / "local_data" / "precedents" / "processed" / "final_cases_case_number_list_report.json"
)

FULL_CASE_NUMBER_RE = re.compile(
    r"^(?P<region>\([^)]+\))?(?P<year>\d{2,4})(?P<code>[가-힣]+)(?P<number>\d+)(?P<label>.*)$"
)
SHORT_CASE_NUMBER_RE = re.compile(r"^(?P<number>\d+)(?P<label>.*)$")


def parse_args() -> argparse.Namespace:
    """커맨드라인 옵션을 정의한다."""
    parser = argparse.ArgumentParser(description="Add 사건번호목록 to final precedent cases.")
    parser.add_argument(
        "--final-cases-dir",
        type=Path,
        default=DEFAULT_FINAL_CASES_DIR,
        help="final_cases JSON 폴더 경로.",
    )
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="final_cases manifest 경로.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="사건번호목록 생성 통계 report 경로.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="테스트용 처리 개수 제한.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="지정하지 않으면 dry-run으로 통계만 출력하고 파일은 수정하지 않는다.",
    )
    return parser.parse_args()


def now_utc_iso() -> str:
    """현재 UTC 시각을 ISO 문자열로 반환한다."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    """UTF-8 JSON 파일을 읽는다."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """딕셔너리를 UTF-8 JSON 파일로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def iter_case_paths(final_cases_dir: Path, limit: int | None = None) -> list[Path]:
    """final_cases JSON 파일을 판례일련번호 순서로 반환한다."""
    paths = sorted(
        final_cases_dir.glob("*.json"),
        key=lambda path: int(path.stem) if path.stem.isdigit() else path.stem,
    )
    if limit is not None:
        return paths[:limit]
    return paths


def compact_case_number(case_number: str) -> str:
    """사건번호 안의 콤마 주변 공백을 정리한다."""
    return re.sub(r"\s*,\s*", ",", str(case_number or "").strip())


def split_case_number(case_number: str) -> tuple[list[str], list[dict[str, str]]]:
    """사건번호를 검색용 목록으로 분리하고 축약 번호를 보완한다.

    예: 98다45652,45669 -> ["98다45652", "98다45669"]
    예: 94다51178,51185(병합) -> ["94다51178", "94다51185(병합)"]
    예: 2020나14126(본소),2021나10787(반소) -> ["2020나14126(본소)", "2021나10787(반소)"]
    """
    compacted = compact_case_number(case_number)
    if not compacted:
        return [], [{"유형": "empty_case_number", "값": case_number}]

    case_numbers: list[str] = []
    warnings: list[dict[str, str]] = []
    last_prefix = ""

    for raw_part in compacted.split(","):
        part = raw_part.strip()
        if not part:
            continue

        full_match = FULL_CASE_NUMBER_RE.match(part)
        if full_match:
            last_prefix = (
                f"{full_match.group('region') or ''}"
                f"{full_match.group('year')}"
                f"{full_match.group('code')}"
            )
            case_numbers.append(part)
            continue

        short_match = SHORT_CASE_NUMBER_RE.match(part)
        if short_match and last_prefix:
            case_numbers.append(f"{last_prefix}{part}")
            continue

        case_numbers.append(part)
        warnings.append({"유형": "unresolved_case_number_part", "값": part})

    return case_numbers, warnings


def update_manifest(manifest_path: Path, report_path: Path, case_count: int) -> None:
    """final_cases manifest에 사건번호목록 파생 필드 정보를 추가한다."""
    if manifest_path.exists():
        manifest = read_json(manifest_path)
    else:
        manifest = {}

    derived_fields = manifest.setdefault("derived_fields", {})
    derived_fields["사건번호목록"] = {
        "created_at": now_utc_iso(),
        "source_field": "사건번호",
        "description": "콤마로 연결된 사건번호를 검색하기 쉽게 분리하고 축약 번호에는 직전 사건번호의 연도와 사건부호를 보완한 목록.",
        "case_count": case_count,
        "report_path": str(report_path.relative_to(PROJECT_ROOT)),
    }
    write_json(manifest_path, manifest)


def main() -> None:
    """final_cases JSON에 사건번호목록을 추가하고 처리 통계를 출력한다."""
    args = parse_args()
    final_cases_dir = args.final_cases_dir.resolve()
    report_path = args.report_path.resolve()
    manifest_path = args.manifest_path.resolve()

    stats: Counter[str] = Counter()
    warning_examples: list[dict[str, str]] = []
    changed_examples: list[dict[str, Any]] = []

    for path in iter_case_paths(final_cases_dir, args.limit):
        payload = read_json(path)
        case_number = str(payload.get("사건번호", "") or "")
        case_number_list, warnings = split_case_number(case_number)

        stats["total_count"] += 1
        if "," in case_number:
            stats["comma_case_number_count"] += 1
        if "(" in case_number or ")" in case_number:
            stats["parenthesized_case_number_count"] += 1
        if len(case_number_list) > 1:
            stats["multi_case_number_count"] += 1
        else:
            stats["single_case_number_count"] += 1
        if warnings:
            stats["warning_count"] += 1
            if len(warning_examples) < 20:
                warning_examples.append(
                    {
                        "파일명": path.name,
                        "사건번호": case_number,
                        "경고": json.dumps(warnings, ensure_ascii=False),
                    }
                )

        before = payload.get("사건번호목록")
        if before != case_number_list:
            stats["changed_count"] += 1
            if len(changed_examples) < 20:
                changed_examples.append(
                    {
                        "파일명": path.name,
                        "사건번호": case_number,
                        "사건번호목록": case_number_list,
                    }
                )
            if args.write:
                payload["사건번호목록"] = case_number_list
                write_json(path, payload)
        elif args.write:
            stats["unchanged_count"] += 1

    report = {
        "schema_version": "precedent_case_number_list_report.v1",
        "created_at": now_utc_iso(),
        "mode": "write" if args.write else "dry-run",
        "final_cases_dir": str(final_cases_dir.relative_to(PROJECT_ROOT)),
        "stats": dict(stats),
        "changed_examples": changed_examples,
        "warning_examples": warning_examples,
        "parse_policy": {
            "사건번호_원문": "수정하지 않고 보존",
            "분리_기준": "콤마",
            "축약번호_보완": "직전 전체 사건번호의 지역표시, 접수연도, 사건부호를 앞에 붙임",
            "괄호라벨": "본소, 반소, 병합, 참가, 재반소 등 괄호 안 표기는 해당 사건번호 뒤에 그대로 유지",
        },
    }

    if args.write:
        write_json(report_path, report)
        update_manifest(manifest_path, report_path, stats["total_count"])

    print(f"mode={report['mode']}")
    print(f"final_cases_dir={final_cases_dir}")
    print(f"total={stats['total_count']}")
    print(f"changed={stats['changed_count']}")
    print(f"multi_case_number={stats['multi_case_number_count']}")
    print(f"comma_case_number={stats['comma_case_number_count']}")
    print(f"parenthesized_case_number={stats['parenthesized_case_number_count']}")
    print(f"warnings={stats['warning_count']}")
    if changed_examples:
        print("changed_examples=")
        for example in changed_examples[:10]:
            print(json.dumps(example, ensure_ascii=False))
    if warning_examples:
        print("warning_examples=")
        for example in warning_examples[:10]:
            print(json.dumps(example, ensure_ascii=False))
    if args.write:
        print(f"report={report_path}")
        print(f"manifest={manifest_path}")


if __name__ == "__main__":
    main()
