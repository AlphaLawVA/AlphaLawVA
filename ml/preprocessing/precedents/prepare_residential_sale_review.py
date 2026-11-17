# prepare_residential_sale_review.py
"""
Description: 기존 판례 검색 제외 목록에서 주거용 매매 분쟁 후보를 추출하고
수동 검토에 필요한 근거 필드와 신호를 JSONL·CSV로 정리한다.
Author: choeminju
Date: 2026-11-06
Before:
    - 임대차 기준으로 수동 검토된 판례 판정 JSONL과 final_cases가 준비됨.
After:
    - 주거용 매매 후보와 검토용 신호가 local_data의 별도 review 폴더에 생성됨.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ml.data_collection.statutes.law_api_common import PROJECT_ROOT


DEFAULT_SOURCE_REVIEW = (
    PROJECT_ROOT
    / "local_data"
    / "precedents"
    / "reviews"
    / "eligibility_review_v01"
    / "precedent_eligibility_review_final.jsonl"
)
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT
    / "local_data"
    / "precedents"
    / "reviews"
    / "residential_sale_review_v01"
)
DEFAULT_APPROVED_MANIFEST = (
    PROJECT_ROOT
    / "data"
    / "precedents"
    / "metadata"
    / "precedent_search_residential_sale_additions_v01.json"
)

SEARCH_FIELDS = (
    "case_name",
    "issue",
    "holding",
    "generated_summary",
    "easy_summary",
)
RESIDENTIAL_MARKERS = (
    "주택",
    "아파트",
    "빌라",
    "연립주택",
    "연립",
    "다세대주택",
    "다세대",
    "다가구주택",
    "다가구",
    "단독주택",
    "공동주택",
    "주거용",
    "오피스텔",
    "가옥",
)
SALE_MARKERS = (
    "매매",
    "매수",
    "매도",
    "매매계약",
    "매매대금",
    "계약금",
    "중도금",
    "잔금",
    "소유권이전등기",
)
DIRECT_DISPUTE_MARKERS = (
    "계약 해제",
    "계약해제",
    "해약",
    "계약금 반환",
    "매매대금 반환",
    "손해배상",
    "하자",
    "누수",
    "곰팡이",
    "수리",
    "고지",
    "설명 의무",
    "설명의무",
    "중개",
    "근저당",
    "가압류",
    "인도",
    "명도",
    "소유권이전등기",
)
EXCLUSION_SIGNAL_GROUPS = {
    "distribution": ("분양", "수분양", "분양권", "입주권"),
    "redevelopment": ("재개발", "재건축", "주택조합", "정비사업"),
    "commercial": (
        "상가",
        "점포",
        "공장",
        "창고",
        "호텔",
        "모텔",
        "숙박시설",
        "음식점",
        "영업장",
        "사업장",
    ),
    "land": ("농지", "임야", "전답", "대지만", "토지만", "토지 매매"),
    "auction": ("경매", "낙찰", "경락"),
    "creditor": ("사해행위", "채권자취소", "채무초과"),
    "family_or_inheritance": ("상속", "이혼", "부부", "재산분할"),
    "security_or_trust": ("양도담보", "가등기담보", "명의신탁", "신탁계약"),
}


def joined(record: dict[str, Any]) -> str:
    return " ".join(str(record.get(field) or "") for field in SEARCH_FIELDS)


def matching_markers(text: str, markers: tuple[str, ...]) -> list[str]:
    return [marker for marker in markers if marker in text]


def build_candidate(record: dict[str, Any]) -> dict[str, Any] | None:
    if record.get("final_decision") != "EXCLUDE":
        return None
    text = joined(record)
    residential_signals = matching_markers(text, RESIDENTIAL_MARKERS)
    sale_signals = matching_markers(text, SALE_MARKERS)
    if not residential_signals or not sale_signals:
        return None

    exclusion_signals = {
        group: matching_markers(text, markers)
        for group, markers in EXCLUSION_SIGNAL_GROUPS.items()
    }
    exclusion_signals = {
        group: signals for group, signals in exclusion_signals.items() if signals
    }
    return {
        "precedent_id": str(record.get("precedent_id") or ""),
        "case_number": record.get("case_number"),
        "case_name": record.get("case_name"),
        "court_name": record.get("court_name"),
        "decision_date": record.get("decision_date"),
        "previous_category": record.get("final_category"),
        "previous_reason": record.get("decision_reason"),
        "residential_signals": residential_signals,
        "sale_signals": sale_signals,
        "direct_dispute_signals": matching_markers(text, DIRECT_DISPUTE_MARKERS),
        "exclusion_signals": exclusion_signals,
        "issue": record.get("issue"),
        "holding": record.get("holding"),
        "generated_summary": record.get("generated_summary"),
        "easy_summary": record.get("easy_summary"),
        "review_decision": "REVIEW",
        "review_category": "",
        "review_reason": "",
    }


def load_candidates(path: Path) -> list[dict[str, Any]]:
    candidates = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        record = json.loads(line)
        candidate = build_candidate(record)
        if candidate:
            candidates.append(candidate)
        if not isinstance(record, dict):
            raise ValueError(f"{line_number}번째 행이 JSON 객체가 아닙니다.")
    return sorted(candidates, key=lambda item: int(item["precedent_id"]))


def write_jsonl(path: Path, candidates: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(candidate, ensure_ascii=False) + "\n"
            for candidate in candidates
        ),
        encoding="utf-8",
    )


def write_csv(path: Path, candidates: list[dict[str, Any]]) -> None:
    fields = (
        "precedent_id",
        "case_number",
        "case_name",
        "court_name",
        "decision_date",
        "previous_category",
        "previous_reason",
        "residential_signals",
        "sale_signals",
        "direct_dispute_signals",
        "exclusion_signals",
        "generated_summary",
        "easy_summary",
        "review_decision",
        "review_category",
        "review_reason",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        writer.writeheader()
        for candidate in candidates:
            row = dict(candidate)
            for field in (
                "residential_signals",
                "sale_signals",
                "direct_dispute_signals",
                "exclusion_signals",
            ):
                row[field] = json.dumps(row[field], ensure_ascii=False)
            writer.writerow({field: row.get(field) for field in fields})


def write_summary(
    path: Path,
    candidates: list[dict[str, Any]],
    source_path: Path,
) -> None:
    previous_categories = Counter(
        str(candidate.get("previous_category") or "") for candidate in candidates
    )
    exclusion_groups = Counter(
        group
        for candidate in candidates
        for group in candidate["exclusion_signals"]
    )
    payload = {
        "schema_version": "residential_sale_review.candidates.v1",
        "source_review": str(source_path),
        "candidate_count": len(candidates),
        "selection_rule": (
            "기존 EXCLUDE 판례 중 제목·판시사항·판결요지·생성요약·쉬운요약에 "
            "주거 대상 표현과 매매 표현이 각각 하나 이상 포함된 판례"
        ),
        "final_decision_method": "CODEX_MANUAL_REVIEW_REQUIRED",
        "external_api_calls": 0,
        "previous_category_counts": dict(sorted(previous_categories.items())),
        "exclusion_signal_group_counts": dict(sorted(exclusion_groups.items())),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_approved_records(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != (
        "precedent_search_residential_sale_additions.v1"
    ):
        raise ValueError("주거용 매매 승인 manifest 스키마가 올바르지 않습니다.")
    records = payload.get("added_precedents")
    if not isinstance(records, list):
        raise ValueError("주거용 매매 승인 목록이 배열이 아닙니다.")
    approved = {str(record.get("precedent_id") or ""): record for record in records}
    if "" in approved or len(approved) != len(records):
        raise ValueError("주거용 매매 승인 판례 ID가 없거나 중복되었습니다.")
    if payload.get("added_case_count") != len(approved):
        raise ValueError("주거용 매매 승인 건수가 실제 목록과 다릅니다.")
    return approved


def exclusion_category(candidate: dict[str, Any]) -> tuple[str, str]:
    signals = candidate["exclusion_signals"]
    priorities = (
        ("distribution", "EXCLUDE_DISTRIBUTION", "분양·분양권·입주권 중심 사건"),
        ("redevelopment", "EXCLUDE_HOUSING_PROJECT", "재개발·재건축·주택조합 중심 사건"),
        ("commercial", "EXCLUDE_COMMERCIAL", "상가·영업용 부동산 사건"),
        ("land", "EXCLUDE_LAND", "토지·농지·임야 중심 사건"),
        ("auction", "EXCLUDE_AUCTION", "경매·낙찰 중심 사건"),
        ("creditor", "EXCLUDE_CREDITOR", "사해행위·채권자취소 중심 사건"),
        (
            "family_or_inheritance",
            "EXCLUDE_FAMILY_OR_INHERITANCE",
            "가족·상속 관계가 주된 사건",
        ),
        (
            "security_or_trust",
            "EXCLUDE_SECURITY_OR_TRUST",
            "담보·신탁 관계가 주된 사건",
        ),
    )
    for group, category, reason in priorities:
        if group in signals:
            return category, reason
    return (
        "EXCLUDE_NOT_DIRECT_RESIDENTIAL_SALE",
        "주거 대상 표현은 있으나 일반 소비자의 주택 매수·매도 분쟁과 직접 연결되지 않음",
    )


def build_final_review(
    candidates: list[dict[str, Any]],
    approved: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    candidate_ids = {candidate["precedent_id"] for candidate in candidates}
    missing = sorted(set(approved) - candidate_ids, key=int)
    if missing:
        raise ValueError(f"승인 판례가 후보 목록에 없습니다: {missing}")

    reviewed = []
    for candidate in candidates:
        record = dict(candidate)
        precedent_id = candidate["precedent_id"]
        if precedent_id in approved:
            approval = approved[precedent_id]
            record["review_decision"] = "KEEP"
            record["review_category"] = approval["category"]
            record["review_reason"] = approval["reason"]
        else:
            category, reason = exclusion_category(candidate)
            record["review_decision"] = "EXCLUDE"
            record["review_category"] = category
            record["review_reason"] = reason
        record["review_method"] = "CODEX_MANUAL_SUMMARY_REVIEW"
        reviewed.append(record)
    return reviewed


def write_final_summary(path: Path, reviewed: list[dict[str, Any]]) -> None:
    decisions = Counter(record["review_decision"] for record in reviewed)
    categories = Counter(record["review_category"] for record in reviewed)
    payload = {
        "schema_version": "residential_sale_review.final.v1",
        "candidate_count": len(reviewed),
        "decision_counts": dict(sorted(decisions.items())),
        "category_counts": dict(sorted(categories.items())),
        "review_method": "CODEX_MANUAL_SUMMARY_REVIEW",
        "external_api_calls": 0,
        "validation": {
            "all_candidates_decided": all(
                record["review_decision"] in {"KEEP", "EXCLUDE"}
                for record in reviewed
            ),
            "precedent_ids_unique": len(reviewed)
            == len({record["precedent_id"] for record in reviewed}),
        },
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-review", type=Path, default=DEFAULT_SOURCE_REVIEW)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--approved-manifest",
        type=Path,
        default=DEFAULT_APPROVED_MANIFEST,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_path = args.source_review.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    approved_manifest = args.approved_manifest.expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"기존 판례 검토 파일이 없습니다: {source_path}")
    output_dir.mkdir(parents=True, exist_ok=True)

    candidates = load_candidates(source_path)
    write_jsonl(output_dir / "residential_sale_candidates.jsonl", candidates)
    write_csv(output_dir / "residential_sale_candidates.csv", candidates)
    write_summary(output_dir / "candidate_summary.json", candidates, source_path)
    approved = load_approved_records(approved_manifest)
    reviewed = build_final_review(candidates, approved)
    write_jsonl(output_dir / "residential_sale_review_final.jsonl", reviewed)
    write_final_summary(output_dir / "review_summary.json", reviewed)
    print(
        f"generated={output_dir} candidates={len(candidates)} "
        f"approved={len(approved)} external_api_calls=0"
    )


if __name__ == "__main__":
    main()
