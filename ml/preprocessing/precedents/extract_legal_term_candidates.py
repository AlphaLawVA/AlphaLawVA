# extract_legal_term_candidates.py
"""
Description: 공식 법령용어 목록과 최종 판례의 MVP 노출 필드를 대조하여
판례별·필드별 용어 등장 횟수와 검수용 후보 목록을 생성한다.
Author: choeminju
Date: 2026-09-20
Before:
    - 공식 법령용어 카탈로그와 final_cases 판례 JSON이 수집되어 있는 상태.

After:
    - local_data/precedents/legal_terms/matched_candidates/에 매칭 결과와 통계가 생성.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import unicodedata
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOCAL_DATA_ROOT = PROJECT_ROOT / "local_data"
DEFAULT_FINAL_CASES_DIR = (
    LOCAL_DATA_ROOT / "precedents" / "processed" / "final_cases"
)
DEFAULT_CATALOG_PATH = (
    LOCAL_DATA_ROOT
    / "precedents"
    / "legal_terms"
    / "official_catalog"
    / "official_legal_terms.jsonl"
)
DEFAULT_OUTPUT_DIR = (
    LOCAL_DATA_ROOT / "precedents" / "legal_terms" / "matched_candidates"
)
MATCH_FIELDS = (
    "사건명",
    "생성요약",
    "판시사항",
    "판결요지",
    "주문",
    "청구취지",
)
SCHEMA_VERSION = "precedent_legal_term_candidates.v0.1"
MIN_MATCH_KEY_LENGTH = 2
HANGUL_RE = re.compile(r"[가-힣]")
HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


def parse_args() -> argparse.Namespace:
    """판례 법률용어 후보 추출 옵션을 정의한다."""
    parser = argparse.ArgumentParser(
        description="공식 법령용어가 판례 MVP 필드에 등장하는 빈도를 집계합니다."
    )
    parser.add_argument(
        "--final-cases-dir",
        type=Path,
        default=DEFAULT_FINAL_CASES_DIR,
        help="최종 판례 JSON 폴더.",
    )
    parser.add_argument(
        "--catalog-path",
        type=Path,
        default=DEFAULT_CATALOG_PATH,
        help="공식 법령용어 JSONL 경로.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="후보 목록, 판례별 매칭, manifest 저장 폴더.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="테스트용 판례 처리 개수 제한.",
    )
    return parser.parse_args()


def now_utc_iso() -> str:
    """현재 UTC 시각을 ISO 문자열로 반환한다."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def project_relative_path(path: Path) -> str:
    """프로젝트 내부 경로는 상대경로로 기록한다."""
    resolved = path.resolve()
    if resolved.is_relative_to(PROJECT_ROOT):
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    return resolved.as_posix()


def normalize_match_text(value: Any) -> str:
    """공식 용어 표기 변형을 묶기 위한 공백 없는 키를 만든다."""
    return WHITESPACE_RE.sub("", normalize_source_text(value))


def normalize_source_text(value: Any) -> str:
    """HTML과 유니코드를 정리하되 단어 경계인 공백은 보존한다."""
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", " ")
    text = html.unescape(HTML_TAG_RE.sub(" ", text))
    text = unicodedata.normalize("NFKC", text).lower()
    return WHITESPACE_RE.sub(" ", text).strip()


def is_matchable_term(match_key: str) -> bool:
    """MVP 한국어 법률용어 후보로 대조할 수 있는 표기인지 확인한다."""
    return len(match_key) >= MIN_MATCH_KEY_LENGTH and bool(HANGUL_RE.search(match_key))


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    """JSONL 파일의 객체를 한 줄씩 반환한다."""
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL {line_number}번째 줄이 객체가 아닙니다: {path}")
            yield value


def choose_representative_term(variants: set[str]) -> str:
    """검수표에 표시할 공식 표기 하나를 결정한다."""
    return min(
        variants,
        key=lambda term: (
            not bool(re.search(r"[가-힣]\s+[가-힣]", term)),
            term.startswith(("(", ")", "-", "·", "ㆍ")),
            len(term),
            term,
        ),
    )


