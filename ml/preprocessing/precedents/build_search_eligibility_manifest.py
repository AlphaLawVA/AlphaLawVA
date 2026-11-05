# build_search_eligibility_manifest.py
"""
Description: final_cases를 보수적인 주택 임대차 기준으로 분류해
판례 검색 API가 사용할 버전형 허용 목록 manifest를 생성한다.
Author: choeminju
Date: 2026-11-06
Before:
    - final_cases에 주택·상가 임대차와 매매·토지 판례가 함께 저장되어 있음.
After:
    - 원본 삭제 없이 판례별 분류 사유와 검색 허용 ID가 JSON으로 기록됨.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ml.data_collection.statutes.law_api_common import PROJECT_ROOT


SCHEMA_VERSION = "precedent_search_eligibility.v1"
POLICY_VERSION = "residential_lease_precision_v1"
DEFAULT_CASES_DIR = (
    PROJECT_ROOT / "local_data" / "precedents" / "processed" / "final_cases"
)
DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT
    / "data"
    / "precedents"
    / "metadata"
    / "precedent_search_eligibility_v01.json"
)

LEGAL_FIELDS = ("판시사항", "판결요지", "참조조문")
SUMMARY_FIELDS = ("생성요약", "쉬운요약")
RESIDENTIAL_CONTEXT_MARKERS = (
    "세입자",
    "집주인",
    "임차주택",
    "주거용",
    "전셋집",
    "월세집",
    "임대주택",
    "주택 임대차",
    "주택임대차",
)
LEASE_ISSUE_MARKERS = (
    "임대차",
    "임차",
    "임대인",
    "전세",
    "월세",
    "보증금",
    "수선",
    "누수",
    "곰팡이",
    "계약갱신",
    "대항력",
    "우선변제",
    "전입신고",
    "확정일자",
)
COMMERCIAL_MARKERS = (
    "상가건물 임대차보호법",
    "상가건물임대차보호법",
    "상가",
    "점포",
    "권리금",
    "영업장",
    "사업자등록",
    "식당",
    "음식점",
    "카페",
    "매장",
    "사무실",
    "공장",
    "창고",
    "숙박",
    "호텔",
    "모텔",
    "병원",
    "의원",
    "약국",
    "학원",
    "교회",
    "뷔페",
    "영업용",
)
HANJA_PATTERN = re.compile(r"[一-龥]")


def _joined(case: dict[str, Any], fields: tuple[str, ...]) -> str:
    return " ".join(str(case.get(field) or "") for field in fields)


def classify_case(case: dict[str, Any]) -> tuple[bool, str]:
    """정밀도 우선 규칙으로 검색 허용 여부와 단일 사유를 반환한다."""
    legal_text = _joined(case, LEGAL_FIELDS)
    summary_text = _joined(case, SUMMARY_FIELDS)
    easy_summary = str(case.get("쉬운요약") or "").strip()
    combined_text = f"{legal_text} {summary_text}"

    if not easy_summary:
        return False, "MISSING_EASY_SUMMARY"
    if HANJA_PATTERN.search(easy_summary):
        return False, "EASY_SUMMARY_HANJA"
    if any(marker in combined_text for marker in COMMERCIAL_MARKERS):
        return False, "COMMERCIAL_OR_BUSINESS"
    if "주택임대차보호법" in legal_text:
        return True, "HOUSING_LEASE_ACT"
    if (
        any(marker in summary_text for marker in RESIDENTIAL_CONTEXT_MARKERS)
        and any(marker in summary_text for marker in LEASE_ISSUE_MARKERS)
    ):
        return True, "RESIDENTIAL_LEASE_CONTEXT"
    return False, "INSUFFICIENT_RESIDENTIAL_CONTEXT"


def build_manifest(cases_dir: Path) -> dict[str, Any]:
    if not cases_dir.is_dir():
        raise FileNotFoundError(f"final_cases 디렉터리가 없습니다: {cases_dir}")

    records = []
    counts: Counter[str] = Counter()
    for path in sorted(cases_dir.glob("*.json"), key=lambda item: item.name):
        case = json.loads(path.read_text(encoding="utf-8"))
        precedent_id = str(case.get("판례일련번호") or "").strip()
        if not precedent_id.isdigit() or precedent_id != path.stem:
            raise ValueError(f"판례 식별자가 파일명과 일치하지 않습니다: {path}")
        eligible, reason = classify_case(case)
        counts[reason] += 1
        records.append(
            {
                "precedent_id": precedent_id,
                "case_number": case.get("사건번호"),
                "eligible": eligible,
                "reason": reason,
            }
        )

    eligible_ids = sorted(
        (record["precedent_id"] for record in records if record["eligible"]),
        key=int,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "policy_summary": (
            "주택 임대차 맥락이 명확하고 쉬운요약 품질 검사를 통과한 "
            "판례만 판례 검색 API에서 허용한다."
        ),
        "review_status": "AUTO_CLASSIFIED_NEEDS_MANUAL_REVIEW",
        "source_case_count": len(records),
        "eligible_case_count": len(eligible_ids),
        "excluded_case_count": len(records) - len(eligible_ids),
        "reason_counts": dict(sorted(counts.items())),
        "eligible_precedent_ids": eligible_ids,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-dir", type=Path, default=DEFAULT_CASES_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_manifest(args.cases_dir.expanduser().resolve())
    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"generated={output_path} "
        f"eligible={manifest['eligible_case_count']} "
        f"excluded={manifest['excluded_case_count']}"
    )


if __name__ == "__main__":
    main()
