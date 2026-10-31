# integrate_legal_term_sources.py
"""
Description: 판례와 법령에서 수집한 법률용어를 동일한 표기 기준으로 통합하고,
출처별 통계와 공식 정의 및 우리말샘 정의를 분리하여 보존한다.
Author: choeminju
Date: 2026-09-27
Before:
    - 정제된 판례 용어, 법령 용어 후보, 기존 우리말샘 정의가 준비된 상태.

After:
    - 통합 후보, 사용 가능 용어, 우리말샘 조회 대상, 보류 용어와 manifest가 생성.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOCAL_DATA_ROOT = PROJECT_ROOT / "local_data"
DEFAULT_PRECEDENT_TERMS_PATH = (
    LOCAL_DATA_ROOT
    / "precedents"
    / "legal_terms"
    / "matched_candidates_morphology_validated"
    / "matched_legal_terms.jsonl"
)
DEFAULT_STATUTE_TERMS_PATH = (
    LOCAL_DATA_ROOT
    / "statutes"
    / "legal_terms"
    / "extracted"
    / "statute_legal_terms_v01.jsonl"
)
DEFAULT_URIMALSAEM_PATHS = [
    LOCAL_DATA_ROOT
    / "precedents"
    / "legal_terms"
    / "urimalsaem_definitions"
    / "urimalsaem_term_definitions.jsonl"
]
DEFAULT_OUTPUT_DIR = LOCAL_DATA_ROOT / "legal_terms" / "integrated_v01"

INTEGRATED_FILENAME = "integrated_legal_term_candidates.jsonl"
USABLE_FILENAME = "usable_legal_terms.jsonl"
LOOKUP_FILENAME = "urimalsaem_lookup_candidates.jsonl"
UNRESOLVED_FILENAME = "unresolved_legal_terms.jsonl"
REVIEW_FILENAME = "integrated_legal_terms_review.csv"
MANIFEST_FILENAME = "manifest.json"
SCHEMA_VERSION = "integrated_legal_terms.v0.1"
WHITESPACE_RE = re.compile(r"\s+")


def parse_args() -> argparse.Namespace:
    """통합 입력과 출력 경로를 정의한다."""
    parser = argparse.ArgumentParser(
        description="판례·법령 법률용어 후보와 정의를 통합합니다."
    )
    parser.add_argument(
        "--precedent-terms-path",
        type=Path,
        default=DEFAULT_PRECEDENT_TERMS_PATH,
    )
    parser.add_argument(
        "--statute-terms-path",
        type=Path,
        default=DEFAULT_STATUTE_TERMS_PATH,
    )
    parser.add_argument(
        "--urimalsaem-path",
        type=Path,
        action="append",
        dest="urimalsaem_paths",
        help="우리말샘 정의 JSONL. 여러 파일이면 옵션을 반복합니다.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )
    return parser.parse_args()


def now_utc_iso() -> str:
    """현재 UTC 시각을 ISO 문자열로 반환한다."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_term(value: Any) -> str:
    """NFKC와 소문자 변환 후 모든 공백을 제거한 통합 키를 만든다."""
    text = unicodedata.normalize("NFKC", str(value or "")).lower().strip()
    return WHITESPACE_RE.sub("", text)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 객체를 읽고 잘못된 행 번호를 오류에 포함한다."""
    rows = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}의 {line_number}번째 행이 객체가 아닙니다.")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """JSONL을 임시 파일을 거쳐 안전하게 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary_path.replace(path)


def write_json(path: Path, value: dict[str, Any]) -> None:
    """JSON 객체를 들여쓰기해 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    """파일의 SHA-256을 계산한다."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def project_relative_path(path: Path) -> str:
    """프로젝트 내부 파일은 상대경로로 기록한다."""
    resolved = path.resolve()
    if resolved.is_relative_to(PROJECT_ROOT):
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    return resolved.as_posix()


