# reranker.py
"""
Description: 내용증명 검색 평가에 사용할 교체 가능한 리랭커 구현을 제공한다.
현재 dense 점수 기준선을 제공하고 이후 모델 비교가 같은 포트를 사용하게 한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 내용증명 흐름에서 리랭커를 교체하거나 비활성화할 공통 경계가 없는 상태.
After:
    - 법령·판례 후보를 기존 유사도 기준으로 안정적으로 제한할 수 있음.
"""

from __future__ import annotations

from typing import Protocol, Sequence, TypeVar


class HasSimilarity(Protocol):
    similarity: float


Candidate = TypeVar("Candidate", bound=HasSimilarity)


class DenseSimilarityReranker:
    """모델 리랭커 비교 전후에 사용할 dense 유사도 기준선."""

    def rerank(
        self,
        _query: str,
        candidates: Sequence[Candidate],
        *,
        top_k: int,
    ) -> list[Candidate]:
        if top_k < 1:
            raise ValueError("top_k는 1 이상이어야 합니다.")
        return sorted(
            candidates,
            key=lambda candidate: candidate.similarity,
            reverse=True,
        )[:top_k]
