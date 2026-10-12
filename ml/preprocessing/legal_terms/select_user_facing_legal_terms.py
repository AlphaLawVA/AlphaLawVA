# select_user_facing_legal_terms.py
"""
Description: 법률 분야 정의가 연결된 용어를 일반 사용자의 이해 난이도 기준으로
후보선택 또는 제외하고, 원본을 보존한 검토용 JSONL·CSV를 생성한다.
Author: choeminju
Date: 2026-10-05
Before:
    - 법률 분야 정의 후보와 정의 검토 상태가 정리되어 있다.
    - 사용자가 쉬운 용어와 반드시 설명할 전문 용어의 기준 예시를 제공했다.
After:
    - 모든 용어에 사용자 노출 후보 여부와 규칙 기반 판단 근거가 추가된다.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = (
    PROJECT_ROOT
    / "local_data"
    / "legal_terms"
    / "reviews"
    / "legal_category_terms_v01"
    / "legal_category_term_review.jsonl"
)
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT
    / "local_data"
    / "legal_terms"
    / "reviews"
    / "user_facing_legal_terms_v01"
)

# 사용자가 직접 제시한 판단은 다른 휴리스틱보다 항상 우선한다.
USER_SELECTED_TERMS = {
    "가등기가처분",
    "가산금",
    "가산세",
    "가압류",
    "가액반환",
    "각하",
    "각하결정",
    "거소지정권",
    "결손처분",
    "결의권",
    "권리변동",
    "납입담보책임",
    "담보가등기",
    "담보계약",
    "담보권",
    "대항력",
    "중가산금",
    "채권계약",
}

USER_EXCLUDED_TERMS = {
    "가공",
    "가중",
    "공신력",
    "규정",
    "대리",
    "동의",
    "면제",
    "면허",
    "명령",
    "무죄",
    "미성년",
    "민법",
    "능력",
    "벌금",
    "법규",
    "법령",
    "법률",
    "법원",
    "법질서",
    "보증인",
    "상속",
    "상호",
    "수표",
    "순위",
    "신문",
    "신분",
    "심판관",
    "압류",
    "압류재산",
    "은닉",
    "이의",
    "인지",
    "재단",
    "재심",
    "전문",
    "지시",
    "위자료",
    "위증",
    "유산",
    "유예",
    "유죄",
    "출생시간",
    "착수",
    "패소",
    "혐의",
    "회계사",
}

# 법률에 익숙하지 않은 성인도 문장에서 대략적인 뜻을 바로 이해할 수 있는 표현이다.
# 조금이라도 전문적 의미가 남는 표현은 이 목록에 넣지 않고 후보로 보존한다.
OBVIOUS_COMMON_TERMS = {
    "가입",
    "가정법원",
    "감독",
    "검사",
    "결정",
    "경과",
    "계속",
    "계약",
    "계약관계",
    "계약금",
    "계약당사자",
    "계약상대방",
    "계약상 의무",
    "계약이행",
    "계약조건",
    "계약조항",
    "고등법원",
    "고발",
    "고소인",
    "공고",
    "공공기관",
    "공인중개사",
    "공인회계사",
    "공정",
    "과태료",
    "관련 사건",
    "관리인",
    "관리자",
    "관할법원",
    "교육 의무",
    "구금",
    "구속",
    "국세",
    "권리",
    "권리관계",
    "권리자",
    "권리주장",
    "권리취득",
    "권리행사",
    "규칙",
    "근로계약",
    "기소",
    "기한",
    "납세의무자",
    "납세자",
    "담보",
    "담보대출",
    "담보대출금",
    "담보물",
    "당사자",
    "대리인",
    "대통령령",
    "대표",
    "도로명주소",
    "독촉",
    "독촉장",
    "등록",
    "등록세",
    "등본",
    "매매계약",
    "면허세",
    "면허취소",
    "명의변경",
    "명의인",
    "무효",
    "민사",
    "민사사건",
    "민사소송",
    "반송",
    "반환 청구",
    "배상",
    "배상의무",
    "배상의무자",
    "배상책임",
    "벌금형",
    "범죄행위",
    "법무법인",
    "법무사",
    "법인",
    "변호사",
    "병역",
    "병역의무",
    "보고의무",
    "보상",
    "보상금",
    "보증",
    "보증금",
    "보증서",
    "부동산",
    "분실신고",
    "사건",
    "사망",
    "사본",
    "사실",
    "사용자",
    "상속인",
    "상속재산",
    "상속포기",
    "상품",
    "서명",
    "서명날인",
    "선고",
    "성년",
    "세무사",
    "세율",
    "소득세",
    "소송",
    "소송당사자",
    "소송비용",
    "소송서류",
    "소송절차",
    "소액",
    "소액사건",
    "소유",
    "소유권",
    "소유권자",
    "소유물",
    "손해배상",
    "승소",
    "승인",
    "시세",
    "시행",
    "시행규칙",
    "시행령",
    "시행일",
    "신청",
    "아동",
    "압수",
    "양도",
    "양도인",
    "양수",
    "양수인",
    "연체",
    "영업정지",
    "영업허가",
    "영주권",
    "예약",
    "외국법인",
    "외국인",
    "요구",
    "원고",
    "원심",
    "위반행위",
    "위법행위",
    "위약",
    "위탁",
    "의무",
    "의사",
    "이사",
    "이혼",
    "인감",
    "인감증명",
    "인감증명서",
    "인도",
    "인증",
    "임대계약",
    "임대인",
    "임대차",
    "임대차계약",
    "임차보증금",
    "임차인",
    "입양",
    "자산",
    "자유",
    "재계약",
    "재물",
    "재산",
    "재산권",
    "재산세",
    "재판",
    "재판관",
    "재판부",
    "쟁점",
    "저작권",
    "전입신고",
    "정상",
    "조건",
    "주민",
    "주민등록",
    "주민등록등본",
    "주소",
    "주장",
    "지급",
    "지급기일",
    "지방법원",
    "지방세",
    "지역",
    "진술",
    "집행",
    "집행관",
    "참가",
    "참가인",
    "책임",
    "청구",
    "총회",
    "출생신고",
    "취득세",
    "취소",
    "친권",
    "친권자",
    "토지",
    "토지소유권",
    "특허권",
    "파산",
    "판결",
    "판결문",
    "판결서",
    "판결이유",
    "판례",
    "피해자보호명령",
    "하자",
    "합의",
    "합의서",
    "항소심",
    "항소인",
    "해약",
    "해제",
    "해지",
    "행사",
    "행위",
    "행정소송",
    "허가",
    "허위진술",
    "화재",
    "확인",
    "확정일자",
    "효력",
    "후견인",
}

# 개별 법률명은 어려운 법률 개념의 설명 대상이 아니라 출처·고유명으로 취급한다.
STATUTE_NAME_SUFFIXES = ("법", "법률", "특례법")


def parse_args() -> argparse.Namespace:
    """입력과 출력 위치를 정의한다."""
    parser = argparse.ArgumentParser(description="사용자용 법률 용어 후보 1차 선별")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL을 행 목록으로 읽는다."""
    with path.open("r", encoding="utf-8-sig") as file:
        return [json.loads(line) for line in file if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """행 목록을 UTF-8 JSONL로 저장한다."""
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def is_statute_name(term: str) -> bool:
    """법률명처럼 보이는 고유명을 판별한다."""
    return len(term) >= 3 and term.endswith(STATUTE_NAME_SUFFIXES)


def classify_term(term: str) -> tuple[str, str]:
    """사용자 기준과 보수적인 규칙으로 후보 여부를 정한다."""
    if term in USER_SELECTED_TERMS:
        return "selected", "사용자_선택_예시"
    if term in USER_EXCLUDED_TERMS:
        return "excluded", "사용자_제외_예시"
    if term in OBVIOUS_COMMON_TERMS:
        return "excluded", "일반_성인이_문맥에서_이해_가능"
    if is_statute_name(term):
        return "excluded", "법률명_또는_고유명"
    return "selected", "애매하면_후보_보존"


def classify_row(row: dict[str, Any]) -> tuple[str, str]:
    """기존 정제 결과를 존중한 뒤 사용자 난이도 기준을 적용한다."""
    term = str(row["term"])
    if term in USER_SELECTED_TERMS or term in USER_EXCLUDED_TERMS:
        return classify_term(term)
    if not bool(row.get("include_in_glossary", False)):
        return "excluded", "기존_정제에서_오탐_또는_범위밖"
    return classify_term(term)


def generation_status(row: dict[str, Any], decision: str) -> str:
    """후보 선택 이후 쉬운 정의 생성 가능 상태를 다시 계산한다."""
    if decision == "excluded":
        return "excluded"
    if row.get("review_status") == "definition_review_required":
        return "ready_with_context_warning"
    return "ready"


def definition_warning(row: dict[str, Any], decision: str) -> str:
    """원 정의 사용 시 검수자가 알아야 할 내부 품질 경고를 반환한다."""
    if (
        decision == "selected"
        and row.get("review_status") == "definition_review_required"
    ):
        return (
            "우리말샘 원 정의와 판례·법령의 실제 사용 문맥이 일치하는지 미검증. "
            "쉬운 정의 생성 후 원문 문맥 확인 권장."
        )
    return ""


def flatten_definitions(value: Any) -> str:
    """CSV 검토를 위해 정의 목록을 줄바꿈 텍스트로 만든다."""
    if not isinstance(value, list):
        return str(value or "")
    definitions = []
    for index, item in enumerate(value, start=1):
        if isinstance(item, dict):
            definition = str(item.get("definition", "")).strip()
            category = str(item.get("category", "")).strip()
            definitions.append(f"{index}. [{category}] {definition}")
        else:
            definitions.append(f"{index}. {item}")
    return "\n".join(definitions)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """선택 결과를 눈으로 검토하기 쉬운 CSV로 저장한다."""
    headers = [
        "candidate_decision",
        "selection_reason",
        "generation_status",
        "definition_warning",
        "term",
        "match_key",
        "source_type",
        "legal_definition_count",
        "legal_definitions_text",
        "precedent_case_count",
        "statute_document_count",
        "review_status",
        "easy_definition_ready",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "candidate_decision": row["candidate_decision"],
                    "selection_reason": row["selection_reason"],
                    "generation_status": row["generation_status"],
                    "definition_warning": row["definition_warning"],
                    "term": row["term"],
                    "match_key": row["match_key"],
                    "source_type": row.get("source_type", ""),
                    "legal_definition_count": row.get("legal_definition_count", 0),
                    "legal_definitions_text": flatten_definitions(
                        row.get("legal_definitions", [])
                    ),
                    "precedent_case_count": row.get("precedent_case_count", 0),
                    "statute_document_count": row.get("statute_document_count", 0),
                    "review_status": row.get("review_status", ""),
                    "easy_definition_ready": row.get("easy_definition_ready", False),
                }
            )


