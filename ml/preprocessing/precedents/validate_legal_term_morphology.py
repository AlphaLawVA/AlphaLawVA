# validate_legal_term_morphology.py
"""
Description: 판례 필드에서 문자열로 찾은 법률용어 후보를 Kiwi 형태소 분석으로
검증하여 활용형 내부 오탐을 제외하고, 완전 오탐과 일부 오탐을 별도로 기록한다.
Author: choeminju
Date: 2026-09-22
Before:
    - 문자열 및 긴 용어 우선 규칙으로 정제된 법률용어 후보가 존재.

After:
    - 원본을 보존한 채 형태소 검증 통계, 유효 후보, 완전 오탐 및 일부 오탐
      목록이 matched_candidates_morphology_validated/ 아래에 생성.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from kiwipiepy import Kiwi

if __package__:
    from ml.preprocessing.precedents.extract_legal_term_candidates import (
        AhoCorasickMatcher,
        MATCH_FIELDS,
        PROJECT_ROOT,
        build_match_patterns,
        iter_case_paths,
        normalize_source_text,
        project_relative_path,
        read_jsonl,
        write_json,
        write_jsonl,
        write_review_csv,
    )
else:
    from extract_legal_term_candidates import (  # type: ignore[no-redef]
        AhoCorasickMatcher,
        MATCH_FIELDS,
        PROJECT_ROOT,
        build_match_patterns,
        iter_case_paths,
        normalize_source_text,
        project_relative_path,
        read_jsonl,
        write_json,
        write_jsonl,
        write_review_csv,
    )


LOCAL_DATA_ROOT = PROJECT_ROOT / "local_data"
DEFAULT_INPUT_DIR = (
    LOCAL_DATA_ROOT
    / "precedents"
    / "legal_terms"
    / "matched_candidates_noise_filtered"
)
DEFAULT_FINAL_CASES_DIR = (
    LOCAL_DATA_ROOT / "precedents" / "processed" / "final_cases"
)
DEFAULT_OUTPUT_DIR = (
    LOCAL_DATA_ROOT
    / "precedents"
    / "legal_terms"
    / "matched_candidates_morphology_validated"
)
SCHEMA_VERSION = "precedent_legal_term_morphology_validation.v0.1"
MAX_EXAMPLES_PER_TERM = 5

# 용어의 끝은 명사 또는 명사성 접사여야 한다. 표현 중간의 관형형·조사는
# '특별한 사정', 법령명처럼 유효한 용어에도 필요하므로 허용한다.
ALLOWED_TERM_TAG_PREFIXES = ("NN",)
ALLOWED_TERM_TAGS = frozenset({"NR", "NP", "SL", "SH", "SN", "XR", "XPN", "XSN"})

RULE_DESCRIPTIONS = {
    "not_token_aligned": "용어 문자열이 형태소 경계와 정확히 맞지 않음",
    "non_nominal_morpheme": "동사·형용사·조사·어미 등 비명사 형태소로 분석됨",
    "no_morpheme": "일치 구간에서 형태소를 찾지 못함",
    "overlapping_shorter_or_later": "같은 위치에서 더 긴 용어 또는 앞선 용어를 우선함",
    "short_internal_substring": "두 글자 용어가 다른 한글 단어 내부에서 발견됨",
}


def parse_args() -> argparse.Namespace:
    """형태소 검증의 입력·출력 경로와 실행 범위를 정의한다."""
    parser = argparse.ArgumentParser(
        description="판례 법률용어 후보의 실제 형태소 사용 여부를 검증합니다."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="정제된 matched_legal_terms.jsonl이 있는 폴더.",
    )
    parser.add_argument(
        "--final-cases-dir",
        type=Path,
        default=DEFAULT_FINAL_CASES_DIR,
        help="최종 판례 JSON 폴더.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="형태소 검증 결과를 새로 저장할 폴더.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="테스트용 판례 처리 개수 제한.",
    )
    parser.add_argument(
        "--max-examples",
        type=int,
        default=MAX_EXAMPLES_PER_TERM,
        help="용어별 오탐 예시 최대 저장 개수.",
    )
    return parser.parse_args()


def now_utc_iso() -> str:
    """현재 UTC 시각을 ISO 문자열로 반환한다."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_allowed_term_tag(tag: str) -> bool:
    """법률용어를 구성할 수 있는 명사성 품사인지 반환한다."""
    return tag.startswith(ALLOWED_TERM_TAG_PREFIXES) or tag in ALLOWED_TERM_TAGS


