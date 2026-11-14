# precedent_evidence_enricher.py
"""
Description: 계약서 검토와 특약 생성의 판례 검색 근거에 화면 표시용 원본 메타데이터를 보강한다.
검색 청크는 유지하면서 사건번호와 생성요약·쉬운요약을 final_cases에서 안전하게 가져온다.
Author: choeminju
Date: 2026-11-15
Before:
    - 판례 근거에는 대표 검색 청크와 일부 검색 메타데이터만 있어 목록용 쉬운요약이 없는 상태.
After:
    - 판례 근거 메타데이터에 검증된 사건정보와 생성요약·쉬운요약·원문 링크가 추가됨.
"""

from __future__ import annotations

import logging
from typing import Any

from api.services.precedent_case_repository import (
    PrecedentCaseRepository,
    PrecedentCaseRepositoryError,
)


logger = logging.getLogger(__name__)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def enrich_precedent_evidence_metadata(
    *,
    source_type: str,
    source_id: str,
    metadata: dict[str, Any],
    repository: PrecedentCaseRepository | None,
) -> dict[str, Any]:
    """판례 근거만 원본 표시정보로 보강하고 검색 청크 자체는 변경하지 않는다."""

    enriched = dict(metadata)
    if source_type != "precedent" or repository is None:
        return enriched
    try:
        case_data = repository.get(source_id)
    except PrecedentCaseRepositoryError:
        logger.info(
            "판례 표시용 원본을 찾지 못함: precedent_id=%s", source_id
        )
        return enriched

    display_fields = {
        "case_no": _optional_text(case_data.get("사건번호")),
        "case_name": _optional_text(case_data.get("사건명")),
        "court_name": _optional_text(case_data.get("법원명")),
        "decision_date": _optional_text(case_data.get("선고일자")),
        "case_type": _optional_text(case_data.get("사건종류명")),
        "judgment_type": _optional_text(case_data.get("판결유형")),
        "precedent_summary": _optional_text(case_data.get("생성요약")),
        "easy_summary": _optional_text(case_data.get("쉬운요약")),
        "url": (
            "https://law.go.kr/LSW/precInfoP.do?mode=0&precSeq="
            f"{source_id}"
        ),
    }
    enriched.update(
        {key: value for key, value in display_fields.items() if value is not None}
    )
    return enriched
