# precedent_search_eligibility.py
"""
Description: 판례 검색 전용 허용 목록을 읽어 BGE-M3 검색 결과에서
주택 임대차 범위의 판례만 반환한다.
Author: choeminju
Date: 2026-11-06
Before:
    - 전체 판례 벡터 DB에서 상가·매매·토지 판례도 검색 결과로 반환될 수 있음.
After:
    - 원본과 벡터 DB를 변경하지 않고 판례 검색 API에만 허용 목록 필터가 적용됨.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from ml.data_collection.statutes.law_api_common import PROJECT_ROOT
from ml.rag.precedent_retriever import (
    BgeM3PrecedentRetriever,
    PrecedentSearchResult,
)


ELIGIBILITY_SCHEMA_VERSION = "precedent_search_eligibility.v1"
DEFAULT_ELIGIBILITY_MANIFEST_PATH = (
    PROJECT_ROOT
    / "data"
    / "precedents"
    / "metadata"
    / "precedent_search_eligibility_v01.json"
)
DEFAULT_ELIGIBILITY_CANDIDATE_MULTIPLIER = 5


class PrecedentRetriever(Protocol):
    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
    ) -> list[PrecedentSearchResult]: ...


def load_eligible_precedent_ids(path: Path) -> frozenset[str]:
    """검증된 manifest에서 판례 검색 허용 ID를 읽는다."""
    if not path.is_file():
        raise FileNotFoundError(f"판례 검색 허용 목록이 없습니다: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"판례 검색 허용 목록을 읽을 수 없습니다: {path}") from error

    if not isinstance(payload, dict):
        raise ValueError("판례 검색 허용 목록은 JSON 객체여야 합니다.")
    if payload.get("schema_version") != ELIGIBILITY_SCHEMA_VERSION:
        raise ValueError("판례 검색 허용 목록의 schema_version이 올바르지 않습니다.")

    raw_ids = payload.get("eligible_precedent_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise ValueError("eligible_precedent_ids는 비어 있지 않은 배열이어야 합니다.")
    normalized_ids = [str(value).strip() for value in raw_ids]
    if any(not value.isdigit() for value in normalized_ids):
        raise ValueError("판례 검색 허용 ID는 숫자 문자열이어야 합니다.")
    if len(normalized_ids) != len(set(normalized_ids)):
        raise ValueError("판례 검색 허용 ID가 중복되었습니다.")
    return frozenset(normalized_ids)


class EligibilityFilteredPrecedentRetriever:
    """후보 판례를 넉넉히 검색한 뒤 허용 판례만 순위대로 반환한다."""

    def __init__(
        self,
        delegate: PrecedentRetriever,
        eligible_precedent_ids: frozenset[str],
        *,
        candidate_multiplier: int = DEFAULT_ELIGIBILITY_CANDIDATE_MULTIPLIER,
    ) -> None:
        if not eligible_precedent_ids:
            raise ValueError("판례 검색 허용 ID 목록은 비어 있을 수 없습니다.")
        if candidate_multiplier < 1:
            raise ValueError("허용 목록 후보 배수는 1 이상이어야 합니다.")
        self.delegate = delegate
        self.eligible_precedent_ids = eligible_precedent_ids
        self.candidate_multiplier = candidate_multiplier

    @property
    def is_loaded(self) -> bool:
        """기존 BGE-M3 검색기의 지연 로딩 상태를 그대로 노출한다."""
        return bool(getattr(self.delegate, "is_loaded", False))

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
    ) -> list[PrecedentSearchResult]:
        if top_k < 1:
            raise ValueError("top_k는 1 이상이어야 합니다.")
        candidates = self.delegate.search(
            query,
            top_k=top_k * self.candidate_multiplier,
        )
        eligible = [
            result
            for result in candidates
            if result.precedent_id in self.eligible_precedent_ids
        ][:top_k]
        return [
            replace(result, rank=rank)
            for rank, result in enumerate(eligible, start=1)
        ]


def create_precedent_search_retriever_from_env() -> PrecedentRetriever:
    """환경 설정에 따라 판례 검색 API 전용 허용 목록 필터를 만든다."""
    delegate = BgeM3PrecedentRetriever()
    enabled = os.environ.get("PRECEDENT_SEARCH_ELIGIBILITY_ENABLED", "true")
    if enabled.strip().lower() in {"0", "false", "no", "off"}:
        return delegate

    configured_path = os.environ.get(
        "PRECEDENT_SEARCH_ELIGIBILITY_MANIFEST",
        "",
    ).strip()
    manifest_path = (
        Path(configured_path).expanduser()
        if configured_path
        else DEFAULT_ELIGIBILITY_MANIFEST_PATH
    )
    if not manifest_path.is_absolute():
        manifest_path = PROJECT_ROOT / manifest_path
    candidate_multiplier = int(
        os.environ.get(
            "PRECEDENT_SEARCH_ELIGIBILITY_CANDIDATE_MULTIPLIER",
            str(DEFAULT_ELIGIBILITY_CANDIDATE_MULTIPLIER),
        )
    )
    return EligibilityFilteredPrecedentRetriever(
        delegate,
        load_eligible_precedent_ids(manifest_path),
        candidate_multiplier=candidate_multiplier,
    )