def validate_match_span(
    tokens: list[Any],
    start: int,
    end: int,
) -> tuple[bool, str | None, list[dict[str, Any]]]:
    """문자열 일치 구간이 형태소 경계와 명사성 종결 조건을 만족하는지 검증한다."""
    overlapping = [
        token
        for token in tokens
        if token.start < end and token.start + token.len > start
    ]
    token_rows = [
        {
            "form": token.form,
            "tag": token.tag,
            "start": token.start,
            "length": token.len,
        }
        for token in overlapping
    ]
    if not overlapping:
        return False, "no_morpheme", token_rows
    if overlapping[0].start != start or overlapping[-1].start + overlapping[-1].len != end:
        return False, "not_token_aligned", token_rows
    if not is_allowed_term_tag(overlapping[-1].tag):
        return False, "non_nominal_morpheme", token_rows
    return True, None, token_rows


def context_window(text: str, start: int, end: int, radius: int = 45) -> str:
    """오탐 검수용으로 일치 구간 주변 문맥을 반환한다."""
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    prefix = "..." if left else ""
    suffix = "..." if right < len(text) else ""
    return f"{prefix}{text[left:start]}[{text[start:end]}]{text[end:right]}{suffix}"


def candidate_groups(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """후보 목록을 기존 매칭 패턴 생성기가 사용할 매핑으로 바꾼다."""
    return {str(row["match_key"]): dict(row) for row in rows}


def validate_candidates(
    candidates: list[dict[str, Any]],
    case_paths: list[Path],
    *,
    kiwi: Kiwi,
    max_examples: int,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    """모든 판례 필드를 재검사해 유효·완전 오탐·일부 오탐을 분리한다."""
    groups = candidate_groups(candidates)
    matcher = AhoCorasickMatcher(build_match_patterns(groups))
    valid_occurrences: dict[str, Counter[str]] = {
        key: Counter() for key in groups
    }
    valid_cases: dict[str, dict[str, set[str]]] = {
        key: {field: set() for field in MATCH_FIELDS} for key in groups
    }
    all_valid_cases: dict[str, set[str]] = {key: set() for key in groups}
    rejected_occurrences: dict[str, Counter[str]] = {
        key: Counter() for key in groups
    }
    rejected_examples: dict[str, list[dict[str, Any]]] = {
        key: [] for key in groups
    }
    global_rule_counts = Counter()
    case_match_rows = []

    for index, case_path in enumerate(case_paths, start=1):
        case = json.loads(case_path.read_text(encoding="utf-8"))
        precedent_id = str(case.get("판례일련번호") or case_path.stem)
        matched_fields: dict[str, list[dict[str, Any]]] = {}

        for field in MATCH_FIELDS:
            text = normalize_source_text(case.get(field))
            if not text:
                continue
            tokens = list(kiwi.tokenize(text))
            raw_matches, base_excluded = matcher.find_with_stats(text)
            global_rule_counts.update(base_excluded)
            valid_matches = []

            for start, end, match_key in raw_matches:
                is_valid, reason, token_rows = validate_match_span(tokens, start, end)
                if is_valid:
                    valid_matches.append((start, end, match_key))
                    continue
                assert reason is not None
                rejected_occurrences[match_key][reason] += 1
                global_rule_counts[reason] += 1
                examples = rejected_examples[match_key]
                if len(examples) < max_examples:
                    examples.append(
                        {
                            "precedent_id": precedent_id,
                            "case_no": str(case.get("사건번호") or ""),
                            "field": field,
                            "matched_text": text[start:end],
                            "context": context_window(text, start, end),
                            "morphemes": token_rows,
                            "reason": reason,
                        }
                    )

            selected, overlap_excluded = matcher.select_non_overlapping(valid_matches)
            global_rule_counts.update(overlap_excluded)
            selected_counts = Counter(match_key for _, _, match_key in selected)
            if not selected_counts:
                continue

            field_matches = []
            for match_key, count in selected_counts.items():
                valid_occurrences[match_key][field] += count
                valid_cases[match_key][field].add(precedent_id)
                all_valid_cases[match_key].add(precedent_id)
                field_matches.append(
                    {
                        "match_key": match_key,
                        "term": groups[match_key]["term"],
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
        if index % 250 == 0 or index == len(case_paths):
            print(f"형태소 검증 {index}/{len(case_paths)}", flush=True)

    kept_rows = []
    excluded_rows = []
    partial_rows = []
    for source in candidates:
        match_key = str(source["match_key"])
        field_stats = {
            field: {
                "case_count": len(valid_cases[match_key][field]),
                "occurrence_count": valid_occurrences[match_key][field],
            }
            for field in MATCH_FIELDS
        }
        valid_count = sum(valid_occurrences[match_key].values())
        rejected_count = sum(rejected_occurrences[match_key].values())
        validation = {
            "pre_morphology_total_case_count": int(source["total_case_count"]),
            "pre_morphology_total_occurrence_count": int(source["total_occurrence_count"]),
            "morphology_rejected_occurrence_count": rejected_count,
            "morphology_rejection_reasons": dict(rejected_occurrences[match_key]),
            "morphology_rejection_examples": rejected_examples[match_key],
        }
        row = {
            **source,
            "total_case_count": len(all_valid_cases[match_key]),
            "total_occurrence_count": valid_count,
            "field_stats": field_stats,
            "morphology_validation": validation,
        }
        if valid_count == 0:
            excluded_rows.append(
                {
                    **row,
                    "morphology_status": "excluded_no_valid_noun_usage",
                }
            )
        else:
            row["morphology_status"] = "valid"
            kept_rows.append(row)
            if rejected_count:
                partial_rows.append(
                    {
                        **row,
                        "morphology_status": "valid_with_rejected_occurrences",
                    }
                )

    sort_key = lambda row: (  # noqa: E731
        -row["field_stats"]["생성요약"]["case_count"],
        -row["total_case_count"],
        -len(row["match_key"]),
        row["term"],
    )
    kept_rows.sort(key=sort_key)
    partial_rows.sort(key=sort_key)
    excluded_rows.sort(
        key=lambda row: (
            -row["morphology_validation"]["pre_morphology_total_case_count"],
            row["term"],
        )
    )
    stats = {
        "processed_case_count": len(case_paths),
        "kept_candidate_count": len(kept_rows),
        "excluded_candidate_count": len(excluded_rows),
        "partially_filtered_candidate_count": len(partial_rows),
        "matched_case_count": len(case_match_rows),
        "rule_counts": dict(global_rule_counts),
    }
    return kept_rows, excluded_rows, partial_rows, case_match_rows, stats


def write_morphology_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """완전·일부 오탐 용어를 사람이 보기 쉬운 CSV로 저장한다."""
    fieldnames = [
        "term",
        "match_key",
        "morphology_status",
        "이전_판례수",
        "이전_등장수",
        "검증후_판례수",
        "검증후_등장수",
        "제외_등장수",
        "제외_사유",
        "오탐_예시",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            validation = row["morphology_validation"]
            examples = validation["morphology_rejection_examples"]
            writer.writerow(
                {
                    "term": row["term"],
                    "match_key": row["match_key"],
                    "morphology_status": row["morphology_status"],
                    "이전_판례수": validation["pre_morphology_total_case_count"],
                    "이전_등장수": validation["pre_morphology_total_occurrence_count"],
                    "검증후_판례수": row["total_case_count"],
                    "검증후_등장수": row["total_occurrence_count"],
                    "제외_등장수": validation["morphology_rejected_occurrence_count"],
                    "제외_사유": " | ".join(
                        f"{reason}:{count}"
                        for reason, count in validation[
                            "morphology_rejection_reasons"
                        ].items()
                    ),
                    "오탐_예시": " || ".join(
                        f"{example['field']}:{example['context']}"
                        for example in examples
                    ),
                }
            )
    temporary_path.replace(path)


def file_sha256(path: Path) -> str:
    """파일 SHA-256을 반환한다."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    """형태소 검증을 실행하고 원본과 분리된 결과를 저장한다."""
    args = parse_args()
    input_dir = args.input_dir.resolve()
    final_cases_dir = args.final_cases_dir.resolve()
    output_dir = args.output_dir.resolve()
    source_path = input_dir / "matched_legal_terms.jsonl"
    candidates = list(read_jsonl(source_path))
    case_paths = iter_case_paths(final_cases_dir, args.limit)

    kept, excluded, partial, case_rows, stats = validate_candidates(
        candidates,
        case_paths,
        kiwi=Kiwi(),
        max_examples=args.max_examples,
    )

    kept_path = output_dir / "matched_legal_terms.jsonl"
    case_path = output_dir / "case_term_matches.jsonl"
    review_path = output_dir / "matched_legal_terms_review.csv"
    excluded_jsonl = output_dir / "excluded_morphology_false_positive_terms.jsonl"
    excluded_csv = output_dir / "excluded_morphology_false_positive_terms.csv"
    partial_jsonl = output_dir / "partially_filtered_terms.jsonl"
    partial_csv = output_dir / "partially_filtered_terms.csv"
    manifest_path = output_dir / "manifest.json"

    write_jsonl(kept_path, kept)
    write_jsonl(case_path, case_rows)
    write_review_csv(review_path, kept)
    write_jsonl(excluded_jsonl, excluded)
    write_morphology_csv(excluded_csv, excluded)
    write_jsonl(partial_jsonl, partial)
    write_morphology_csv(partial_csv, partial)

    output_paths = (
        kept_path,
        case_path,
        review_path,
        excluded_jsonl,
        excluded_csv,
        partial_jsonl,
        partial_csv,
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": now_utc_iso(),
        "status": "completed",
        "policy": (
            "Kiwi 형태소 경계에 맞고 명사성 형태소로 끝나는 등장만 유지하며, "
            "원본 후보와 정의 데이터는 삭제하지 않음"
        ),
        "source_candidate_path": project_relative_path(source_path),
        "final_cases_dir": project_relative_path(final_cases_dir),
        "source_candidate_count": len(candidates),
        "matching_scope": list(MATCH_FIELDS),
        "allowed_term_tag_prefixes": list(ALLOWED_TERM_TAG_PREFIXES),
        "allowed_term_tags": sorted(ALLOWED_TERM_TAGS),
        "rule_descriptions": RULE_DESCRIPTIONS,
        **stats,
        "outputs": {
            "matched_legal_terms_jsonl": project_relative_path(kept_path),
            "case_term_matches_jsonl": project_relative_path(case_path),
            "matched_legal_terms_review_csv": project_relative_path(review_path),
            "excluded_false_positive_jsonl": project_relative_path(excluded_jsonl),
            "excluded_false_positive_csv": project_relative_path(excluded_csv),
            "partially_filtered_jsonl": project_relative_path(partial_jsonl),
            "partially_filtered_csv": project_relative_path(partial_csv),
        },
        "output_sha256": {path.name: file_sha256(path) for path in output_paths},
        "notice": (
            "완전 오탐은 현재 판례 필드에서 명사성 용례를 찾지 못했다는 뜻이며, "
            "공식 용어 카탈로그와 사전 정의 원본에서는 삭제하지 않았다."
        ),
    }
    write_json(manifest_path, manifest)

    print(
        f"완료: 원본 {len(candidates):,}개, 유효 {len(kept):,}개, "
        f"완전 오탐 {len(excluded):,}개, 일부 오탐 {len(partial):,}개"
    )
    print(f"완전 오탐 CSV: {project_relative_path(excluded_csv)}")
    print(f"완전 오탐 JSONL: {project_relative_path(excluded_jsonl)}")


if __name__ == "__main__":
    main()
