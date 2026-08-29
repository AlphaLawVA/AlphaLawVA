# preprocess_precedents.py
"""
Description: 수집된 판례 상세 JSON을 청킹 전 전처리본으로 변환한다.
판례내용에서 주문, 청구취지, 원심판결, 이유를 분리하고 사건번호와 선고일자를 정리한다.
Author: choeminju
Date: 2026-08-29
Before:
    - local_data/precedents/raw/details/에 국가법령정보 API 상세 응답 원본이 있는 상태.

After:
    - local_data/precedents/processed/cases/에 flat 한글 필드의 판례 전처리 JSON이 생성.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from precedent_config import (
    PREPROCESS_INDEX_PATH,
    PREPROCESS_REPORT_PATH,
    PROCESSED_CASES_DIR,
    PROJECT_ROOT,
    RAW_DETAILS_DIR,
    ensure_collection_dirs,
    now_utc_iso,
)


CASE_TYPE_MAP = {
    "다": "민사 상고사건",
    "다카": "민사 상고사건",
    "민상": "민사 상고사건",
    "가단": "민사 단독사건",
    "가합": "민사 합의사건",
    "가소": "소액 민사사건",
    "나": "민사 항소사건",
    "재나": "민사 재심 항소사건",
    "도": "형사 상고사건",
    "고합": "형사 합의사건",
    "노": "형사 항소사건",
    "누": "행정 상고사건",
    "구": "행정 1심 사건",
    "구합": "행정 합의사건",
    "행": "행정소송 사건",
    "행상": "행정 상고사건",
    "카합": "가처분 등 민사 비송 합의사건",
    "추": "재심청구 사건",
    "민공": "민사 공시송달 사건",
}


@dataclass
class PreprocessStats:
    """전처리 실행 결과를 report에 남기기 위한 통계."""

    started_at: str
    total_count: int = 0
    processed_count: int = 0
    overwritten_count: int = 0
    skipped_existing_count: int = 0
    failed_count: int = 0
    section_case_counts: Counter[str] = field(default_factory=Counter)
    missing_field_counts: Counter[str] = field(default_factory=Counter)
    case_type_counts: Counter[str] = field(default_factory=Counter)
    failure_examples: list[dict[str, str]] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    """커맨드라인 옵션을 정의한다."""
    parser = argparse.ArgumentParser(description="Preprocess raw precedent detail JSON files.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="테스트용 처리 개수 제한.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="이미 생성된 processed/cases JSON도 다시 만든다.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=500,
        help="N건마다 진행 상황을 출력한다.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    """UTF-8 JSON 파일을 읽는다."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """딕셔너리를 UTF-8 JSON 파일로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def display_path(path: Path) -> str:
    """로컬 절대경로 대신 프로젝트 기준 상대경로를 문자열로 만든다."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def normalize_text(value: Any) -> str:
    """HTML 줄바꿈과 공백을 정리해 사람이 읽기 쉬운 텍스트로 만든다."""
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_case_name(value: Any) -> str:
    """사건명에서 검색·표시에 방해되는 따옴표와 과도한 공백만 정리한다."""
    text = normalize_text(value)
    text = text.replace('"', "").replace("“", "").replace("”", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def format_decision_date(value: Any) -> str:
    """선고일자를 YYYY-MM-DD 문자열로 정리하고 이상값은 '날짜 없음'으로 둔다."""
    raw = re.sub(r"\D", "", str(value or ""))
    if len(raw) != 8:
        return "날짜 없음"
    year = int(raw[:4])
    month = int(raw[4:6])
    day = int(raw[6:8])
    if year < 1800 or not 1 <= month <= 12 or not 1 <= day <= 31:
        return "날짜 없음"
    return f"{year:04d}-{month:02d}-{day:02d}"


def normalize_case_number(value: Any) -> str:
    """사건번호 공백을 정리하고 단기연도 형식이면 서기연도로 바꾼다."""
    case_number = re.sub(r"\s+", "", str(value or ""))
    match = re.match(r"^([3-9]\d{3})([가-힣]+)(\d+)$", case_number)
    if not match:
        return case_number
    year = int(match.group(1)) - 2333
    return f"{year}{match.group(2)}{match.group(3)}"


def parse_case_number(case_number: str) -> tuple[int | None, str, str, str]:
    """사건번호에서 접수연도, 사건부호, 접수번호, 사건유형을 추출한다."""
    match = re.match(r"^(\d{2,4})([가-힣]+)(\d+)$", case_number)
    if not match:
        return None, "", "", ""

    year_raw, code, number = match.groups()
    if len(year_raw) == 4:
        receipt_year = int(year_raw)
    else:
        year = int(year_raw)
        receipt_year = 2000 + year if year < 30 else 1900 + year
    return receipt_year, code, number, CASE_TYPE_MAP.get(code, "")


def extract_prec_service(raw_payload: dict[str, Any]) -> dict[str, Any]:
    """API 상세 응답에서 PrecService 객체를 꺼낸다."""
    service = raw_payload.get("response", {}).get("PrecService", {})
    return service if isinstance(service, dict) else {}


def get_precedent_id(raw_payload: dict[str, Any], raw_path: Path) -> str:
    """raw 파일에서 판례일련번호를 찾고 없으면 파일명을 사용한다."""
    service = extract_prec_service(raw_payload)
    return str(
        raw_payload.get("precedent_id")
        or service.get("판례정보일련번호")
        or raw_path.stem
    ).strip()


def iter_section_matches(text: str) -> list[dict[str, Any]]:
    """판례내용에서 【...】 형태의 섹션 제목 위치를 순서대로 찾는다."""
    matches = list(re.finditer(r"【\s*([^】]{1,40}?)\s*】", text))
    sections: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        title = re.sub(r"\s+", "", match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append(
            {
                "제목": title,
                "표준제목": normalize_section_title(title),
                "제목시작": match.start(),
                "본문시작": start,
                "다음제목시작": end,
            }
        )
    return sections


def normalize_section_title(title: str) -> str:
    """다양하게 표기되는 섹션 제목을 전처리 필드명으로 맞춘다."""
    compact = re.sub(r"\s+", "", title)
    if compact == "주문":
        return "주문"
    if compact in {"청구취지", "청구취지및항소취지", "청구및항소취지"}:
        return "청구취지"
    if compact in {"항소취지", "상고취지", "신청취지"}:
        return "청구취지"
    if compact in {"이유", "이유"}:
        return "이유"
    if compact in {"원심판결", "원판결", "제1심판결", "제1심판결문"}:
        return "원심판결"
    return ""


def split_precedent_body(body: str) -> dict[str, str]:
    """정리된 판례내용을 주문, 청구취지, 원심판결, 이유로 나눈다."""
    result = {
        "주문": extract_section_until(body, "주문", {"이유", "청구취지"}),
        "청구취지": extract_section_until(body, "청구취지", {"주문", "이유"}),
        "원심판결": extract_section_until(body, "원심판결", {"주문", "청구취지", "이유"}),
        "이유": extract_section_until(body, "이유", set()),
    }
    return result


def extract_section_until(body: str, target_title: str, stop_titles: set[str]) -> str:
    """목표 섹션부터 다음 주요 섹션 전까지의 본문을 추출한다.

    `이유` 안에는 `범죄사실`, `증거의 요지`, `법령의 적용`처럼 하위 제목이
    다시 등장할 수 있으므로 모든 제목에서 끊지 않고 주요 섹션 기준으로만 끊는다.
    """
    sections = iter_section_matches(body)
    for index, section in enumerate(sections):
        if section["표준제목"] != target_title:
            continue
        start = int(section["본문시작"])
        end = len(body)
        if stop_titles:
            for next_section in sections[index + 1 :]:
                if next_section["표준제목"] in stop_titles:
                    end = int(next_section["제목시작"])
                    break
        return body[start:end].strip()
    return ""


def build_processed_case(raw_path: Path) -> dict[str, Any]:
    """raw 상세 JSON 하나를 flat 한글 필드의 전처리 JSON으로 변환한다."""
    raw_payload = read_json(raw_path)
    service = extract_prec_service(raw_payload)
    precedent_id = get_precedent_id(raw_payload, raw_path)
    case_number = normalize_case_number(service.get("사건번호"))
    receipt_year, _case_code, receipt_number, case_type = parse_case_number(case_number)
    body = normalize_text(service.get("판례내용"))
    sections = split_precedent_body(body)

    processed_case = {
        "판례일련번호": precedent_id,
        "원본파일경로": display_path(raw_path),
        "사건번호": case_number,
        "접수연도": receipt_year,
        "사건유형": case_type,
        "접수번호": receipt_number,
        "사건명": clean_case_name(service.get("사건명")),
        "법원명": normalize_text(service.get("법원명")),
        "선고일자": format_decision_date(service.get("선고일자")),
        "사건종류명": normalize_text(service.get("사건종류명")),
        "판결유형": normalize_text(service.get("판결유형") or service.get("선고")),
        "판시사항": normalize_text(service.get("판시사항")),
        "판결요지": normalize_text(service.get("판결요지")),
        "참조조문": normalize_text(service.get("참조조문")),
        "참조판례": normalize_text(service.get("참조판례")),
        "주문": sections["주문"],
        "청구취지": sections["청구취지"],
        "원심판결": sections["원심판결"],
        "이유": sections["이유"],
    }
    return processed_case


def update_stats(stats: PreprocessStats, processed_case: dict[str, Any]) -> None:
    """전처리 결과 통계를 갱신한다."""
    stats.processed_count += 1
    for section_name in ["주문", "청구취지", "원심판결", "이유"]:
        if processed_case.get(section_name):
            stats.section_case_counts[section_name] += 1
    case_type = str(processed_case.get("사건종류명") or "미상")
    stats.case_type_counts[case_type] += 1
    for key, value in processed_case.items():
        if key == "원본파일경로":
            continue
        if value is None or value == "":
            stats.missing_field_counts[key] += 1


def build_index_item(processed_case: dict[str, Any]) -> dict[str, Any]:
    """전체 판례를 훑어보기 위한 가벼운 index 항목을 만든다."""
    return {
        "판례일련번호": processed_case["판례일련번호"],
        "사건번호": processed_case["사건번호"],
        "사건명": processed_case["사건명"],
        "법원명": processed_case["법원명"],
        "선고일자": processed_case["선고일자"],
        "사건종류명": processed_case["사건종류명"],
        "사건유형": processed_case["사건유형"],
        "전처리파일경로": str(
            display_path(PROCESSED_CASES_DIR / f"{processed_case['판례일련번호']}.json")
        ),
    }


def write_report(stats: PreprocessStats, index_items: list[dict[str, Any]]) -> None:
    """전처리 실행 요약과 통계를 report/index 파일로 저장한다."""
    report = {
        "전처리버전": "precedent_preprocess_v1",
        "시작시각": stats.started_at,
        "종료시각": now_utc_iso(),
        "입력폴더": display_path(RAW_DETAILS_DIR),
        "출력폴더": display_path(PROCESSED_CASES_DIR),
        "전체대상수": stats.total_count,
        "전처리완료수": stats.processed_count,
        "덮어쓴파일수": stats.overwritten_count,
        "기존파일스킵수": stats.skipped_existing_count,
        "실패수": stats.failed_count,
        "섹션보유판례수": dict(stats.section_case_counts),
        "빈필드통계": dict(stats.missing_field_counts),
        "사건종류통계": dict(stats.case_type_counts),
        "실패예시": stats.failure_examples,
    }
    write_json(PREPROCESS_REPORT_PATH, report)
    write_json(PREPROCESS_INDEX_PATH, index_items)


def iter_raw_detail_paths(limit: int | None) -> list[Path]:
    """처리할 raw 상세 JSON 경로를 정렬해서 반환한다."""
    paths = sorted(RAW_DETAILS_DIR.glob("*.json"))
    if limit is not None:
        return paths[:limit]
    return paths


def main() -> int:
    """판례 raw 상세 JSON 전처리를 실행한다."""
    args = parse_args()
    ensure_collection_dirs()
    raw_paths = iter_raw_detail_paths(args.limit)
    stats = PreprocessStats(started_at=now_utc_iso(), total_count=len(raw_paths))
    index_items: list[dict[str, Any]] = []

    for index, raw_path in enumerate(raw_paths, start=1):
        output_path = PROCESSED_CASES_DIR / f"{raw_path.stem}.json"
        if output_path.exists() and not args.overwrite:
            stats.skipped_existing_count += 1
            try:
                index_items.append(build_index_item(read_json(output_path)))
            except Exception:
                pass
            continue

        try:
            processed_case = build_processed_case(raw_path)
            output_path = PROCESSED_CASES_DIR / f"{processed_case['판례일련번호']}.json"
            if output_path.exists() and args.overwrite:
                stats.overwritten_count += 1
            write_json(output_path, processed_case)
            update_stats(stats, processed_case)
            index_items.append(build_index_item(processed_case))
        except Exception as exc:
            stats.failed_count += 1
            if len(stats.failure_examples) < 20:
                stats.failure_examples.append({"파일": str(raw_path), "오류": str(exc)})

        if args.progress_every > 0 and index % args.progress_every == 0:
            print(
                f"전처리 진행 {index}/{len(raw_paths)}: "
                f"완료 {stats.processed_count}건, "
                f"스킵 {stats.skipped_existing_count}건, "
                f"실패 {stats.failed_count}건",
                flush=True,
            )

    write_report(stats, index_items)
    print(
        "완료: "
        f"대상 {stats.total_count}건, "
        f"전처리 {stats.processed_count}건, "
        f"스킵 {stats.skipped_existing_count}건, "
        f"실패 {stats.failed_count}건, "
        f"출력={PROCESSED_CASES_DIR}",
        flush=True,
    )
    return 0 if stats.failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
