# expand_legal_term_components.py
"""
Description: 우리말샘에서 전체 표현을 찾지 못한 법률용어를 명사 구성 요소로
분해하고, 구성 요소의 정확 일치 정의를 원래 표현과 별도로 연결한다.
Author: choeminju
Date: 2026-09-27
Before:
    - 판례·법령 통합 용어와 우리말샘 전체 표현 조회 결과가 준비된 상태.

After:
    - 구성 용어 조회 후보, 구성 정의 연결 결과, 미해결 표현과 manifest가 생성.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from kiwipiepy import Kiwi


PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOCAL_DATA_ROOT = PROJECT_ROOT / "local_data"
DEFAULT_INTEGRATED_PATH = (
    LOCAL_DATA_ROOT
    / "legal_terms"
    / "integrated_v01"
    / "integrated_legal_term_candidates.jsonl"
)
DEFAULT_OUTPUT_DIR = LOCAL_DATA_ROOT / "legal_terms" / "component_expansion_v01"

COMPONENT_CANDIDATES_FILENAME = "component_candidates.jsonl"
COMPONENT_LOOKUP_FILENAME = "component_lookup_candidates.jsonl"
EXPANDED_TERMS_FILENAME = "expanded_legal_terms.jsonl"
UNRESOLVED_TERMS_FILENAME = "unresolved_after_component_expansion.jsonl"
MANIFEST_FILENAME = "manifest.json"
SCHEMA_VERSION = "legal_term_component_expansion.v0.1"
WHITESPACE_RE = re.compile(r"\s+")
NOMINAL_TAG_PREFIXES = ("NN", "NR", "SL", "SH", "SN", "XSN")
MAX_MORPHOLOGY_NGRAM = 4


def parse_args() -> argparse.Namespace:
    """구성 용어 확장 입력과 출력 경로를 정의한다."""
    parser = argparse.ArgumentParser(
        description="미검색 법률 복합 표현을 구성 용어로 분해합니다."
    )
    parser.add_argument(
        "--integrated-path",
        type=Path,
        default=DEFAULT_INTEGRATED_PATH,
    )
    parser.add_argument(
        "--component-definitions-path",
        type=Path,
        help="우리말샘 구성 용어 수집 결과 JSONL. 없으면 조회 후보만 생성합니다.",
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
    """NFKC와 소문자 변환 후 모든 공백을 제거한다."""
    text = unicodedata.normalize("NFKC", str(value or "")).lower().strip()
    return WHITESPACE_RE.sub("", text)


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


def nominal_runs(kiwi: Kiwi, term: str) -> list[list[str]]:
    """비명사 형태소를 경계로 연속된 명사성 형태소 묶음을 만든다."""
    runs: list[list[str]] = []
    current: list[str] = []
    for token in kiwi.tokenize(term):
        if token.tag.startswith(NOMINAL_TAG_PREFIXES):
            current.append(token.form)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def morphology_components(kiwi: Kiwi, term: str, full_key: str) -> dict[str, set[str]]:
    """명사 단독 및 최대 네 형태소의 연속 복합명사 후보를 만든다."""
    candidates: dict[str, set[str]] = defaultdict(set)
    for run in nominal_runs(kiwi, term):
        for start in range(len(run)):
            max_size = min(MAX_MORPHOLOGY_NGRAM, len(run) - start)
            for size in range(1, max_size + 1):
                component = normalize_term("".join(run[start : start + size]))
                if len(component) < 2 or component == full_key:
                    continue
                method = "morphology_token" if size == 1 else "morphology_ngram"
                candidates[component].add(method)
    return candidates


def find_occurrences(text: str, pattern: str) -> list[tuple[int, int]]:
    """겹치는 경우를 포함해 문자열 안의 모든 패턴 위치를 찾는다."""
    result = []
    start = 0
    while True:
        index = text.find(pattern, start)
        if index < 0:
            return result
        result.append((index, index + len(pattern)))
        start = index + 1


def generate_component_candidates(
    integrated_rows: list[dict[str, Any]],
    kiwi: Kiwi,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, set[str]]]]:
    """미검색 표현별 후보와 후보별 부모 표현을 함께 만든다."""
    unresolved = [
        row
        for row in integrated_rows
        if row.get("urimalsaem_lookup_status") == "not_found"
    ]
    exact_keys = {
        str(row["match_key"])
        for row in integrated_rows
        if row.get("urimalsaem_lookup_status") == "exact"
    }
    components_by_parent: dict[str, dict[str, set[str]]] = {}
    parents_by_component: dict[str, set[str]] = defaultdict(set)
    methods_by_component: dict[str, set[str]] = defaultdict(set)
    display_by_component: dict[str, str] = {}

    for row in unresolved:
        parent_key = str(row["match_key"])
        candidates = morphology_components(kiwi, str(row["term"]), parent_key)
        for exact_key in exact_keys:
            if len(exact_key) >= 2 and exact_key != parent_key and exact_key in parent_key:
                candidates.setdefault(exact_key, set()).add("known_exact_substring")
        components_by_parent[parent_key] = candidates
        for component, methods in candidates.items():
            parents_by_component[component].add(parent_key)
            methods_by_component[component].update(methods)
            display_by_component.setdefault(component, component)

    candidate_rows = [
        {
            "match_key": component,
            "term": display_by_component[component],
            "parent_count": len(parents_by_component[component]),
            "parent_match_keys": sorted(parents_by_component[component]),
            "extraction_methods": sorted(methods_by_component[component]),
        }
        for component in sorted(parents_by_component)
    ]
    return candidate_rows, components_by_parent


def build_definition_index(
    integrated_rows: Iterable[dict[str, Any]],
    component_definition_rows: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """기존 정의와 새 구성 용어 정의를 동일 스키마로 색인한다."""
    result = {}
    for row in integrated_rows:
        if row.get("urimalsaem_lookup_status") != "exact":
            continue
        definitions = list((row.get("definitions") or {}).get("urimalsaem", []))
        if definitions:
            result[str(row["match_key"])] = {
                "term": row["term"],
                "definitions": definitions,
                "definition_source": "integrated_glossary",
            }
    for row in component_definition_rows:
        if row.get("lookup_status") != "exact" or not row.get("definitions"):
            continue
        key = normalize_term(row.get("match_key") or row.get("term"))
        result[key] = {
            "term": row.get("term") or key,
            "definitions": list(row["definitions"]),
            "definition_source": "component_lookup",
        }
    return result


def select_lookup_candidates(
    candidates: Iterable[dict[str, Any]],
    integrated_rows: Iterable[dict[str, Any]],
    component_definition_rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """통합 사전이나 이전 조회 결과에 없는 구성 용어만 반환한다."""
    known_keys = {str(row["match_key"]) for row in integrated_rows}
    known_keys.update(
        normalize_term(row.get("match_key") or row.get("term"))
        for row in component_definition_rows
    )
    return [row for row in candidates if row["match_key"] not in known_keys]


def select_longest_non_overlapping_components(
    parent_key: str,
    candidates: dict[str, set[str]],
    definition_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """정의가 있는 후보 중 긴 표현부터 겹치지 않게 선택한다."""
    occurrences = []
    for component, methods in candidates.items():
        if component not in definition_index:
            continue
        for start, end in find_occurrences(parent_key, component):
            occurrences.append((start, end, component, methods))
    occurrences.sort(key=lambda item: (-(item[1] - item[0]), item[0], item[2]))

    selected = []
    occupied: list[tuple[int, int]] = []
    for start, end, component, methods in occurrences:
        if any(start < used_end and end > used_start for used_start, used_end in occupied):
            continue
        definition = definition_index[component]
        selected.append(
            {
                "match_key": component,
                "term": definition["term"],
                "start": start,
                "end": end,
                "extraction_methods": sorted(methods),
                "definition_source": definition["definition_source"],
                "definitions": definition["definitions"],
            }
        )
        occupied.append((start, end))
    return sorted(selected, key=lambda row: (row["start"], row["end"]))


def build_expanded_rows(
    integrated_rows: list[dict[str, Any]],
    components_by_parent: dict[str, dict[str, set[str]]],
    definition_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """원본 통합 행을 보존하면서 구성 용어 정의 연결 필드를 추가한다."""
    result = []
    for source in integrated_rows:
        row = dict(source)
        parent_key = str(row["match_key"])
        components = select_longest_non_overlapping_components(
            parent_key,
            components_by_parent.get(parent_key, {}),
            definition_index,
        )
        row["component_definitions"] = components
        if row.get("urimalsaem_lookup_status") == "exact":
            row["component_definition_status"] = "full_term_exact"
        elif components:
            row["component_definition_status"] = "components_available"
        else:
            row["component_definition_status"] = "unresolved"
        result.append(row)
    return result


def main() -> None:
    """구성 용어를 생성하고 선택적으로 수집 정의까지 연결한다."""
    args = parse_args()
    integrated_path = args.integrated_path.resolve()
    component_definitions_path = (
        args.component_definitions_path.resolve()
        if args.component_definitions_path
        else None
    )
    output_dir = args.output_dir.resolve()
    if not integrated_path.exists():
        raise FileNotFoundError(f"통합 용어 파일이 없습니다: {integrated_path}")
    if component_definitions_path and not component_definitions_path.exists():
        raise FileNotFoundError(
            f"구성 용어 정의 파일이 없습니다: {component_definitions_path}"
        )

    integrated_rows = read_jsonl(integrated_path)
    component_definition_rows = (
        read_jsonl(component_definitions_path) if component_definitions_path else []
    )
    candidates, components_by_parent = generate_component_candidates(
        integrated_rows,
        Kiwi(),
    )
    lookup_candidates = select_lookup_candidates(
        candidates,
        integrated_rows,
        component_definition_rows,
    )
    definition_index = build_definition_index(
        integrated_rows,
        component_definition_rows,
    )
    expanded_rows = build_expanded_rows(
        integrated_rows,
        components_by_parent,
        definition_index,
    )
    unresolved_rows = [
        row
        for row in expanded_rows
        if row["component_definition_status"] == "unresolved"
    ]

    outputs = {
        COMPONENT_CANDIDATES_FILENAME: candidates,
        COMPONENT_LOOKUP_FILENAME: lookup_candidates,
        EXPANDED_TERMS_FILENAME: expanded_rows,
        UNRESOLVED_TERMS_FILENAME: unresolved_rows,
    }
    for filename, rows in outputs.items():
        write_jsonl(output_dir / filename, rows)

    status_counts = Counter(
        row["component_definition_status"] for row in expanded_rows
    )
    input_paths = [integrated_path]
    if component_definitions_path:
        input_paths.append(component_definitions_path)
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
            "integrated_rows": len(integrated_rows),
            "component_candidate_rows": len(candidates),
            "component_lookup_candidate_rows": len(lookup_candidates),
            "component_definition_rows": len(component_definition_rows),
            "unresolved_rows": len(unresolved_rows),
            "component_definition_status": dict(sorted(status_counts.items())),
        },
        "outputs": {
            filename: {
                "path": project_relative_path(output_dir / filename),
                "sha256": file_sha256(output_dir / filename),
                "row_count": len(rows),
            }
            for filename, rows in outputs.items()
        },
        "definition_policy": (
            "전체 표현 정확 일치 정의와 구성 용어 정의를 분리한다. 구성 용어는 "
            "긴 정확 일치 표현부터 겹치지 않게 선택하며 전체 표현의 정의로 합성하지 않는다."
        ),
    }
    write_json(output_dir / MANIFEST_FILENAME, manifest)

    print(f"구성 용어 후보: {len(candidates)}개")
    print(f"우리말샘 신규 조회 대상: {len(lookup_candidates)}개")
    print(f"구성 정의 연결 후 미해결: {len(unresolved_rows)}개")
    print(f"결과 폴더: {project_relative_path(output_dir)}")


if __name__ == "__main__":
    main()
