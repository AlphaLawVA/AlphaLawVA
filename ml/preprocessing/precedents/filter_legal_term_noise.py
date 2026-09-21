# filter_legal_term_noise.py
"""
Description: 판례에서 추출한 법률용어 후보 중 문장 조각과 형식 잔여물처럼
명확한 노이즈만 보수적으로 제외하고 정제본과 제외 근거를 저장한다.
Author: choeminju
Date: 2026-09-21
Before:
    - 공식 법령용어와 판례 필드를 대조한 후보 및 판례별 매칭 결과가 존재.

After:
    - 원본을 보존한 채 정제 후보, 정제 판례별 매칭, 제외 목록과 규칙별
      통계를 local_data/precedents/legal_terms/matched_candidates_noise_filtered/
      아래에 생성.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

if __package__:
    from ml.preprocessing.precedents.extract_legal_term_candidates import (
        PROJECT_ROOT,
        project_relative_path,
        read_jsonl,
        write_json,
        write_jsonl,
        write_review_csv,
    )
else:
    from extract_legal_term_candidates import (  # type: ignore[no-redef]
        PROJECT_ROOT,
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
    / "matched_candidates_filtered"
)
DEFAULT_OUTPUT_DIR = (
    LOCAL_DATA_ROOT
    / "precedents"
    / "legal_terms"
    / "matched_candidates_noise_filtered"
)
SCHEMA_VERSION = "precedent_legal_term_noise_filter.v0.1"

WHITESPACE_RE = re.compile(r"\s+")
NUMERIC_DURATION_RE = re.compile(r"^\d+(?:년|개월|일|시간|회|차)$")
DEFINITION_BOILERPLATE_RE = re.compile(
    r"^(?:\(?이하|(?:라|이라|이라고)한다|(?:라|이라)함은|본다)[.)]*$"
)

# 공식 용어 목록에 포함됐지만 판례 문장에서 조사·어미까지 함께 잘린 조각이다.
# 일반적인 어미 정규식은 유효한 법률용어까지 지울 수 있어 확인된 표현만 둔다.
GRAMMATICAL_FRAGMENT_KEYS = frozenset(
    {
        "가진",
        "공공의",
        "공동으로",
        "공사의",
        "관리과",
        "묵시의",
        "무상의",
        "무조건의",
        "미등기의",
        "보험과",
        "부당한",
        "부정한",
        "부족한",
        "불리한",
        "불법의",
        "불특정의",
        "사건과",
        "상당한",
        "선의의",
        "순서대로",
        "시설과",
        "은행과",
        "유효한",
        "인정할수있는",
        "재심의",
        "제도의",
        "조약과",
        "주거의",
        "지원과",
        "직권으로",
        "진정한",
        "차임의",
        "타당한",
        "허위의",
        "완료된이후",
        "집행과",
    }
)

# 사람·물건 뒤의 '등'이 포함된 열거 표현은 독립된 용어가 아니다.
# '평등'처럼 원래 단어가 '등'으로 끝나는 경우를 피하려고 확인된 표현만 둔다.
ENUMERATION_FRAGMENT_KEYS = frozenset(
    {
        "경제적이익등",
        "공무원등",
        "구상금등",
        "금품등",
        "담당등",
        "법원사무관등",
        "사업자등",
        "상담등",
        "서면등",
        "설비등",
        "소송등",
        "소송사건등",
        "소유자등",
        "신청인등",
        "영화상영관등",
        "처분등",
        "토지등",
    }
)

UNIT_FRAGMENT_KEYS = frozenset({"편,건"})

RULE_DESCRIPTIONS = {
    "unbalanced_delimiter": "괄호가 한쪽만 남은 형식 잔여물",
    "definition_boilerplate": "'(이하', '라 한다)' 등 법령 정의문 문장 조각",
    "numeric_duration": "'1년', '2년'처럼 독립 개념이 아닌 숫자·기간 값",
    "grammatical_fragment": "조사·어미가 붙은 형용·부사·문장 조각",
    "enumeration_fragment": "'사업자 등'처럼 열거를 위한 표현 조각",
    "unit_fragment": "'편, 건'처럼 문서 단위가 합쳐진 표현",
    "duplicate_match_key": "동일한 매칭 키가 중복된 후순위 행",
}


def parse_args() -> argparse.Namespace:
    """노이즈 필터 입력과 출력 경로를 정의한다."""
    parser = argparse.ArgumentParser(
        description="판례 법률용어 후보에서 명확한 노이즈만 제외합니다."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="matched_legal_terms.jsonl과 case_term_matches.jsonl이 있는 폴더.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="정제 결과와 제외 근거를 저장할 폴더.",
    )
    return parser.parse_args()


def compact_term(value: Any) -> str:
    """규칙 비교용으로 공백을 제거한 문자열을 반환한다."""
    return WHITESPACE_RE.sub("", str(value or "").strip())


def has_unbalanced_delimiter(term: str) -> bool:
    """괄호나 대괄호가 한쪽만 남았는지 확인한다."""
    return term.count("(") != term.count(")") or term.count("[") != term.count("]")


def classify_noise(term: str) -> list[str]:
    """고신뢰 규칙에 해당하는 노이즈 사유를 반환한다."""
    compact = compact_term(term)
    reasons = []

    if has_unbalanced_delimiter(term):
        reasons.append("unbalanced_delimiter")
    if DEFINITION_BOILERPLATE_RE.fullmatch(compact):
        reasons.append("definition_boilerplate")
    if NUMERIC_DURATION_RE.fullmatch(compact):
        reasons.append("numeric_duration")
    if compact in GRAMMATICAL_FRAGMENT_KEYS:
        reasons.append("grammatical_fragment")
    if compact in ENUMERATION_FRAGMENT_KEYS:
        reasons.append("enumeration_fragment")
    if compact in UNIT_FRAGMENT_KEYS:
        reasons.append("unit_fragment")
    return reasons


def filter_candidate_rows(
    rows: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """후보를 유지 목록과 제외 근거 목록으로 나눈다."""
    kept = []
    excluded = []
    seen_match_keys: set[str] = set()

    for row in rows:
        match_key = str(row.get("match_key") or compact_term(row.get("term")))
        reasons = classify_noise(str(row.get("term") or match_key))
        if match_key in seen_match_keys:
            reasons.append("duplicate_match_key")
        else:
            seen_match_keys.add(match_key)

        if reasons:
            excluded.append({**row, "noise_reasons": reasons})
        else:
            kept.append(row)
    return kept, excluded


def filter_case_match_rows(
    rows: Iterable[dict[str, Any]],
    excluded_match_keys: set[str],
) -> list[dict[str, Any]]:
    """후보에서 제외된 용어를 판례별 매칭 결과에서도 제거한다."""
    filtered_rows = []
    for row in rows:
        matched_fields = {}
        for field, matches in row.get("matched_fields", {}).items():
            kept_matches = [
                match
                for match in matches
                if str(match.get("match_key") or "") not in excluded_match_keys
            ]
            if kept_matches:
                matched_fields[field] = kept_matches
        if matched_fields:
            filtered_rows.append({**row, "matched_fields": matched_fields})
    return filtered_rows


def file_sha256(path: Path) -> str:
    """파일의 SHA-256 해시를 반환한다."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now_utc_iso() -> str:
    """현재 UTC 시각을 ISO 문자열로 반환한다."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main() -> None:
    """후보와 판례별 매칭 결과를 정제하고 실행 기록을 저장한다."""
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    source_candidates = input_dir / "matched_legal_terms.jsonl"
    source_case_matches = input_dir / "case_term_matches.jsonl"

    output_candidates = output_dir / "matched_legal_terms.jsonl"
    output_case_matches = output_dir / "case_term_matches.jsonl"
    output_review_csv = output_dir / "matched_legal_terms_review.csv"
    excluded_path = output_dir / "excluded_noise_terms.jsonl"
    manifest_path = output_dir / "manifest.json"

    candidate_rows = list(read_jsonl(source_candidates))
    case_match_rows = list(read_jsonl(source_case_matches))
    kept_rows, excluded_rows = filter_candidate_rows(candidate_rows)
    excluded_match_keys = {
        str(row.get("match_key") or "") for row in excluded_rows
    }
    filtered_case_rows = filter_case_match_rows(case_match_rows, excluded_match_keys)

    write_jsonl(output_candidates, kept_rows)
    write_jsonl(output_case_matches, filtered_case_rows)
    write_review_csv(output_review_csv, kept_rows)
    write_jsonl(excluded_path, excluded_rows)

    reason_counts = Counter(
        reason for row in excluded_rows for reason in row["noise_reasons"]
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": now_utc_iso(),
        "status": "completed",
        "policy": "원본을 보존하고 고신뢰 형태 규칙에 해당하는 노이즈만 제외",
        "source_candidate_path": project_relative_path(source_candidates),
        "source_case_matches_path": project_relative_path(source_case_matches),
        "source_candidate_count": len(candidate_rows),
        "kept_candidate_count": len(kept_rows),
        "excluded_candidate_count": len(excluded_rows),
        "source_case_count": len(case_match_rows),
        "kept_case_count": len(filtered_case_rows),
        "rule_descriptions": RULE_DESCRIPTIONS,
        "rule_counts": dict(sorted(reason_counts.items())),
        "excluded_terms": [row["term"] for row in excluded_rows],
        "outputs": {
            "matched_legal_terms": project_relative_path(output_candidates),
            "case_term_matches": project_relative_path(output_case_matches),
            "review_csv": project_relative_path(output_review_csv),
            "excluded_noise_terms": project_relative_path(excluded_path),
        },
        "output_sha256": {
            path.name: file_sha256(path)
            for path in (
                output_candidates,
                output_case_matches,
                output_review_csv,
                excluded_path,
            )
        },
        "notice": (
            "법률적 중요도나 조회 성공 여부로 삭제하지 않았으며, "
            "의미가 불확실한 복합 법률용어는 유지했다."
        ),
    }
    write_json(manifest_path, manifest)

    print(
        f"완료: 원본 {len(candidate_rows):,}개, 유지 {len(kept_rows):,}개, "
        f"노이즈 제외 {len(excluded_rows):,}개"
    )
    print(f"정제 후보: {project_relative_path(output_candidates)}")
    print(f"제외 근거: {project_relative_path(excluded_path)}")
    for reason, count in sorted(reason_counts.items()):
        print(f"- {reason}: {count:,}개")


if __name__ == "__main__":
    main()