def load_term_groups(
    catalog_path: Path,
) -> tuple[dict[str, dict[str, Any]], Counter[str]]:
    """공식 표기 변형을 동일 매칭 키 기준으로 묶는다."""
    grouped: dict[str, dict[str, set[str] | int]] = {}
    excluded = Counter()

    for row in read_jsonl(catalog_path):
        term = str(row.get("term") or "").strip()
        match_key = normalize_match_text(term)
        if not term:
            excluded["empty_term"] += 1
            continue
        if len(match_key) < MIN_MATCH_KEY_LENGTH:
            excluded["too_short"] += 1
            continue
        if not HANGUL_RE.search(match_key):
            excluded["no_hangul"] += 1
            continue

        group = grouped.setdefault(
            match_key,
            {
                "variants": set(),
                "source_term_ids": set(),
                "dictionary_type_codes": set(),
                "law_type_codes": set(),
                "official_entry_count": 0,
            },
        )
        group["variants"].add(term)  # type: ignore[union-attr]
        group["source_term_ids"].update(  # type: ignore[union-attr]
            str(value).strip()
            for value in row.get("source_term_ids", [])
            if str(value).strip()
        )
        for field, target in (
            ("dictionary_type_code", "dictionary_type_codes"),
            ("law_type_code", "law_type_codes"),
        ):
            for value in str(row.get(field) or "").split(","):
                if value.strip():
                    group[target].add(value.strip())  # type: ignore[union-attr]
        group["official_entry_count"] += 1  # type: ignore[operator]

    result: dict[str, dict[str, Any]] = {}
    for match_key, group in grouped.items():
        variants = set(group["variants"])
        result[match_key] = {
            "match_key": match_key,
            "term": choose_representative_term(variants),
            "official_variants": sorted(variants),
            "source_term_ids": sorted(
                set(group["source_term_ids"]),
                key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value),
            ),
            "dictionary_type_codes": sorted(set(group["dictionary_type_codes"])),
            "law_type_codes": sorted(set(group["law_type_codes"])),
            "official_entry_count": int(group["official_entry_count"]),
        }
    return result, excluded


class AhoCorasickMatcher:
    """외부 라이브러리 없이 다수 용어를 한 번에 찾는 문자열 매처."""

    def __init__(self, patterns: Mapping[str, str] | Iterable[str]) -> None:
        self.transitions: list[dict[str, int]] = [{}]
        self.failures: list[int] = [0]
        self.outputs: list[list[tuple[str, int]]] = [[]]
        pattern_items = patterns.items() if isinstance(patterns, Mapping) else (
            (pattern, pattern) for pattern in patterns
        )
        for pattern, match_key in pattern_items:
            self._add(pattern, match_key)
        self._build_failures()

    def _new_state(self) -> int:
        self.transitions.append({})
        self.failures.append(0)
        self.outputs.append([])
        return len(self.transitions) - 1

    def _add(self, pattern: str, match_key: str) -> None:
        state = 0
        for character in pattern:
            next_state = self.transitions[state].get(character)
            if next_state is None:
                next_state = self._new_state()
                self.transitions[state][character] = next_state
            state = next_state
        self.outputs[state].append((match_key, len(pattern)))

    def _build_failures(self) -> None:
        queue: deque[int] = deque()
        for state in self.transitions[0].values():
            queue.append(state)

        while queue:
            state = queue.popleft()
            for character, next_state in self.transitions[state].items():
                queue.append(next_state)
                failure = self.failures[state]
                while failure and character not in self.transitions[failure]:
                    failure = self.failures[failure]
                self.failures[next_state] = self.transitions[failure].get(character, 0)
                inherited = self.outputs[self.failures[next_state]]
                if inherited:
                    self.outputs[next_state].extend(inherited)

    def count(self, text: str) -> Counter[str]:
        """겹치는 위치에서는 가장 긴 공식 용어만 골라 등장 횟수를 반환한다."""
        matches: list[tuple[int, int, str]] = []
        state = 0
        for end, character in enumerate(text, start=1):
            while state and character not in self.transitions[state]:
                state = self.failures[state]
            state = self.transitions[state].get(character, 0)
            for match_key, pattern_length in self.outputs[state]:
                matches.append((end - pattern_length, end, match_key))

        selected: list[tuple[int, int, str]] = []
        for start, end, match_key in sorted(
            matches,
            key=lambda match: (-(match[1] - match[0]), match[0], match[2]),
        ):
            if any(start < chosen_end and end > chosen_start for chosen_start, chosen_end, _ in selected):
                continue
            selected.append((start, end, match_key))
        return Counter(match_key for _, _, match_key in selected)


