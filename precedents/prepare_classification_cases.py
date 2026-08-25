#!/usr/bin/env python3
"""판례 LLM 분류 전에 사용할 분류용 JSON을 생성한다.

이 파일은 `local_data/precedents/processed/cases/*.json` 전처리본을 읽어서
`local_data/precedents/processed/classification_cases/*.json` 분류용 파일을 만든다.
원본 raw JSON과 기존 전처리 JSON은 수정하지 않는다.

Before:
    local_data/precedents/processed/cases/{판례일련번호}.json
    - 주문, 청구취지, 이유 등 청킹 전 전처리 필드까지 포함한 판례 전처리본이다.

After:
    local_data/precedents/processed/classification_cases/{판례일련번호}.json
    - LLM 분류에 사용할 사건명, 판시사항, 판결요지 중심의 데이터다.
    - 키워드 진단 결과는 후검수용 필드로 저장하되, LLM 프롬프트에는 넣지 않는다.
    local_data/precedents/processed/keyword_diagnosis_report.json
    - 분류용 파일 생성 개수와 키워드 라벨 통계를 기록한다.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from precedent_config import (
    CLASSIFICATION_CASES_DIR,
    KEYWORD_DIAGNOSIS_REPORT_PATH,
    PROCESSED_CASES_DIR,
    ensure_collection_dirs,
    now_utc_iso,
)


NEGATIVE_KEYWORD_GROUPS = {
    "마약_향정": [
        "마약",
        "향정신성",
        "필로폰",
        "대마",
        "투약",
        "마약류관리에관한법률",
        "메트암페타민",
        "엑스터시",
        "코카인",
    ],
    "강력범죄": [
        "살인",
        "강도",
        "강간",
        "준강간",
        "추행",
        "상해",
        "폭행",
        "협박",
        "체포",
        "감금",
        "유기",
        "치사",
        "치상",
    ],
    "교통_음주": [
        "음주운전",
        "도로교통법",
        "무면허운전",
        "교통사고처리특례법",
        "위험운전치상",
        "뺑소니",
        "중앙선침범",
        "혈중알코올농도",
    ],
    "국가보안_반란_군사": [
        "반란",
        "내란",
        "국가보안법",
        "간첩",
        "이적단체",
        "군형법",
        "계엄",
        "군부계엄",
        "항명",
        "군사기밀",
        "보안관찰",
    ],
    "조세_세금": [
        "법인세",
        "부가가치세",
        "소득세",
        "상속세",
        "증여세",
        "관세",
        "과세처분",
        "부과처분",
        "조세포탈",
        "익금",
        "손금",
        "세무서장",
    ],
    "지식재산": [
        "특허",
        "상표",
        "디자인권",
        "저작권",
        "실용신안",
        "침해금지",
        "무효심판",
        "권리범위확인",
        "특허심판원",
        "라이선스",
    ],
    "노동_산재": [
        "해고",
        "부당해고",
        "임금",
        "퇴직금",
        "근로자",
        "근로계약",
        "산업재해",
        "요양급여",
        "노동조합",
        "단체협약",
        "쟁의행위",
    ],
    "회사_주식_증권": [
        "주식",
        "주주",
        "이사",
        "감사",
        "주주총회",
        "신주발행",
        "증권",
        "상장",
        "시세조종",
        "내부자거래",
        "합병",
        "분할",
    ],
    "가사_친족": [
        "이혼",
        "위자료",
        "재산분할",
        "양육권",
        "양육비",
        "친권",
        "인지",
        "상속재산분할",
        "유류분",
        "친생자",
    ],
    "의료_보건": [
        "의료사고",
        "의사",
        "병원",
        "진료",
        "수술",
        "의료법",
        "요양기관",
        "건강보험",
        "보험급여",
        "진단서",
    ],
    "선거_정치": [
        "공직선거법",
        "선거운동",
        "후보자",
        "정당",
        "정치자금",
        "허위사실공표",
        "기부행위",
        "당선무효",
    ],
    "환경_식품_행정규제": [
        "폐기물",
        "대기환경",
        "수질",
        "식품위생법",
        "영업정지",
        "허가취소",
        "과징금",
        "행정처분",
    ],
    "해상_항공": [
        "선박",
        "해상",
        "해운",
        "선원",
        "항공기",
        "운송물",
        "선하증권",
        "공동해손",
    ],
    "공공수용_토지행정": [
        "토지수용",
        "수용재결",
        "보상금",
        "공익사업",
        "도시계획시설",
        "개발제한구역",
        "농지전용",
        "산지전용",
    ],
}

RELATED_KEYWORD_GROUPS = {
    "lease": [
        "주택임대차보호법",
        "임대차보증금",
        "보증금반환",
        "보증금 반환",
        "전세보증금",
        "월세보증금",
        "차임",
        "연체차임",
        "임대인",
        "임차인",
        "임대차계약",
        "임대차 종료",
        "임대차기간",
        "임차목적물",
        "목적물 명도",
        "건물명도",
        "건물인도",
        "주택 인도",
        "대항력",
        "우선변제권",
        "최우선변제권",
        "확정일자",
        "소액임차인",
        "임차권등기",
        "임차권등기명령",
        "계약갱신요구권",
        "묵시의 갱신",
        "묵시적 갱신",
        "전세권",
        "전세권설정등기",
        "전세금반환",
        "전세금 반환",
    ],
    "sale": [
        "부동산매매",
        "부동산 매매",
        "매매계약",
        "매매대금",
        "매매대금반환",
        "계약금반환",
        "계약금 반환",
        "중도금",
        "잔금",
        "소유권이전등기",
        "소유권이전등기절차",
        "소유권이전등기말소",
        "매도인",
        "매수인",
        "담보책임",
        "하자담보책임",
        "매매계약 해제",
        "이중매매",
        "가등기",
        "소유권보존등기",
    ],
    "real_estate_common": [
        "등기부",
        "등기사항증명서",
        "근저당권",
        "저당권",
        "가압류",
        "압류",
        "경매",
        "배당요구",
        "배당절차",
        "신탁등기",
        "명의신탁",
        "중개대상물",
        "공인중개사",
        "중개업자",
        "중개수수료",
        "확인설명서",
        "중개대상물 확인·설명",
        "전입신고",
        "주민등록",
        "점유",
        "인도",
    ],
}


def parse_args() -> argparse.Namespace:
    """커맨드라인 옵션을 정의한다."""
    parser = argparse.ArgumentParser(description="Prepare keyword-diagnosed classification case JSON files.")
    parser.add_argument(
        "--core-field-policy",
        choices=["issue_or_summary", "issue_and_summary"],
        default="issue_or_summary",
        help="issue_or_summary는 판시사항/판결요지 중 하나라도 있으면 포함한다.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="테스트용으로 생성할 분류용 파일 수를 제한한다.",
    )
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="기존 classification_cases 폴더를 비우지 않고 덮어쓴다.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    """UTF-8 JSON 파일을 딕셔너리로 읽는다."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """딕셔너리를 보기 좋은 UTF-8 JSON 파일로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_text(value: Any) -> str:
    """None과 공백 문자열을 빈 문자열로 정리한다."""
    if value is None:
        return ""
    return str(value).strip()


