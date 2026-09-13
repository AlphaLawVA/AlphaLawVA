# chunk_precedents_b.py
"""
Description: final_cases 판례 JSON을 B안(reason + generated summary + issue + holding) 청크 구조로 변환한다.
A안과 같은 이유 청킹 기준을 유지하면서 판시사항과 판결요지를 별도 검색 청크로 추가한다.
Author: choeminju
Date: 2026-09-13
Before:
    - local_data/precedents/processed/final_cases/에 생성요약과 공식 판시사항·판결요지가 포함된 판례 JSON이 있는 상태.
After:
    - local_data/precedents/chunks/B_reason_summary_issue_holding_v1/에 판례별 청크 JSON, 전체 chunks.jsonl, manifest가 생성.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml.preprocessing.precedents.chunk_precedents_a import (  # noqa: E402
    DEFAULT_CHUNK_SIZE,
    DEFAULT_OVERLAP_SIZE,
    FINAL_CASES_DIR,
    SCHEMA_VERSION,
    case_no_list_to_metadata,
    chunk_reason_text,
    decision_year,
    iter_case_paths,
    normalize_chunk_text,
    normalize_summary_text,
    now_utc_iso,
    project_relative_path,
    read_json,
    remove_court_signature_tail,
    write_json,
    write_jsonl,
)


LOCAL_DATA_ROOT = PROJECT_ROOT / "local_data"
DEFAULT_OUTPUT_DIR = (
    LOCAL_DATA_ROOT / "precedents" / "chunks" / "B_reason_summary_issue_holding_v1"
)

CHUNKING_STRATEGY = "B_reason_summary_issue_holding_v1"

SECTION_TO_ID_PART = {
    "이유": "reason",
    "생성요약": "summary",
    "판시사항": "issue",
    "판결요지": "holding",
}

SINGLE_CHUNK_SECTIONS = {"생성요약"}


def parse_args() -> argparse.Namespace:
    """커맨드라인 옵션을 정의한다."""
    parser = argparse.ArgumentParser(description="Build B안 precedent chunks from final_cases.")
    parser.add_argument(
        "--final-cases-dir",
        type=Path,
        default=FINAL_CASES_DIR,
        help="생성요약이 포함된 final_cases JSON 폴더.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="청크 결과를 저장할 폴더.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="이유·판시사항·판결요지 청크의 목표 최대 글자 수.",
    )
    parser.add_argument(
        "--overlap-size",
        type=int,
        default=DEFAULT_OVERLAP_SIZE,
        help="긴 섹션 청크 사이에 겹쳐 넣을 글자 수.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="테스트용 처리 개수 제한.",
    )
    return parser.parse_args()


def base_metadata(case: dict[str, Any], case_path: Path) -> dict[str, Any]:
    """모든 B안 청크에 공통으로 들어갈 metadata를 만든다."""
    decision_date = str(case.get("선고일자") or "").strip()
    return {
        "doc_type": "precedent",
        "chunking_strategy": CHUNKING_STRATEGY,
        "precedent_id": str(case.get("판례일련번호") or "").strip(),
        "case_no": str(case.get("사건번호") or "").strip(),
        "case_no_list": case_no_list_to_metadata(case.get("사건번호목록")),
        "case_name": str(case.get("사건명") or "").strip(),
        "court_name": str(case.get("법원명") or "").strip(),
        "decision_date": decision_date,
        "decision_year": decision_year(decision_date),
        "case_type": str(case.get("사건종류명") or "").strip(),
        "judgment_type": str(case.get("판결유형") or "").strip(),
        "source_path": project_relative_path(case_path),
    }


def build_chunk(
    case: dict[str, Any],
    case_path: Path,
    section: str,
    section_chunk_index: int,
    retrieval_text: str,
) -> dict[str, Any]:
    """B안 청크 하나의 JSON 구조를 만든다."""
    precedent_id = str(case.get("판례일련번호") or "").strip()
    id_part = SECTION_TO_ID_PART[section]
    metadata = base_metadata(case, case_path)
    metadata.update(
        {
            "section": section,
            "section_chunk_index": section_chunk_index,
            "chunk_char_count": len(retrieval_text),
        }
    )
    return {
        "chunk_id": f"precedent:{precedent_id}:{id_part}:{section_chunk_index:04d}",
        "source_case_id": precedent_id,
        "chunk_type": section,
        "retrieval_text": retrieval_text,
        "metadata": metadata,
    }


def split_section_text(
    section: str,
    text: str,
    chunk_size: int,
    overlap_size: int,
) -> list[str]:
    """섹션 성격에 맞게 단일 청크 또는 분할 청크 목록을 만든다."""
    if not text:
        return []
    if section in SINGLE_CHUNK_SECTIONS:
        return [text]
    return chunk_reason_text(text, chunk_size, overlap_size)


def build_case_chunks(
    case_path: Path,
    chunk_size: int,
    overlap_size: int,
) -> tuple[dict[str, Any], Counter[str]]:
    """판례 하나에서 이유·생성요약·판시사항·판결요지 청크를 만든다."""
    case = read_json(case_path)
    stats: Counter[str] = Counter()
    chunks = []

    reason = normalize_chunk_text(case.get("이유"))
    reason, removed_signature = remove_court_signature_tail(reason)
    if removed_signature:
        stats["removed_court_signature_tail_count"] += 1

    section_texts = {
        "이유": reason,
        "생성요약": normalize_summary_text(case.get("생성요약")),
        "판시사항": normalize_chunk_text(case.get("판시사항")),
        "판결요지": normalize_chunk_text(case.get("판결요지")),
    }

    for section, text in section_texts.items():
        section_chunks = split_section_text(section, text, chunk_size, overlap_size)
        if not section_chunks:
            stats[f"{section}_empty_count"] += 1
            continue
        for index, section_chunk in enumerate(section_chunks, start=1):
            chunks.append(build_chunk(case, case_path, section, index, section_chunk))

    for chunk in chunks:
        stats[f"{chunk['chunk_type']}_chunk_count"] += 1

    precedent_id = str(case.get("판례일련번호") or "").strip()
    case_payload = {
        "schema_version": SCHEMA_VERSION,
        "chunking_strategy": CHUNKING_STRATEGY,
        "precedent_id": precedent_id,
        "case_name": str(case.get("사건명") or "").strip(),
        "chunk_count": len(chunks),
        "chunks": chunks,
    }
    return case_payload, stats


def build_manifest(
    args: argparse.Namespace,
    stats: Counter[str],
    warnings: list[dict[str, str]],
) -> dict[str, Any]:
    """B안 청킹 실행 결과 manifest를 만든다."""
    return {
        "schema_version": "precedent_chunk_manifest.v1",
        "created_at": now_utc_iso(),
        "chunking_strategy": CHUNKING_STRATEGY,
        "source_final_cases_dir": project_relative_path(args.final_cases_dir),
        "output_dir": project_relative_path(args.output_dir),
        "chunk_size": args.chunk_size,
        "overlap_size": args.overlap_size,
        "included_sections": list(SECTION_TO_ID_PART),
        "stats": dict(stats),
        "warnings": warnings,
        "metadata_fields": [
            "doc_type",
            "chunking_strategy",
            "precedent_id",
            "case_no",
            "case_no_list",
            "case_name",
            "court_name",
            "decision_date",
            "decision_year",
            "case_type",
            "judgment_type",
            "section",
            "section_chunk_index",
            "chunk_char_count",
            "source_path",
        ],
    }


def main() -> None:
    """B안 판례 청크를 생성한다."""
    args = parse_args()
    args.final_cases_dir = args.final_cases_dir.resolve()
    args.output_dir = args.output_dir.resolve()

    by_case_dir = args.output_dir / "by_case"
    chunks_path = args.output_dir / "chunks.jsonl"
    manifest_path = args.output_dir / "manifest.json"

    stats: Counter[str] = Counter()
    warnings: list[dict[str, str]] = []
    all_chunks = []

    for case_path in iter_case_paths(args.final_cases_dir, args.limit):
        case_payload, case_stats = build_case_chunks(case_path, args.chunk_size, args.overlap_size)
        precedent_id = case_payload["precedent_id"] or case_path.stem

        stats["case_count"] += 1
        stats.update(case_stats)
        stats["chunk_count"] += case_payload["chunk_count"]
        if not case_payload["chunks"]:
            stats["empty_chunk_case_count"] += 1
            warnings.append({"case_path": project_relative_path(case_path), "reason": "생성된 청크가 없음"})
            continue

        write_json(by_case_dir / f"{precedent_id}.json", case_payload)
        all_chunks.extend(case_payload["chunks"])

    write_jsonl(chunks_path, all_chunks)
    write_json(manifest_path, build_manifest(args, stats, warnings))

    print(f"완료: {CHUNKING_STRATEGY}")
    print(f"판례 수: {stats['case_count']}")
    print(f"전체 청크 수: {stats['chunk_count']}")
    print(f"이유 청크 수: {stats['이유_chunk_count']}")
    print(f"생성요약 청크 수: {stats['생성요약_chunk_count']}")
    print(f"판시사항 청크 수: {stats['판시사항_chunk_count']}")
    print(f"판결요지 청크 수: {stats['판결요지_chunk_count']}")
    print(f"법관 서명부 제거 추정: {stats['removed_court_signature_tail_count']}")
    print(f"출력 폴더: {args.output_dir}")


if __name__ == "__main__":
    main()