def build_match_patterns(term_groups: dict[str, dict[str, Any]]) -> dict[str, str]:
    """공식 띄어쓰기 변형과 붙여 쓴 표기를 매칭 패턴으로 만든다."""
    patterns: dict[str, str] = {}
    for match_key, group in term_groups.items():
        patterns[match_key] = match_key
        for variant in group["official_variants"]:
            normalized_variant = normalize_source_text(variant)
            if normalized_variant:
                patterns[normalized_variant] = match_key
    return patterns


def iter_case_paths(final_cases_dir: Path, limit: int | None) -> list[Path]:
    """판례일련번호 순서로 final_cases 경로를 반환한다."""
    paths = sorted(
        final_cases_dir.glob("*.json"),
        key=lambda path: int(path.stem) if path.stem.isdigit() else path.stem,
    )
    return paths[:limit] if limit is not None else paths


def build_candidate_rows(
    term_groups: dict[str, dict[str, Any]],
    case_paths: list[Path],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """판례 필드를 검색해 후보별·판례별 통계를 생성한다."""
    matcher = AhoCorasickMatcher(build_match_patterns(term_groups))
    occurrence_by_field: dict[str, Counter[str]] = {
        key: Counter() for key in term_groups
    }
    cases_by_field: dict[str, dict[str, set[str]]] = {
        key: {field: set() for field in MATCH_FIELDS} for key in term_groups
    }
    all_cases: dict[str, set[str]] = {key: set() for key in term_groups}
    nonempty_field_case_counts = Counter()
    matched_field_case_counts = Counter()
    case_match_rows: list[dict[str, Any]] = []

    for index, case_path in enumerate(case_paths, start=1):
        case = json.loads(case_path.read_text(encoding="utf-8"))
        precedent_id = str(case.get("판례일련번호") or case_path.stem)
        matched_fields: dict[str, list[dict[str, Any]]] = {}

        for field in MATCH_FIELDS:
            text = normalize_source_text(case.get(field))
            if not text:
                continue
            nonempty_field_case_counts[field] += 1
            counts = matcher.count(text)
            if not counts:
                continue
            matched_field_case_counts[field] += 1
            field_matches = []
            for match_key, count in counts.items():
                occurrence_by_field[match_key][field] += count
                cases_by_field[match_key][field].add(precedent_id)
                all_cases[match_key].add(precedent_id)
                field_matches.append(
                    {
                        "match_key": match_key,
                        "term": term_groups[match_key]["term"],
                        "occurrence_count": count,
                    }
                )
            matched_fields[field] = sorted(
                field_matches,
                key=lambda row: (-len(row["match_key"]), row["term"]),
            )

        if matched_fields:
            case_match_rows.append(
                {
                    "precedent_id": precedent_id,
                    "case_no": str(case.get("사건번호") or ""),
                    "case_name": str(case.get("사건명") or ""),
                    "matched_fields": matched_fields,
                }
            )
        if index % 500 == 0 or index == len(case_paths):
            print(f"판례 매칭 {index}/{len(case_paths)}", flush=True)

    candidate_rows = []
    for match_key, group in term_groups.items():
        if not all_cases[match_key]:
            continue
        field_stats = {
            field: {
                "case_count": len(cases_by_field[match_key][field]),
                "occurrence_count": occurrence_by_field[match_key][field],
            }
            for field in MATCH_FIELDS
        }
        candidate_rows.append(
            {
                **group,
                "total_case_count": len(all_cases[match_key]),
                "total_occurrence_count": sum(occurrence_by_field[match_key].values()),
                "field_stats": field_stats,
                "review_status": "pending",
                "review_note": "",
            }
        )

    candidate_rows.sort(
        key=lambda row: (
            -row["field_stats"]["생성요약"]["case_count"],
            -row["total_case_count"],
            -len(row["match_key"]),
            row["term"],
        )
    )
    stats = {
        "nonempty_field_case_counts": dict(nonempty_field_case_counts),
        "matched_field_case_counts": dict(matched_field_case_counts),
        "matched_case_count": len(case_match_rows),
        "automaton_state_count": len(matcher.transitions),
    }
    return candidate_rows, case_match_rows, stats


def write_json(path: Path, value: Any) -> None:
    """JSON 파일을 원자적으로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """JSONL 파일을 원자적으로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary_path.replace(path)


def write_review_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """사람이 정렬·검수하기 쉬운 평면 CSV를 저장한다."""
    fieldnames = [
        "term",
        "official_variants",
        "match_key",
        "total_case_count",
        "total_occurrence_count",
    ]
    for field in MATCH_FIELDS:
        fieldnames.extend([f"{field}_case_count", f"{field}_occurrence_count"])
    fieldnames.extend(
        [
            "source_term_ids",
            "dictionary_type_codes",
            "law_type_codes",
            "official_entry_count",
            "review_status",
            "review_note",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            flat = {
                "term": row["term"],
                "official_variants": " | ".join(row["official_variants"]),
                "match_key": row["match_key"],
                "total_case_count": row["total_case_count"],
                "total_occurrence_count": row["total_occurrence_count"],
                "source_term_ids": ",".join(row["source_term_ids"]),
                "dictionary_type_codes": ",".join(row["dictionary_type_codes"]),
                "law_type_codes": ",".join(row["law_type_codes"]),
                "official_entry_count": row["official_entry_count"],
                "review_status": row["review_status"],
                "review_note": row["review_note"],
            }
            for field in MATCH_FIELDS:
                flat[f"{field}_case_count"] = row["field_stats"][field]["case_count"]
                flat[f"{field}_occurrence_count"] = row["field_stats"][field][
                    "occurrence_count"
                ]
            writer.writerow(flat)
    temporary_path.replace(path)


def file_sha256(path: Path) -> str:
    """파일의 SHA-256 해시를 반환한다."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    """공식 법령용어와 판례 필드를 대조하고 검수 후보를 저장한다."""
    args = parse_args()
    final_cases_dir = args.final_cases_dir.resolve()
    catalog_path = args.catalog_path.resolve()
    output_dir = args.output_dir.resolve()
    candidate_path = output_dir / "matched_legal_terms.jsonl"
    case_matches_path = output_dir / "case_term_matches.jsonl"
    review_csv_path = output_dir / "matched_legal_terms_review.csv"
    manifest_path = output_dir / "manifest.json"

    term_groups, excluded = load_term_groups(catalog_path)
    case_paths = iter_case_paths(final_cases_dir, args.limit)
    candidate_rows, case_match_rows, match_stats = build_candidate_rows(
        term_groups,
        case_paths,
    )
    write_jsonl(candidate_path, candidate_rows)
    write_jsonl(case_matches_path, case_match_rows)
    write_review_csv(review_csv_path, candidate_rows)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": now_utc_iso(),
        "status": "completed",
        "matching_scope": list(MATCH_FIELDS),
        "excluded_scope": ["이유"],
        "normalization": "Unicode NFKC, lowercase, HTML 제거, 공백 축약",
        "minimum_match_key_length": MIN_MATCH_KEY_LENGTH,
        "matching_method": (
            "Aho-Corasick exact substring with official whitespace variants; "
            "longest non-overlapping matches"
        ),
        "catalog_path": project_relative_path(catalog_path),
        "final_cases_dir": project_relative_path(final_cases_dir),
        "processed_case_count": len(case_paths),
        "official_catalog_record_count": sum(1 for _ in read_jsonl(catalog_path)),
        "matchable_term_group_count": len(term_groups),
        "excluded_catalog_record_counts": dict(excluded),
        "matched_term_group_count": len(candidate_rows),
        **match_stats,
        "outputs": {
            "matched_legal_terms": project_relative_path(candidate_path),
            "case_term_matches": project_relative_path(case_matches_path),
            "review_csv": project_relative_path(review_csv_path),
        },
        "output_sha256": {
            candidate_path.name: file_sha256(candidate_path),
            case_matches_path.name: file_sha256(case_matches_path),
            review_csv_path.name: file_sha256(review_csv_path),
        },
        "notice": "부분 문자열 일치 결과는 공식 용어 후보이며 최종 용어 DB가 아니다.",
    }
    write_json(manifest_path, manifest)
    print(
        f"완료: 판례 {len(case_paths):,}건, "
        f"매칭 후보 {len(candidate_rows):,}개, "
        f"매칭 판례 {len(case_match_rows):,}건"
    )


if __name__ == "__main__":
    main()