def found_terms(text: str, terms: list[str]) -> list[str]:
    """텍스트에 포함된 키워드를 중복 없이 순서대로 찾는다."""
    found = []
    for term in terms:
        if term in text and term not in found:
            found.append(term)
    return found


def diagnose_keywords(text: str) -> dict[str, Any]:
    """관련/비관련 키워드 묶음을 진단하고 후검수용 라벨을 만든다."""
    related_groups = {
        group: terms
        for group, terms in (
            (group, found_terms(text, keywords))
            for group, keywords in RELATED_KEYWORD_GROUPS.items()
        )
        if terms
    }
    unrelated_groups = {
        group: terms
        for group, terms in (
            (group, found_terms(text, keywords))
            for group, keywords in NEGATIVE_KEYWORD_GROUPS.items()
        )
        if len(terms) >= 2
    }

    has_related = bool(related_groups)
    has_unrelated = bool(unrelated_groups)
    if has_related and has_unrelated:
        label = "mixed"
    elif has_related:
        label = "strong_related"
    elif has_unrelated:
        label = "strong_unrelated_signal"
    else:
        label = "neutral"

    return {
        "schema_version": "precedent_keyword_diagnosis.v1",
        "label": label,
        "related_groups": related_groups,
        "unrelated_signal_groups": unrelated_groups,
        "note": "LLM 입력에는 넣지 않고, LLM 분류 후 상충 검수용으로만 사용한다.",
    }


