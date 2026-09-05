# normalize_judgment_type.py
"""
Description: final_cases 판례 JSON의 판결유형 표기를 정규화한다.
전각 콜론, 콜론 주변 공백, 일부 붙어 있는 판결유형 표기만 통일하고 의미가 애매한 값은 임의 수정하지 않는다.
Author: choeminju
Date: 2026-09-04
Before:
    - local_data/precedents/processed/final_cases/의 판결유형 값이 공백과 콜론 표기 차이로 나뉘어 있는 상태.

After:
    - 각 final_cases JSON의 판결유형 표기가 통일되고 변경 전후 매핑 report가 생성.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FINAL_CASES_DIR = PROJECT_ROOT / "local_data" / "precedents" / "processed" / "final_cases"
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "local_data" / "precedents" / "processed" / "final_cases_manifest.json"
DEFAULT_REPORT_PATH = (
    PROJECT_ROOT / "local_data" / "precedents" / "processed" / "final_cases_judgment_type_report.json"
)


def parse_args() -> argparse.Namespace:
    """커맨드라인 옵션을 정의한다."""
    parser = argparse.ArgumentParser(description="Normalize 판결유형 in final precedent cases.")
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
        help="판결유형 정규화 통계 report 경로.",
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


def normalize_judgment_type(value: Any) -> str:
    """판결유형의 의미는 유지하면서 표기 차이만 줄인다."""
    text = "" if value is None else str(value).strip()
    if not text:
        return ""

    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*:\s*", " : ", text)
    text = re.sub(r"전원합의체\s*(판결|결정)", r"전원합의체 \1", text)
    return text.strip()


def update_manifest(manifest_path: Path, report_path: Path, case_count: int) -> None:
    """final_cases manifest에 판결유형 정규화 이력을 기록한다."""
    if manifest_path.exists():
        manifest = read_json(manifest_path)
    else:
        manifest = {}

    normalizations = manifest.setdefault("normalizations", {})
    normalizations["판결유형"] = {
        "updated_at": now_utc_iso(),
        "description": "전각 콜론, 콜론 주변 공백, 전원합의체 판결/결정 붙임 표기를 통일.",
        "case_count": case_count,
        "report_path": str(report_path.relative_to(PROJECT_ROOT)),
    }
    write_json(manifest_path, manifest)


def main() -> None:
    """final_cases JSON의 판결유형을 정규화하고 처리 통계를 출력한다."""
    args = parse_args()
    final_cases_dir = args.final_cases_dir.resolve()
    report_path = args.report_path.resolve()
    manifest_path = args.manifest_path.resolve()

    stats: Counter[str] = Counter()
    before_counts: Counter[str] = Counter()
    after_counts: Counter[str] = Counter()
    mapping: dict[str, Counter[str]] = defaultdict(Counter)
    changed_examples: list[dict[str, str]] = []
    missing_examples: list[str] = []

    for path in iter_case_paths(final_cases_dir, args.limit):
        payload = read_json(path)
        before = "" if payload.get("판결유형") is None else str(payload.get("판결유형")).strip()
        after = normalize_judgment_type(before)

        stats["total_count"] += 1
        if not before:
            stats["missing_count"] += 1
            if len(missing_examples) < 20:
                missing_examples.append(path.name)

        before_counts[before] += 1
        after_counts[after] += 1
        mapping[after][before] += 1

        if before != after:
            stats["changed_count"] += 1
            if len(changed_examples) < 30:
                changed_examples.append(
                    {
                        "파일명": path.name,
                        "변경전": before,
                        "변경후": after,
                    }
                )
            if args.write:
                payload["판결유형"] = after
                write_json(path, payload)
        else:
            stats["unchanged_count"] += 1

    report = {
        "schema_version": "precedent_judgment_type_normalization_report.v1",
        "created_at": now_utc_iso(),
        "mode": "write" if args.write else "dry-run",
        "final_cases_dir": str(final_cases_dir),
        "stats": dict(stats),
        "unique_before_count": len(before_counts),
        "unique_after_count": len(after_counts),
        "before_counts": dict(before_counts.most_common()),
        "after_counts": dict(after_counts.most_common()),
        "mapping": {
            normalized: dict(originals.most_common())
            for normalized, originals in sorted(mapping.items(), key=lambda item: (-sum(item[1].values()), item[0]))
        },
        "changed_examples": changed_examples,
        "missing_examples": missing_examples,
    }

    if args.write:
        write_json(report_path, report)
        update_manifest(manifest_path, report_path, stats["total_count"])
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    print(
        "완료: "
        f"mode={report['mode']}, "
        f"전체 {stats['total_count']}건, "
        f"변경 {stats['changed_count']}건, "
        f"고유값 {len(before_counts)}개 -> {len(after_counts)}개"
    )
    if args.write:
        print(f"리포트: {report_path}")


if __name__ == "__main__":
    main()
