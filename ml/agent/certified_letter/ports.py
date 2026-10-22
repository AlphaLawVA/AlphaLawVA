# ports.py
"""
Description: 내용증명 그래프가 검색기·리랭커·생성기에 요구하는 포트를 정의한다.
구체 모델과 벡터 DB를 그래프 상태 전이에서 분리하여 테스트와 교체를 가능하게 한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 내용증명 워크플로가 구체 외부 의존성을 직접 호출해야 하는 상태.
After:
    - 검색, 리랭크와 생성 의존성을 명시적으로 주입할 수 있음.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from ml.agent.certified_letter.state import (
    LetterDraftData,
    NormalizedCertifiedLetterRequest,
    PrecedentEvidenceData,
    StatuteEvidenceData,
    ValidationIssue,
)


class StatuteSearchPort(Protocol):
    def search(
        self,
        queries: Sequence[str],
        *,
        candidate_limit: int,
    ) -> Sequence[StatuteEvidenceData]: ...


class PrecedentSearchPort(Protocol):
    def search(
        self,
        queries: Sequence[str],
        *,
        candidate_limit: int,
    ) -> Sequence[PrecedentEvidenceData]: ...


class StatuteRerankerPort(Protocol):
    def rerank(
        self,
        query: str,
        candidates: Sequence[StatuteEvidenceData],
        *,
        top_k: int,
    ) -> Sequence[StatuteEvidenceData]: ...


class PrecedentRerankerPort(Protocol):
    def rerank(
        self,
        query: str,
        candidates: Sequence[PrecedentEvidenceData],
        *,
        top_k: int,
    ) -> Sequence[PrecedentEvidenceData]: ...


class LetterDraftGeneratorPort(Protocol):
    def generate(
        self,
        request: NormalizedCertifiedLetterRequest,
        statute_evidence: Sequence[StatuteEvidenceData],
    ) -> LetterDraftData: ...


class DraftValidatorPort(Protocol):
    def validate(
        self,
        request: NormalizedCertifiedLetterRequest,
        statute_evidence: Sequence[StatuteEvidenceData],
        draft: LetterDraftData,
    ) -> Sequence[ValidationIssue]: ...

    def repair(
        self,
        request: NormalizedCertifiedLetterRequest,
        statute_evidence: Sequence[StatuteEvidenceData],
        draft: LetterDraftData,
        issues: Sequence[ValidationIssue],
    ) -> LetterDraftData: ...
