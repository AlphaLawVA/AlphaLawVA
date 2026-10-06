# review_legal_category_terms.py
"""
Description: 우리말샘 법률 분야 정의가 연결된 전체 용어와 구성 용어를
보수적으로 검토하여 쉬운 정의 생성 대상, 보류 대상, 제외 대상을 분리한다.
Author: choeminju
Date: 2026-10-05
Before:
    - 통합 용어와 구성 용어의 우리말샘 정의 연결 결과가 준비된 상태.
After:
    - 원본을 보존한 채 법률 분야 정의 용어의 판정 결과와 통계가 생성.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOCAL_DATA_ROOT = PROJECT_ROOT / "local_data"
DEFAULT_FULL_TERMS_PATH = (
    LOCAL_DATA_ROOT / "legal_terms" / "integrated_v01" / "usable_legal_terms.jsonl"
)
DEFAULT_COMPONENT_TERMS_PATH = (
    LOCAL_DATA_ROOT
    / "legal_terms"
    / "urimalsaem_component_definitions_v01"
    / "urimalsaem_term_definitions.jsonl"
)
DEFAULT_EXPANDED_TERMS_PATH = (
    LOCAL_DATA_ROOT
    / "legal_terms"
    / "component_expansion_v01"
    / "expanded_legal_terms.jsonl"
)
DEFAULT_OUTPUT_DIR = (
    LOCAL_DATA_ROOT / "legal_terms" / "reviews" / "legal_category_terms_v01"
)

ALL_REVIEW_FILENAME = "legal_category_term_review.jsonl"
REVIEW_CSV_FILENAME = "legal_category_term_review.csv"
READY_FILENAME = "easy_definition_ready_terms.jsonl"
DEFERRED_FILENAME = "deferred_terms.jsonl"
EXCLUDED_FILENAME = "excluded_terms.jsonl"
MANIFEST_FILENAME = "manifest.json"
SCHEMA_VERSION = "legal_category_term_review.v0.1"
REVIEW_METHOD = "codex_conservative_manual_rules_v01"

# 일반 성인에게 별도 용어 풀이가 불필요하다고 판단할 수 있는 매우 쉬운 말만 둔다.
# 조금이라도 법적 맥락에서 의미가 달라질 수 있는 말은 이 목록에 넣지 않는다.
EASY_GENERAL_TERMS = frozenset(
    {
        "계약",
        "법령",
        "법률",
        "법원",
        "변호사",
        "부동산",
        "사건",
        "사실",
        "상품",
        "서명",
        "신청",
        "요구",
        "요소",
        "의무",
        "재판",
        "지급",
        "주장",
        "토지",
        "화재",
        "확인",
    }
)

# 주거용 부동산 계약 서비스의 용어 풀이 범위와 명백히 거리가 먼 전문 분야다.
# 다른 민사·재산 문맥에도 쓰일 수 있는 용어는 보수적으로 제외하지 않는다.
OUT_OF_SCOPE_TERMS = frozenset(
    {
        "디자인권",
        "면접교섭권",
        "배타적발행권",
        "병역의무",
        "사립학교법",
        "상표권",
        "선박등기",
        "선박압류",
        "식품위생법",
        "신주발행무효",
        "실용신안권",
        "약혼해제",
        "영주권",
        "온천법",
        "위헌법률심판",
        "의료법인",
        "의장권",
        "이혼",
        "입양",
        "저작권",
        "저작인접권",
        "저작재산권",
        "출입국관리법",
        "출판권",
        "특허권",
        "특허법원",
        "특허소송",
        "학교법인",
        "혼인신고",
    }
)

# 판례 문맥에서 실제 단어가 아니라 다른 표현의 내부 문자열로 잡혔거나,
# 연결된 법률 분야 뜻과 사용된 뜻이 명백히 다른 경우다.
FALSE_FULL_TERMS = frozenset({"무인", "소요", "전적", "주지", "지도"})
FALSE_COMPONENT_TERMS = frozenset({"기소", "소각"})

# 용어 자체는 보존하지만 현재 연결된 우리말샘 법률 정의를 그대로 사용하면
# 실제 판례·상위 표현 문맥과 다른 뜻을 전달할 가능성이 높은 항목이다.
FULL_DEFINITION_REVIEW_TERMS = frozenset(
    {
        "가중",
        "경과",
        "공정",
        "능력",
        "신문",
        "인가",
        "자조",
        "작위",
        "지시",
        "지역",
    }
)
COMPONENT_DEFINITION_REVIEW_TERMS = frozenset(
    {
        "가공",
        "검인",
        "결정",
        "과세특례",
        "공탁",
        "면제",
        "상호",
        "소액",
        "심판관",
        "압류재산",
        "위탁계약",
        "이사",
        "인지",
        "재심",
        "전문",
        "제외신고",
        "중과",
        "착수",
        "후견",
    }
)


def parse_args() -> argparse.Namespace:
    """검토 입력과 출력 경로를 정의한다."""
    parser = argparse.ArgumentParser(
        description="우리말샘 법률 분야 정의 용어를 보수적으로 검토합니다."
    )
    parser.add_argument("--full-terms-path", type=Path, default=DEFAULT_FULL_TERMS_PATH)
    parser.add_argument(
        "--component-terms-path",
        type=Path,
        default=DEFAULT_COMPONENT_TERMS_PATH,
    )
    parser.add_argument(
        "--expanded-terms-path",
        type=Path,
        default=DEFAULT_EXPANDED_TERMS_PATH,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def now_utc_iso() -> str:
    """현재 UTC 시각을 ISO 문자열로 반환한다."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 객체 목록을 읽는다."""
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
    """JSON 객체를 들여쓰기하여 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_review_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """전수 검토 결과를 필터링하기 쉬운 CSV로 저장한다."""
    fieldnames = [
        "review_status",
        "include_in_glossary",
        "easy_definition_ready",
        "source_type",
        "term",
        "match_key",
        "parent_match_keys",
        "legal_definition_count",
        "legal_definitions",
        "definition_categories",
        "precedent_case_count",
        "statute_document_count",
        "review_reason",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            definitions = row["legal_definitions"]
            writer.writerow(
                {
                    "review_status": row["review_status"],
                    "include_in_glossary": row["include_in_glossary"],
                    "easy_definition_ready": row["easy_definition_ready"],
                    "source_type": row["source_type"],
                    "term": row["term"],
                    "match_key": row["match_key"],
                    "parent_match_keys": " | ".join(row["parent_match_keys"]),
                    "legal_definition_count": row["legal_definition_count"],
                    "legal_definitions": " | ".join(
                        str(definition.get("definition") or "")
                        for definition in definitions
                    ),
                    "definition_categories": " | ".join(
                        sorted(
                            {
                                str(definition.get("category") or "")
                                for definition in definitions
                                if definition.get("category")
                            }
                        )
                    ),
                    "precedent_case_count": row["precedent_case_count"],
                    "statute_document_count": row["statute_document_count"],
                    "review_reason": row["review_reason"],
                }
            )


def file_sha256(path: Path) -> str:
    """파일 SHA-256을 계산한다."""
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


def legal_definitions(row: dict[str, Any], source_type: str) -> list[dict[str, Any]]:
    """행에서 우리말샘 법률 분야 정의만 반환한다."""
    if source_type == "full_term":
        definitions = (row.get("definitions") or {}).get("urimalsaem", [])
    else:
        definitions = row.get("definitions", [])
    return [definition for definition in definitions if definition.get("is_legal_category")]


def build_component_parent_index(
    expanded_rows: Iterable[dict[str, Any]],
) -> dict[str, list[str]]:
    """구성 용어가 실제 연결된 상위 미검색 표현을 색인한다."""
    parents: dict[str, set[str]] = defaultdict(set)
    for row in expanded_rows:
        parent_key = str(row.get("match_key") or "")
        for component in row.get("component_definitions", []):
            component_key = str(component.get("match_key") or "")
            if component_key and parent_key:
                parents[component_key].add(parent_key)
    return {key: sorted(values) for key, values in parents.items()}


def classify_term(
    match_key: str,
    source_type: str,
    definition_count: int,
    parent_match_keys: list[str],
) -> tuple[str, bool, bool, str]:
    """용어를 보수적으로 분류하고 포함·생성 가능 여부와 근거를 반환한다."""
    if match_key in EASY_GENERAL_TERMS:
        return (
            "exclude_easy_general",
            False,
            False,
            "20~30대 일반 사용자에게 별도 풀이가 불필요한 매우 쉬운 일반어",
        )
    if match_key in OUT_OF_SCOPE_TERMS:
        return (
            "exclude_out_of_scope",
            False,
            False,
            "주거용 부동산 계약 서비스와 명백히 거리가 먼 전문 분야 용어",
        )
    if source_type == "full_term" and match_key in FALSE_FULL_TERMS:
        return (
            "exclude_false_match",
            False,
            False,
            "판례에서 다른 단어 내부 문자열로 잡혔거나 연결 정의와 실제 뜻이 명백히 다름",
        )
    if source_type == "component_term" and match_key in FALSE_COMPONENT_TERMS:
        return (
            "exclude_false_match",
            False,
            False,
            "상위 표현을 잘못 분해해 만들어진 구성 용어",
        )
    if source_type == "component_term" and not parent_match_keys:
        return (
            "preserved_inactive_component",
            True,
            False,
            "현재 최장 일치 구성 용어 선택 결과에서 연결된 상위 표현이 없어 비활성 보존",
        )
    if definition_count > 1:
        return (
            "sense_selection_required",
            True,
            False,
            "법률 분야 정의가 여러 개여서 실제 판례 또는 상위 표현 문맥에 맞는 뜻 선택 필요",
        )
    if (
        source_type == "full_term" and match_key in FULL_DEFINITION_REVIEW_TERMS
    ) or (
        source_type == "component_term"
        and match_key in COMPONENT_DEFINITION_REVIEW_TERMS
    ):
        return (
            "definition_review_required",
            True,
            False,
            "용어는 유효하지만 연결된 정의가 실제 사용 문맥보다 좁거나 다른 뜻일 가능성이 큼",
        )
    if source_type == "component_term":
        return (
            "context_ready",
            True,
            True,
            "상위 미검색 표현에 연결된 단일 법률 분야 구성 용어 정의",
        )
    return (
        "ready",
        True,
        True,
        "전체 표현과 정확히 일치하는 단일 법률 분야 정의",
    )


def build_review_row(
    row: dict[str, Any],
    source_type: str,
    parent_match_keys: list[str] | None = None,
) -> dict[str, Any]:
    """원본 출처와 정의를 보존한 검토 행을 만든다."""
    parents = parent_match_keys or []
    definitions = legal_definitions(row, source_type)
    match_key = str(row.get("match_key") or row.get("term") or "")
    status, included, ready, reason = classify_term(
        match_key,
        source_type,
        len(definitions),
        parents,
    )
    precedent_source = row.get("precedent_source") or row
    statute_source = row.get("statute_source") or {}
    return {
        "match_key": match_key,
        "term": str(row.get("term") or match_key),
        "source_type": source_type,
        "parent_match_keys": parents,
        "parent_count": len(parents),
        "source_domains": list(row.get("source_domains") or []),
        "precedent_case_count": int(
            precedent_source.get("total_case_count") or row.get("total_case_count") or 0
        ),
        "statute_document_count": int(statute_source.get("document_count") or 0),
        "legal_definition_count": len(definitions),
        "legal_definitions": definitions,
        "review_status": status,
        "include_in_glossary": included,
        "easy_definition_ready": ready,
        "review_reason": reason,
        "review_method": REVIEW_METHOD,
    }


def build_review_rows(
    full_rows: Iterable[dict[str, Any]],
    component_rows: Iterable[dict[str, Any]],
    parent_index: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """법률 분야 정의가 있는 전체 용어와 구성 용어를 전부 분류한다."""
    result = []
    for row in full_rows:
        if legal_definitions(row, "full_term"):
            result.append(build_review_row(row, "full_term"))
    for row in component_rows:
        if legal_definitions(row, "component_term"):
            key = str(row.get("match_key") or row.get("term") or "")
            result.append(
                build_review_row(row, "component_term", parent_index.get(key, []))
            )
    return sorted(result, key=lambda value: (value["match_key"], value["source_type"]))


def validate_review_rows(rows: list[dict[str, Any]]) -> None:
    """중복과 판정 불변식을 검증한다."""
    keys = [(row["source_type"], row["match_key"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("검토 결과에 source_type과 match_key 중복이 있습니다.")
    for row in rows:
        if row["legal_definition_count"] < 1:
            raise ValueError(f"법률 분야 정의가 없는 행이 포함되었습니다: {row['match_key']}")
        if row["easy_definition_ready"] and not row["include_in_glossary"]:
            raise ValueError(f"제외 용어가 생성 준비로 표시되었습니다: {row['match_key']}")
        if row["easy_definition_ready"] and row["legal_definition_count"] != 1:
            raise ValueError(f"복수 정의 용어가 생성 준비로 표시되었습니다: {row['match_key']}")


def build_manifest(
    rows: list[dict[str, Any]],
    input_paths: list[Path],
    output_paths: list[Path],
) -> dict[str, Any]:
    """입력 스냅숏과 판정 통계를 기록한다."""
    status_counts = Counter(row["review_status"] for row in rows)
    source_type_counts = Counter(row["source_type"] for row in rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "review_method": REVIEW_METHOD,
        "created_at": now_utc_iso(),
        "policy": {
            "ambiguous_terms": "preserve",
            "excluded_scope": [
                "obviously easy general terms",
                "clear false matches",
                "clearly out-of-scope specialist terms",
            ],
            "source_rows_mutated": False,
        },
        "inputs": [
            {
                "path": project_relative_path(path),
                "sha256": file_sha256(path),
            }
            for path in input_paths
        ],
        "outputs": [project_relative_path(path) for path in output_paths],
        "counts": {
            "reviewed_total": len(rows),
            "included_total": sum(row["include_in_glossary"] for row in rows),
            "easy_definition_ready_total": sum(
                row["easy_definition_ready"] for row in rows
            ),
            "deferred_total": sum(
                row["include_in_glossary"] and not row["easy_definition_ready"]
                for row in rows
            ),
            "excluded_total": sum(not row["include_in_glossary"] for row in rows),
            "source_type": dict(sorted(source_type_counts.items())),
            "review_status": dict(sorted(status_counts.items())),
        },
    }


def run_review(
    full_terms_path: Path,
    component_terms_path: Path,
    expanded_terms_path: Path,
    output_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """검토를 실행하고 분리 결과와 manifest를 저장한다."""
    full_rows = read_jsonl(full_terms_path)
    component_rows = read_jsonl(component_terms_path)
    expanded_rows = read_jsonl(expanded_terms_path)
    parent_index = build_component_parent_index(expanded_rows)
    review_rows = build_review_rows(full_rows, component_rows, parent_index)
    validate_review_rows(review_rows)

    ready_rows = [row for row in review_rows if row["easy_definition_ready"]]
    deferred_rows = [
        row
        for row in review_rows
        if row["include_in_glossary"] and not row["easy_definition_ready"]
    ]
    excluded_rows = [row for row in review_rows if not row["include_in_glossary"]]

    output_paths = [
        output_dir / ALL_REVIEW_FILENAME,
        output_dir / REVIEW_CSV_FILENAME,
        output_dir / READY_FILENAME,
        output_dir / DEFERRED_FILENAME,
        output_dir / EXCLUDED_FILENAME,
        output_dir / MANIFEST_FILENAME,
    ]
    write_jsonl(output_paths[0], review_rows)
    write_review_csv(output_paths[1], review_rows)
    write_jsonl(output_paths[2], ready_rows)
    write_jsonl(output_paths[3], deferred_rows)
    write_jsonl(output_paths[4], excluded_rows)
    manifest = build_manifest(
        review_rows,
        [full_terms_path, component_terms_path, expanded_terms_path],
        output_paths,
    )
    write_json(output_paths[5], manifest)
    return review_rows, manifest


def main() -> None:
    """CLI 진입점."""
    args = parse_args()
    _, manifest = run_review(
        args.full_terms_path,
        args.component_terms_path,
        args.expanded_terms_path,
        args.output_dir,
    )
    counts = manifest["counts"]
    print(f"검토 완료: {counts['reviewed_total']}개")
    print(f"- 쉬운 정의 생성 준비: {counts['easy_definition_ready_total']}개")
    print(f"- 보존·추가 검토: {counts['deferred_total']}개")
    print(f"- 제외: {counts['excluded_total']}개")
    print(f"- 결과 폴더: {args.output_dir}")


if __name__ == "__main__":
    main()