def unique_dicts(rows: Iterable[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """지정 필드 조합이 같은 객체를 처음 등장한 순서로 중복 제거한다."""
    result = []
    seen = set()
    for row in rows:
        key = tuple(str(row.get(field) or "") for field in fields)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def merge_statute_rows(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """정규화 후 같은 법령 용어의 출처와 정의를 합친다."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = normalize_term(row.get("term"))
        if not key:
            continue
        grouped.setdefault(key, []).append(row)

    merged = {}
    for key, values in grouped.items():
        sources = unique_dicts(
            (source for value in values for source in value.get("sources", [])),
            ("source_id",),
        )
        official_definitions = unique_dicts(
            (
                definition
                for value in values
                for definition in value.get("official_definitions", [])
            ),
            ("source_id", "definition"),
        )
        law_names = {str(source.get("law_name") or "") for source in sources}
        articles = {
            (str(source.get("law_name") or ""), str(source.get("article") or ""))
            for source in sources
        }
        merged[key] = {
            "term": str(values[0].get("term") or key),
            "variants": sorted({str(value.get("term") or key) for value in values}),
            "document_count": len(law_names - {""}),
            "article_count": len({article for article in articles if any(article)}),
            "occurrence_count": sum(
                int(source.get("occurrence_count") or 0) for source in sources
            ),
            "sources": sources,
            "extraction_method": sorted(
                {
                    str(method)
                    for value in values
                    for method in value.get("extraction_method", [])
                    if str(method)
                }
            ),
            "official_definitions": official_definitions,
        }
    return merged


def index_precedent_rows(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """판례 용어를 정규화 키로 색인하고 중복 키는 거부한다."""
    indexed = {}
    for row in rows:
        key = normalize_term(row.get("match_key") or row.get("term"))
        if not key:
            continue
        if key in indexed:
            raise ValueError(f"판례 용어 정규화 키가 중복됩니다: {key}")
        indexed[key] = row
    return indexed


def index_urimalsaem_rows(
    row_groups: Iterable[Iterable[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """여러 우리말샘 결과에서 뒤에 제공된 최신 행을 우선한다."""
    indexed = {}
    for rows in row_groups:
        for row in rows:
            key = normalize_term(row.get("match_key") or row.get("term"))
            if key:
                indexed[key] = row
    return indexed


def choose_display_term(
    key: str,
    precedent: dict[str, Any] | None,
    statute: dict[str, Any] | None,
) -> str:
    """판례 표기를 우선하되 없으면 법령 표기를 사용한다."""
    if precedent and precedent.get("term"):
        return str(precedent["term"])
    if statute and statute.get("term"):
        return str(statute["term"])
    return key


def build_integrated_rows(
    precedent_rows: Iterable[dict[str, Any]],
    statute_rows: Iterable[dict[str, Any]],
    urimalsaem_row_groups: Iterable[Iterable[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """세 출처를 정규화 키로 결합하고 정의 상태를 계산한다."""
    precedents = index_precedent_rows(precedent_rows)
    statutes = merge_statute_rows(statute_rows)
    urimalsaem = index_urimalsaem_rows(urimalsaem_row_groups)
    result = []

    for key in sorted(precedents.keys() | statutes.keys()):
        precedent = precedents.get(key)
        statute = statutes.get(key)
        dictionary = urimalsaem.get(key)
        lookup_status = str(dictionary.get("lookup_status")) if dictionary else "not_queried"
        definitions = list(dictionary.get("definitions", [])) if dictionary else []
        official_definitions = list(statute.get("official_definitions", [])) if statute else []
        if lookup_status == "exact" and definitions:
            definition_status = "urimalsaem_exact"
        elif official_definitions:
            definition_status = "statute_official_only"
        elif lookup_status in {"not_found", "error"}:
            definition_status = f"urimalsaem_{lookup_status}"
        else:
            definition_status = "definition_pending"

        variants = {choose_display_term(key, precedent, statute)}
        if precedent:
            variants.update(str(value) for value in precedent.get("official_variants", []))
        if statute:
            variants.update(str(value) for value in statute.get("variants", []))
        source_domains = [
            domain
            for domain, source in (("precedent", precedent), ("statute", statute))
            if source is not None
        ]
        result.append(
            {
                "match_key": key,
                "term": choose_display_term(key, precedent, statute),
                "variants": sorted(value for value in variants if value),
                "source_domains": source_domains,
                "precedent_source": precedent,
                "statute_source": statute,
                "definitions": {
                    "urimalsaem": definitions,
                    "statute_official": official_definitions,
                },
                "urimalsaem_lookup_status": lookup_status,
                "definition_status": definition_status,
                "review_status": "pending",
                "review_note": "",
            }
        )
    return result


def lookup_candidate(row: dict[str, Any]) -> dict[str, Any]:
    """우리말샘 수집기가 읽을 최소 후보와 출처 통계를 만든다."""
    precedent = row.get("precedent_source") or {}
    statute = row.get("statute_source") or {}
    return {
        "match_key": row["match_key"],
        "term": row["term"],
        "source_domains": row["source_domains"],
        "total_case_count": precedent.get("total_case_count", 0),
        "total_occurrence_count": precedent.get("total_occurrence_count", 0),
        "field_stats": precedent.get("field_stats", {}),
        "document_count": statute.get("document_count", 0),
        "article_count": statute.get("article_count", 0),
        "statute_occurrence_count": statute.get("occurrence_count", 0),
    }


def write_review_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """통합 상태를 사람이 빠르게 확인할 CSV로 저장한다."""
    columns = [
        "term",
        "match_key",
        "source_domains",
        "definition_status",
        "urimalsaem_lookup_status",
        "urimalsaem_definition_count",
        "official_definition_count",
        "precedent_case_count",
        "statute_document_count",
        "statute_article_count",
        "review_status",
        "review_note",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            precedent = row.get("precedent_source") or {}
            statute = row.get("statute_source") or {}
            writer.writerow(
                {
                    "term": row["term"],
                    "match_key": row["match_key"],
                    "source_domains": ",".join(row["source_domains"]),
                    "definition_status": row["definition_status"],
                    "urimalsaem_lookup_status": row["urimalsaem_lookup_status"],
                    "urimalsaem_definition_count": len(row["definitions"]["urimalsaem"]),
                    "official_definition_count": len(row["definitions"]["statute_official"]),
                    "precedent_case_count": precedent.get("total_case_count", 0),
                    "statute_document_count": statute.get("document_count", 0),
                    "statute_article_count": statute.get("article_count", 0),
                    "review_status": row["review_status"],
                    "review_note": row["review_note"],
                }
            )


def main() -> None:
    """입력 파일을 통합하고 정의 상태별 결과를 저장한다."""
    args = parse_args()
    precedent_path = args.precedent_terms_path.resolve()
    statute_path = args.statute_terms_path.resolve()
    urimalsaem_paths = [
        path.resolve()
        for path in (args.urimalsaem_paths or DEFAULT_URIMALSAEM_PATHS)
    ]
    output_dir = args.output_dir.resolve()
    input_paths = [precedent_path, statute_path, *urimalsaem_paths]
    missing = [str(path) for path in input_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"입력 파일이 없습니다: {', '.join(missing)}")

    precedent_rows = read_jsonl(precedent_path)
    statute_rows = read_jsonl(statute_path)
    urimalsaem_groups = [read_jsonl(path) for path in urimalsaem_paths]
    integrated = build_integrated_rows(
        precedent_rows,
        statute_rows,
        urimalsaem_groups,
    )
    usable = [
        row for row in integrated if row["definition_status"] == "urimalsaem_exact"
    ]
    lookup_rows = [
        lookup_candidate(row)
        for row in integrated
        if row["urimalsaem_lookup_status"] == "not_queried"
    ]
    unresolved = [
        row
        for row in integrated
        if row["urimalsaem_lookup_status"] in {"not_found", "error"}
    ]

    outputs = {
        INTEGRATED_FILENAME: integrated,
        USABLE_FILENAME: usable,
        LOOKUP_FILENAME: lookup_rows,
        UNRESOLVED_FILENAME: unresolved,
    }
    for filename, rows in outputs.items():
        write_jsonl(output_dir / filename, rows)
    write_review_csv(output_dir / REVIEW_FILENAME, integrated)

    status_counts = Counter(row["definition_status"] for row in integrated)
    source_counts = Counter(
        "+".join(row["source_domains"]) for row in integrated
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": now_utc_iso(),
        "inputs": [
            {
                "path": project_relative_path(path),
                "sha256": file_sha256(path),
            }
            for path in input_paths
        ],
        "counts": {
            "precedent_input_rows": len(precedent_rows),
            "statute_input_rows": len(statute_rows),
            "integrated_candidate_rows": len(integrated),
            "usable_rows": len(usable),
            "urimalsaem_lookup_candidate_rows": len(lookup_rows),
            "unresolved_rows": len(unresolved),
            "definition_status": dict(sorted(status_counts.items())),
            "source_domains": dict(sorted(source_counts.items())),
        },
        "outputs": {
            filename: {
                "path": project_relative_path(output_dir / filename),
                "sha256": file_sha256(output_dir / filename),
                "row_count": len(rows),
            }
            for filename, rows in outputs.items()
        },
        "review_csv": project_relative_path(output_dir / REVIEW_FILENAME),
        "usage_notice": (
            "usable_legal_terms는 우리말샘 정확 일치 정의가 있는 용어만 포함한다. "
            "법령 공식 정의는 별도 배열로 보존하며 서로 합치거나 재해석하지 않는다."
        ),
    }
    write_json(output_dir / MANIFEST_FILENAME, manifest)

    print(f"통합 후보: {len(integrated)}개")
    print(f"사용 가능: {len(usable)}개")
    print(f"우리말샘 신규 조회 대상: {len(lookup_rows)}개")
    print(f"기존 미검색·오류 보류: {len(unresolved)}개")
    print(f"결과 폴더: {project_relative_path(output_dir)}")


if __name__ == "__main__":
    main()
