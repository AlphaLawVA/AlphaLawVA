# build_search_eligibility_manifest.py
"""
Description: 수동 검토로 확정한 주택 임대차 판례에서 분양 중심 판례를 제외하고
별도 검토한 주거용 매매 판례를 합쳐 판례 검색 API용 manifest를 생성한다.
Author: choeminju
Date: 2026-11-06
Before:
    - final_cases와 수동 검토한 주택 임대차·주거용 매매 판례 목록이 준비됨.
After:
    - 주거용 임대차·매매 범위의 최종 검색 허용 ID가 JSON으로 기록됨.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ml.data_collection.statutes.law_api_common import PROJECT_ROOT


SCHEMA_VERSION = "precedent_search_eligibility.v1"
POLICY_VERSION = "residential_real_estate_manual_review_v3"
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
DEFAULT_REVIEWED_ELIGIBLE_IDS_PATH = (
    PROJECT_ROOT
    / "local_data"
    / "precedents"
    / "reviews"
    / "eligibility_review_v01"
    / "eligible_precedent_ids.json"
)
DEFAULT_DISTRIBUTION_EXCLUSIONS_PATH = (
    PROJECT_ROOT
    / "data"
    / "precedents"
    / "metadata"
    / "precedent_search_distribution_exclusions_v01.json"
)
DEFAULT_RESIDENTIAL_SALE_ADDITIONS_PATH = (
    PROJECT_ROOT
    / "data"
    / "precedents"
    / "metadata"
    / "precedent_search_residential_sale_additions_v01.json"
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
DISTRIBUTION_MARKER = "분양"


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
    """초기 정밀도 우선 규칙으로 비교용 허용 목록을 생성한다."""
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


def load_reviewed_eligible_ids(path: Path) -> list[str]:
    """수동 검토로 확정한 판례 ID 목록을 검증해 읽는다."""
    if not path.is_file():
        raise FileNotFoundError(f"수동 검토 허용 목록이 없습니다: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("수동 검토 허용 목록은 비어 있지 않은 배열이어야 합니다.")
    ids = [str(value).strip() for value in payload]
    if any(not value.isdigit() for value in ids):
        raise ValueError("수동 검토 판례 ID는 숫자 문자열이어야 합니다.")
    if len(ids) != len(set(ids)):
        raise ValueError("수동 검토 판례 ID가 중복되었습니다.")
    return sorted(ids, key=int)


def load_residential_sale_additions(path: Path) -> list[dict[str, Any]]:
    """수동 검토로 확정한 주거용 매매 추가 판례를 검증해 읽는다."""
    if not path.is_file():
        raise FileNotFoundError(f"주거용 매매 추가 목록이 없습니다: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != (
        "precedent_search_residential_sale_additions.v1"
    ):
        raise ValueError("주거용 매매 추가 목록 schema_version이 올바르지 않습니다.")
    records = payload.get("added_precedents")
    if not isinstance(records, list) or not records:
        raise ValueError("주거용 매매 추가 목록은 비어 있지 않은 배열이어야 합니다.")
    ids = [str(record.get("precedent_id") or "").strip() for record in records]
    if any(not precedent_id.isdigit() for precedent_id in ids):
        raise ValueError("주거용 매매 추가 판례 ID는 숫자 문자열이어야 합니다.")
    if len(ids) != len(set(ids)):
        raise ValueError("주거용 매매 추가 판례 ID가 중복되었습니다.")
    if payload.get("added_case_count") != len(records):
        raise ValueError("주거용 매매 추가 판례 건수가 실제 목록과 다릅니다.")
    return records


def is_distribution_case(case: dict[str, Any]) -> bool:
    """사용자 노출 요약에 분양 맥락이 명시된 판례인지 판단한다."""
    return DISTRIBUTION_MARKER in _joined(case, SUMMARY_FIELDS)


def build_reviewed_manifest(
    cases_dir: Path,
    reviewed_eligible_ids_path: Path,
    residential_sale_additions_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """임대차 목록에서 분양 사건을 제외하고 주거용 매매 판례를 합친다."""
    if not cases_dir.is_dir():
        raise FileNotFoundError(f"final_cases 디렉터리가 없습니다: {cases_dir}")
    reviewed_ids = load_reviewed_eligible_ids(reviewed_eligible_ids_path)
    eligible_ids = []
    excluded_records = []
    for precedent_id in reviewed_ids:
        path = cases_dir / f"{precedent_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"수동 검토 판례 원본이 없습니다: {path}")
        case = json.loads(path.read_text(encoding="utf-8"))
        if is_distribution_case(case):
            excluded_records.append(
                {
                    "precedent_id": precedent_id,
                    "case_number": case.get("사건번호"),
                    "case_name": case.get("사건명"),
                    "reason": (
                        "생성요약 또는 쉬운요약에서 분양·분양전환·"
                        "분양권이 주요 사건 맥락으로 확인됨"
                    ),
                }
            )
        else:
            eligible_ids.append(precedent_id)

    sale_records = (
        load_residential_sale_additions(residential_sale_additions_path)
        if residential_sale_additions_path is not None
        else []
    )
    sale_ids = [str(record["precedent_id"]) for record in sale_records]
    overlap = sorted(set(eligible_ids) & set(sale_ids), key=int)
    if overlap:
        raise ValueError(f"임대차와 주거용 매매 허용 목록이 중복되었습니다: {overlap}")
    for record in sale_records:
        precedent_id = str(record["precedent_id"])
        path = cases_dir / f"{precedent_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"주거용 매매 판례 원본이 없습니다: {path}")
        case = json.loads(path.read_text(encoding="utf-8"))
        if str(case.get("사건번호") or "") != str(record.get("case_number") or ""):
            raise ValueError(f"주거용 매매 판례 사건번호가 다릅니다: {precedent_id}")
        eligible_ids.append(precedent_id)

    eligible_ids = sorted(eligible_ids, key=int)

    source_count = len(list(cases_dir.glob("*.json")))
    source_sha256 = hashlib.sha256(reviewed_eligible_ids_path.read_bytes()).hexdigest()
    sale_source_sha256 = (
        hashlib.sha256(residential_sale_additions_path.read_bytes()).hexdigest()
        if residential_sale_additions_path is not None
        else None
    )
    final_lease_count = len(reviewed_ids) - len(excluded_records)
    manual_scope_excluded_count = source_count - len(reviewed_ids) - len(sale_ids)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "policy_summary": (
            "수동 검토한 주택 임대차 판례에서 분양 중심 판례를 제외하고, "
            "별도 수동 검토한 주거용 매매 판례를 추가한다."
        ),
        "review_status": "MANUAL_LEASE_BASE_PLUS_RESIDENTIAL_SALE",
        "source_case_count": source_count,
        "base_reviewed_eligible_case_count": len(reviewed_ids),
        "distribution_excluded_case_count": len(excluded_records),
        "residential_lease_eligible_case_count": final_lease_count,
        "residential_sale_added_case_count": len(sale_ids),
        "eligible_case_count": len(eligible_ids),
        "excluded_case_count": source_count - len(eligible_ids),
        "reason_counts": {
            "MANUAL_SCOPE_EXCLUDED": manual_scope_excluded_count,
            "DISTRIBUTION_SUMMARY_EXCLUDED": len(excluded_records),
            "ELIGIBLE_RESIDENTIAL_LEASE": final_lease_count,
            "ELIGIBLE_RESIDENTIAL_SALE_ADDITION": len(sale_ids),
        },
        "source_reviewed_ids_sha256": source_sha256,
        "distribution_exclusion_manifest": str(
            DEFAULT_DISTRIBUTION_EXCLUSIONS_PATH.relative_to(PROJECT_ROOT)
        ),
        "residential_sale_additions_manifest": str(
            DEFAULT_RESIDENTIAL_SALE_ADDITIONS_PATH.relative_to(PROJECT_ROOT)
        ),
        "residential_sale_additions_sha256": sale_source_sha256,
        "eligible_precedent_ids": eligible_ids,
    }
    exclusions = {
        "schema_version": "precedent_search_distribution_exclusions.v1",
        "policy_version": POLICY_VERSION,
        "policy_summary": (
            "생성요약 또는 쉬운요약에 분양 맥락이 명시된 판례를 "
            "판례 검색 허용 목록에서 제외한다. 이유·전문에 단어가 "
            "단순 등장하는 경우는 제외하지 않는다."
        ),
        "base_reviewed_eligible_case_count": len(reviewed_ids),
        "excluded_case_count": len(excluded_records),
        "remaining_eligible_case_count": final_lease_count,
        "excluded_precedents": excluded_records,
    }
    return manifest, exclusions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-dir", type=Path, default=DEFAULT_CASES_DIR)
    parser.add_argument(
        "--reviewed-eligible-ids",
        type=Path,
        default=DEFAULT_REVIEWED_ELIGIBLE_IDS_PATH,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--distribution-exclusions-output",
        type=Path,
        default=DEFAULT_DISTRIBUTION_EXCLUSIONS_PATH,
    )
    parser.add_argument(
        "--residential-sale-additions",
        type=Path,
        default=DEFAULT_RESIDENTIAL_SALE_ADDITIONS_PATH,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest, exclusions = build_reviewed_manifest(
        args.cases_dir.expanduser().resolve(),
        args.reviewed_eligible_ids.expanduser().resolve(),
        args.residential_sale_additions.expanduser().resolve(),
    )
    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    exclusions_path = args.distribution_exclusions_output.expanduser().resolve()
    exclusions_path.parent.mkdir(parents=True, exist_ok=True)
    exclusions_path.write_text(
        json.dumps(exclusions, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"generated={output_path} "
        f"eligible={manifest['eligible_case_count']} "
        f"excluded={manifest['excluded_case_count']} "
        f"distribution_excluded={manifest['distribution_excluded_case_count']} "
        f"residential_sale_added={manifest['residential_sale_added_case_count']}"
    )


if __name__ == "__main__":
    main()
