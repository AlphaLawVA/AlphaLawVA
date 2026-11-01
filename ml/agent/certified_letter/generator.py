# generator.py
"""
Description: 공급자 중립 구조화 모델을 내용증명 생성·수정 포트에 연결한다.
모델 선택 전에도 동일한 프롬프트와 출력 타입으로 그래프를 시험할 수 있게 한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 프롬프트와 모델 호출 경계가 정해지지 않은 상태.
After:
    - 구조화 모델 어댑터만 교체해 생성·수정 노드를 실행할 수 있음.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from ml.agent.certified_letter.prompts import (
    PromptBundle,
    build_generation_prompt,
    build_repair_prompt,
)
from ml.agent.certified_letter.state import (
    LetterDraftData,
    NormalizedCertifiedLetterRequest,
    StatuteEvidenceData,
    ValidationIssue,
)


class StructuredLetterModel(Protocol):
    def invoke(self, prompt: PromptBundle) -> LetterDraftData: ...


class PromptedLetterDraftGenerator:
    def __init__(self, model: StructuredLetterModel) -> None:
        self.model = model

    def generate(
        self,
        request: NormalizedCertifiedLetterRequest,
        statute_evidence: Sequence[StatuteEvidenceData],
    ) -> LetterDraftData:
        return self.model.invoke(build_generation_prompt(request, statute_evidence))

    def repair(
        self,
        request: NormalizedCertifiedLetterRequest,
        statute_evidence: Sequence[StatuteEvidenceData],
        draft: LetterDraftData,
        issues: Sequence[ValidationIssue],
    ) -> LetterDraftData:
        return self.model.invoke(
            build_repair_prompt(
                request,
                statute_evidence,
                draft,
                issues,
            )
        )
