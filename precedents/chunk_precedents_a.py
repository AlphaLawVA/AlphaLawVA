# chunk_precedents_a.py
"""
Description: final_cases 판례 JSON을 A안(reason + generated summary) 청크 구조로 변환한다.
판례별 by_case JSON과 벡터DB 적재용 chunks.jsonl을 함께 생성하고, 청킹 통계를 manifest로 저장한다.
Author: choeminju
Date: 2026-09-04
Before:
    - local_data/precedents/processed/final_cases/에 생성요약이 포함된 최종 판례 JSON이 있는 상태.

After:
    - local_data/precedents/chunks/A_reason_summary_v1/에 판례별 청크 JSON, 전체 chunks.jsonl, manifest가 생성.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DATA_ROOT = PROJECT_ROOT / "local_data"
FINAL_CASES_DIR = LOCAL_DATA_ROOT / "precedents" / "processed" / "final_cases"
DEFAULT_OUTPUT_DIR = LOCAL_DATA_ROOT / "precedents" / "chunks" / "A_reason_summary_v1"

SCHEMA_VERSION = "precedent_chunks.v1"
CHUNKING_STRATEGY = "A_reason_summary_v1"
DEFAULT_CHUNK_SIZE = 800
DEFAULT_OVERLAP_SIZE = 120

SECTION_TO_ID_PART = {
    "이유": "reason",
    "생성요약": "summary",
}

COURT_SIGNATURE_LINE_RE = re.compile(
    r"(?m)^\s*(?:대법원판사|대법관|판사|재판장|주심)\s*[가-힣A-Za-z·ㆍ\s,()]+$"
)
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.다요임음함됨됨)])\s+")
SHORT_INTRO_RE = re.compile(
    r"(상고이유|재항고이유|항소이유|원심판결 이유|기초사실|판단|본다|대하여)"
)


def parse_args() -> argparse.Namespace:
    """커맨드라인 옵션을 정의한다."""
    parser = argparse.ArgumentParser(description="Build A안 precedent chunks from final_cases.")
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
        help="이유 청크의 목표 최대 글자 수.",
    )
    parser.add_argument(
        "--overlap-size",
        type=int,
        default=DEFAULT_OVERLAP_SIZE,
        help="이유 청크 사이에 겹쳐 넣을 글자 수.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="테스트용 처리 개수 제한.",
    )
    return parser.parse_args()


def now_utc_iso() -> str:
    """현재 UTC 시각을 ISO 문자열로 반환한다."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any]:
    """UTF-8 JSON 파일을 읽는다."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """딕셔너리를 UTF-8 JSON 파일로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """JSON 객체 목록을 JSONL 파일로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def project_relative_path(path: Path) -> str:
    """프로젝트 내부 경로는 상대경로 문자열로 반환한다."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def iter_case_paths(final_cases_dir: Path, limit: int | None = None) -> list[Path]:
    """final_cases JSON 파일을 판례일련번호 순서로 반환한다."""
    paths = sorted(
        final_cases_dir.glob("*.json"),
        key=lambda path: int(path.stem) if path.stem.isdigit() else path.stem,
    )
    if limit is not None:
        return paths[:limit]
    return paths


def normalize_chunk_text(value: Any) -> str:
    """청킹과 임베딩에 넣기 좋게 텍스트 공백과 줄바꿈을 정리한다."""
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_summary_text(value: Any) -> str:
    """생성요약 청크는 줄바꿈 없는 한 문단으로 정리한다."""
    return re.sub(r"\s+", " ", normalize_chunk_text(value)).strip()


def remove_court_signature_tail(text: str) -> tuple[str, bool]:
    """판결문 말미의 법관 서명부로 보이는 줄들을 청킹 본문에서 제외한다."""
    if not text:
        return "", False

    search_start = max(0, len(text) - 1200)
    tail = text[search_start:]
    matches = list(COURT_SIGNATURE_LINE_RE.finditer(tail))
    if not matches:
        return text, False

    cut_at = search_start + matches[0].start()
    cleaned = text[:cut_at].rstrip()
    if len(cleaned) < len(text) * 0.5:
        return text, False
    return cleaned, True


