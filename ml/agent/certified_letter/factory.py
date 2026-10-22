# factory.py
"""
Description: 내용증명 분류·검색·리랭크·생성·검증 의존성을 하나의 LangGraph로 조립한다.
실제 모델만 주입하면 기존 BGE 검색 기준선으로 실행 가능한 구성 지점을 제공한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 호출자가 그래프의 모든 의존성을 직접 조립해야 하는 상태.
After:
    - 분류기, 생성 모델과 선택형 검색·리랭커를 주입해 워크플로를 만들 수 있음.
"""

from __future__ import annotations

from ml.agent.certified_letter.generator import (
    PromptedLetterDraftGenerator,
    StructuredLetterModel,
)
from ml.agent.certified_letter.graph import build_certified_letter_graph
from ml.agent.certified_letter.nodes import CertifiedLetterNodeDependencies
from ml.agent.certified_letter.ports import (
    PrecedentRerankerPort,
    PrecedentSearchPort,
    ScopeClassifierPort,
    StatuteRerankerPort,
    StatuteSearchPort,
)
from ml.agent.certified_letter.retrieval import (
    BgeM3PrecedentSearchAdapter,
    BgeM3StatuteSearchAdapter,
)
from ml.agent.certified_letter.validators import CertifiedLetterDraftValidator
from ml.rag.reranker import DenseSimilarityReranker


def create_certified_letter_workflow(
    *,
    model: StructuredLetterModel,
    scope_classifier: ScopeClassifierPort,
    statute_search: StatuteSearchPort | None = None,
    precedent_search: PrecedentSearchPort | None = None,
    statute_reranker: StatuteRerankerPort | None = None,
    precedent_reranker: PrecedentRerankerPort | None = None,
):
    dense_baseline = DenseSimilarityReranker()
    dependencies = CertifiedLetterNodeDependencies(
        scope_classifier=scope_classifier,
        statute_search=statute_search or BgeM3StatuteSearchAdapter(),
        precedent_search=precedent_search or BgeM3PrecedentSearchAdapter(),
        statute_reranker=statute_reranker or dense_baseline,
        precedent_reranker=precedent_reranker or dense_baseline,
        generator=PromptedLetterDraftGenerator(model),
        validator=CertifiedLetterDraftValidator(),
    )
    return build_certified_letter_graph(dependencies)