def has_required_core_fields(case: dict[str, Any], policy: str) -> bool:
    """판시사항/판결요지 보유 여부 기준으로 1차 분류 대상인지 판단한다."""
    has_issue = bool(normalize_text(case.get("판시사항")))
    has_summary = bool(normalize_text(case.get("판결요지")))
    if policy == "issue_and_summary":
        return has_issue and has_summary
    return has_issue or has_summary


def build_classification_case(case: dict[str, Any], source_path: Path) -> dict[str, Any]:
    """전처리 판례 하나에서 LLM 분류용 핵심 필드와 키워드 진단 필드를 만든다."""
    keyword_text = "\n".join(
        normalize_text(case.get(field))
        for field in ["사건명", "판시사항", "판결요지"]
        if normalize_text(case.get(field))
    )
    return {
        "schema_version": "precedent_classification_case.v1",
        "created_at": now_utc_iso(),
        "판례일련번호": normalize_text(case.get("판례일련번호") or source_path.stem),
        "사건번호": normalize_text(case.get("사건번호")),
        "사건명": normalize_text(case.get("사건명")),
        "법원명": normalize_text(case.get("법원명")),
        "선고일자": normalize_text(case.get("선고일자")),
        "사건종류명": normalize_text(case.get("사건종류명")),
        "판결유형": normalize_text(case.get("판결유형")),
        "판시사항": normalize_text(case.get("판시사항")),
        "판결요지": normalize_text(case.get("판결요지")),
        "원본파일경로": case.get("원본파일경로"),
        "전처리파일경로": str(source_path),
        "keyword_diagnosis": diagnose_keywords(keyword_text),
    }


def iter_processed_case_paths() -> list[Path]:
    """전처리 판례 JSON 경로를 판례일련번호 순서로 반환한다."""
    return sorted(
        PROCESSED_CASES_DIR.glob("*.json"),
        key=lambda path: int(path.stem) if path.stem.isdigit() else path.stem,
    )


def reset_output_dir() -> None:
    """이전 분류용 JSON을 비워 이번 기준으로 다시 생성되게 한다."""
    if CLASSIFICATION_CASES_DIR.exists():
        shutil.rmtree(CLASSIFICATION_CASES_DIR)
    CLASSIFICATION_CASES_DIR.mkdir(parents=True, exist_ok=True)


def main() -> int:
    """분류용 JSON 생성 작업을 실행한다."""
    args = parse_args()
    ensure_collection_dirs()
    if not args.keep_existing:
        reset_output_dir()

    stats = Counter()
    keyword_label_counts = Counter()
    skipped_reason_counts = Counter()

    for path in iter_processed_case_paths():
        stats["source_total"] += 1
        case = read_json(path)
        if not normalize_text(case.get("사건명")):
            skipped_reason_counts["missing_case_name"] += 1
            continue
        if not has_required_core_fields(case, args.core_field_policy):
            skipped_reason_counts["missing_required_core_fields"] += 1
            continue

        classification_case = build_classification_case(case, path)
        keyword_label = classification_case["keyword_diagnosis"]["label"]
        keyword_label_counts[keyword_label] += 1
        write_json(CLASSIFICATION_CASES_DIR / f"{path.stem}.json", classification_case)
        stats["created"] += 1
        if args.limit is not None and stats["created"] >= args.limit:
            break

    report = {
        "schema_version": "precedent_keyword_diagnosis_report.v1",
        "created_at": now_utc_iso(),
        "core_field_policy": args.core_field_policy,
        "paths": {
            "source_processed_cases": str(PROCESSED_CASES_DIR),
            "classification_cases": str(CLASSIFICATION_CASES_DIR),
        },
        "stats": dict(stats),
        "skipped_reason_counts": dict(skipped_reason_counts),
        "keyword_label_counts": dict(keyword_label_counts),
    }
    write_json(KEYWORD_DIAGNOSIS_REPORT_PATH, report)

    print(f"분류용 판례 생성 완료: {CLASSIFICATION_CASES_DIR}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