def main() -> None:
    """전체 용어를 분류하고 검토 파일과 통계를 저장한다."""
    args = parse_args()
    rows = read_jsonl(args.input)
    classified = []
    for row in rows:
        decision, reason = classify_row(row)
        classified.append(
            {
                **row,
                "candidate_decision": decision,
                "selection_reason": reason,
                "generation_status": generation_status(row, decision),
                "definition_warning": definition_warning(row, decision),
            }
        )

    classified.sort(
        key=lambda row: (
            0 if row["candidate_decision"] == "selected" else 1,
            row["term"],
        )
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "user_facing_legal_term_review.jsonl", classified)
    write_csv(args.output_dir / "user_facing_legal_term_review.csv", classified)

    selected = [row for row in classified if row["candidate_decision"] == "selected"]
    excluded = [row for row in classified if row["candidate_decision"] == "excluded"]
    write_jsonl(args.output_dir / "selected_terms.jsonl", selected)
    write_jsonl(args.output_dir / "excluded_terms.jsonl", excluded)

    manifest = {
        "schema_version": "user_facing_legal_term_selection.v0.1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input": str(args.input),
        "total_count": len(classified),
        "selected_count": len(selected),
        "excluded_count": len(excluded),
        "generation_status_counts": dict(
            Counter(row["generation_status"] for row in classified)
        ),
        "reason_counts": dict(Counter(row["selection_reason"] for row in classified)),
        "policy": {
            "selection_goal": "법률에 익숙하지 않은 20대 초반 사용자의 문맥 이해 지원",
            "exclude": "일반 성인이 문장에서 대략적인 뜻을 바로 이해할 수 있는 용어",
            "select": "법적 효과나 절차를 이름만으로 추측하기 어려운 전문 용어",
            "uncertain": "애매하면 제외하지 않고 후보로 보존",
        },
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"전체: {len(classified)}개")
    print(f"후보선택: {len(selected)}개")
    print(f"제외: {len(excluded)}개")
    print(f"결과 폴더: {args.output_dir}")


if __name__ == "__main__":
    main()