def split_long_unit(text: str, chunk_size: int, overlap_size: int) -> list[str]:
    """긴 단일 문단을 문장 경계와 글자 수 기준으로 나눈다."""
    if len(text) <= chunk_size:
        return [text]

    parts = []
    start = 0
    while start < len(text):
        hard_end = min(len(text), start + chunk_size)
        end = hard_end
        if hard_end < len(text):
            window = text[start:hard_end]
            boundaries = [match.end() for match in SENTENCE_BOUNDARY_RE.finditer(window)]
            if boundaries and boundaries[-1] >= chunk_size * 0.6:
                end = start + boundaries[-1]
        part = text[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(text):
            break
        start = max(end - overlap_size, start + 1)
    return parts


def split_text_units(text: str, chunk_size: int, overlap_size: int) -> list[str]:
    """줄 단위 문단을 청크 조립용 단위로 나눈다."""
    raw_units = [unit.strip() for unit in re.split(r"\n+", text) if unit.strip()]
    merged_units = []
    pending_intro = ""

    for unit in raw_units:
        if pending_intro:
            merged_units.append(f"{pending_intro}\n{unit}")
            pending_intro = ""
            continue
        if len(unit) <= 80 and SHORT_INTRO_RE.search(unit):
            pending_intro = unit
            continue
        merged_units.append(unit)

    if pending_intro:
        if merged_units:
            merged_units[-1] = f"{merged_units[-1]}\n{pending_intro}"
        else:
            merged_units.append(pending_intro)

    compacted_units = []
    index = 0
    while index < len(merged_units):
        unit = merged_units[index]
        if len(unit) < 120 and index + 1 < len(merged_units):
            compacted_units.append(f"{unit}\n{merged_units[index + 1]}")
            index += 2
            continue
        compacted_units.append(unit)
        index += 1

    units = []
    for unit in compacted_units:
        units.extend(split_long_unit(unit, chunk_size, 0))
    return units


def overlap_tail(text: str, overlap_size: int) -> str:
    """다음 청크 시작에 붙일 이전 청크의 끝부분을 만든다."""
    if overlap_size <= 0 or len(text) <= overlap_size:
        return ""
    tail = text[-overlap_size:].strip()
    newline_pos = tail.find("\n")
    if newline_pos >= 0 and newline_pos < len(tail) - 20:
        tail = tail[newline_pos + 1 :].strip()
    return tail


def chunk_reason_text(reason: str, chunk_size: int, overlap_size: int) -> list[str]:
    """이유 텍스트를 순서를 보존한 여러 청크로 나눈다."""
    units = split_text_units(reason, chunk_size, overlap_size)
    chunks = []
    current = ""

    for unit in units:
        separator = "\n" if current else ""
        if current and len(current) + len(separator) + len(unit) > chunk_size:
            chunks.append(current.strip())
            current = overlap_tail(current, overlap_size)
            separator = "\n" if current else ""
            if current and len(current) + len(separator) + len(unit) > chunk_size:
                current = ""
                separator = ""
        current = f"{current}{separator}{unit}".strip()

    if current:
        chunks.append(current.strip())
    return chunks


def case_no_list_to_metadata(value: Any) -> str:
    """사건번호목록을 ChromaDB metadata에 안전한 문자열로 바꾼다."""
    if isinstance(value, list):
        return "|".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def decision_year(value: Any) -> int | None:
    """YYYY-MM-DD 선고일자에서 선고연도를 추출한다."""
    text = str(value or "").strip()
    if re.match(r"^\d{4}", text):
        return int(text[:4])
    return None


def base_metadata(case: dict[str, Any], case_path: Path) -> dict[str, Any]:
    """모든 청크에 공통으로 들어갈 metadata를 만든다."""
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
    """청크 하나의 JSON 구조를 만든다."""
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


def build_case_chunks(case_path: Path, chunk_size: int, overlap_size: int) -> tuple[dict[str, Any], Counter[str]]:
    """판례 하나에서 이유 청크와 생성요약 청크를 만든다."""
    case = read_json(case_path)
    stats: Counter[str] = Counter()
    chunks = []

    reason = normalize_chunk_text(case.get("이유"))
    reason, removed_signature = remove_court_signature_tail(reason)
    if removed_signature:
        stats["removed_court_signature_tail_count"] += 1

    for index, reason_chunk in enumerate(chunk_reason_text(reason, chunk_size, overlap_size), start=1):
        chunks.append(build_chunk(case, case_path, "이유", index, reason_chunk))

    summary = normalize_summary_text(case.get("생성요약"))
    if summary:
        chunks.append(build_chunk(case, case_path, "생성요약", 1, summary))

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
    """청킹 실행 결과 manifest를 만든다."""
    return {
        "schema_version": "precedent_chunk_manifest.v1",
        "created_at": now_utc_iso(),
        "chunking_strategy": CHUNKING_STRATEGY,
        "source_final_cases_dir": project_relative_path(args.final_cases_dir),
        "output_dir": project_relative_path(args.output_dir),
        "chunk_size": args.chunk_size,
        "overlap_size": args.overlap_size,
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
    """A안 판례 청크를 생성한다."""
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
    print(f"법관 서명부 제거 추정: {stats['removed_court_signature_tail_count']}")
    print(f"출력 폴더: {args.output_dir}")


if __name__ == "__main__":
    main()
