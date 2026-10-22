# retrieval.py
"""
Description: 기존 BGE-M3 법령·판례 검색 결과를 내용증명 그래프 근거로 변환한다.
여러 쟁점 질의의 결과를 출처 식별자 기준으로 병합하고 최상 점수를 보존한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 기존 검색기 결과 타입이 내용증명 그래프 포트와 직접 맞지 않는 상태.
After:
    - 검색기를 재구현하지 않고 법령·판례 근거 포트로 주입할 수 있음.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from ml.agent.certified_letter.state import (
    PrecedentEvidenceData,
    StatuteEvidenceData,
)
from ml.rag.precedent_retriever import (
    BgeM3PrecedentRetriever,
    PrecedentSearchResult,
)
from ml.rag.statute_retriever import BgeM3StatuteRetriever, StatuteSearchResult


OFFICIAL_PRECEDENT_URL = (
    "https://law.go.kr/LSW/precInfoP.do?mode=0&precSeq={precedent_id}"
)


class StatuteSearchManyBackend(Protocol):
    def search_many(
        self,
        queries: Sequence[str],
        *,
        top_k: int,
    ) -> list[list[StatuteSearchResult]]: ...


class PrecedentSearchManyBackend(Protocol):
    def search_many(
        self,
        queries: Sequence[str],
        *,
        top_k: int,
    ) -> list[list[PrecedentSearchResult]]: ...


class BgeM3StatuteSearchAdapter:
    def __init__(self, retriever: StatuteSearchManyBackend | None = None) -> None:
        self.retriever = retriever or BgeM3StatuteRetriever()

    def search(
        self,
        queries: Sequence[str],
        *,
        candidate_limit: int,
    ) -> list[StatuteEvidenceData]:
        batches = self.retriever.search_many(queries, top_k=candidate_limit)
        merged: dict[tuple[str, str], StatuteEvidenceData] = {}
        for result in _flatten(batches):
            evidence = _to_statute_evidence(result)
            if evidence is None:
                continue
            identity = (
                evidence.statute_id or evidence.law_name,
                evidence.article_label,
            )
            existing = merged.get(identity)
            if existing is None or evidence.similarity > existing.similarity:
                merged[identity] = evidence
        return sorted(merged.values(), key=lambda item: item.similarity, reverse=True)[
            :candidate_limit
        ]


class BgeM3PrecedentSearchAdapter:
    def __init__(self, retriever: PrecedentSearchManyBackend | None = None) -> None:
        self.retriever = retriever or BgeM3PrecedentRetriever()

    def search(
        self,
        queries: Sequence[str],
        *,
        candidate_limit: int,
    ) -> list[PrecedentEvidenceData]:
        batches = self.retriever.search_many(queries, top_k=candidate_limit)
        merged: dict[str, PrecedentEvidenceData] = {}
        for result in _flatten(batches):
            evidence = _to_precedent_evidence(result)
            existing = merged.get(evidence.precedent_id)
            if existing is None or evidence.similarity > existing.similarity:
                merged[evidence.precedent_id] = evidence
        return sorted(merged.values(), key=lambda item: item.similarity, reverse=True)[
            :candidate_limit
        ]


def _to_statute_evidence(
    result: StatuteSearchResult,
) -> StatuteEvidenceData | None:
    if not result.law_name or not result.article_label or not result.text.strip():
        return None
    source_url = _optional_text(result.metadata.get("source_url"))
    return StatuteEvidenceData(
        chunk_id=result.chunk_id,
        text=result.text.strip(),
        similarity=result.similarity,
        statute_id=_optional_text(result.law_id),
        law_name=result.law_name.strip(),
        article_label=result.article_label.strip(),
        article_title=_optional_text(result.article_title),
        effective_date=_optional_text(result.effective_date),
        source_node_id=_optional_text(result.source_node_id),
        source_url=source_url,
    )


def _to_precedent_evidence(
    result: PrecedentSearchResult,
) -> PrecedentEvidenceData:
    summary = _optional_text(result.metadata.get("easy_summary"))
    if summary is None:
        summary = _optional_text(result.metadata.get("precedent_summary"))
    return PrecedentEvidenceData(
        precedent_id=result.precedent_id,
        text=result.text.strip(),
        similarity=result.similarity,
        case_number=_optional_text(result.case_no),
        case_name=_optional_text(result.case_name),
        court_name=_optional_text(result.court_name),
        decision_date=_optional_text(result.decision_date),
        summary=summary,
        source_url=OFFICIAL_PRECEDENT_URL.format(
            precedent_id=result.precedent_id
        ),
    )


def _flatten(values):
    for batch in values:
        yield from batch


def _optional_text(value) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
