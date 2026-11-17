# test_precedent_evidence_enricher.py
"""
Description: 계약서 검토·특약 판례 근거에 원본 사건정보와 두 종류의 요약이 보강되는지 검증한다.
법령 근거와 원본이 없는 판례는 기존 메타데이터가 유지되는지도 확인한다.
Author: choeminju
Date: 2026-11-15
Before:
    - 판례 표시용 메타데이터 보강 함수와 안전한 final_cases 저장소가 정의된 상태.
After:
    - 사건번호·생성요약·쉬운요약 보강과 안전한 폴백 동작이 자동 검증됨.
"""

from __future__ import annotations

import json
from pathlib import Path

from api.services.precedent_case_repository import PrecedentCaseRepository
from api.services.precedent_evidence_enricher import (
    enrich_precedent_evidence_metadata,
)


def _repository(tmp_path: Path) -> PrecedentCaseRepository:
    case = {
        "판례일련번호": "12345",
        "사건번호": "2024다12345",
        "사건명": "임대차보증금",
        "법원명": "대법원",
        "선고일자": "2026. 1. 2.",
        "사건종류명": "민사",
        "판결유형": "판결",
        "생성요약": "임대차보증금 반환 범위를 판단한 판례입니다.",
        "쉬운요약": "세입자가 보증금을 돌려받는 문제를 다룬 판례입니다.",
    }
    (tmp_path / "12345.json").write_text(
        json.dumps(case, ensure_ascii=False), encoding="utf-8"
    )
    return PrecedentCaseRepository(tmp_path)


def test_enriches_precedent_display_metadata(tmp_path: Path) -> None:
    enriched = enrich_precedent_evidence_metadata(
        source_type="precedent",
        source_id="12345",
        metadata={"case_no": "검색 메타데이터 사건번호"},
        repository=_repository(tmp_path),
    )

    assert enriched["case_no"] == "2024다12345"
    assert enriched["precedent_summary"] == "임대차보증금 반환 범위를 판단한 판례입니다."
    assert enriched["easy_summary"] == "세입자가 보증금을 돌려받는 문제를 다룬 판례입니다."
    assert enriched["url"].endswith("precSeq=12345")


def test_keeps_existing_metadata_for_non_precedent_or_missing_case(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    statute_metadata = {"law_name": "민법"}
    missing_metadata = {"case_no": "2025다99999"}

    assert enrich_precedent_evidence_metadata(
        source_type="statute",
        source_id="법령-1",
        metadata=statute_metadata,
        repository=repository,
    ) == statute_metadata
    assert enrich_precedent_evidence_metadata(
        source_type="precedent",
        source_id="99999",
        metadata=missing_metadata,
        repository=repository,
    ) == missing_metadata
