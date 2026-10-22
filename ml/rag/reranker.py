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

from pathlib import Path
from threading import RLock
from typing import Any, Callable, Protocol, Sequence, TypeVar


class HasSimilarity(Protocol):
    similarity: float


class RerankCandidate(HasSimilarity, Protocol):
    text: str


Candidate = TypeVar("Candidate", bound=HasSimilarity)
TextCandidate = TypeVar("TextCandidate", bound=RerankCandidate)


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


def load_cross_encoder(
    model_path: Path,
    *,
    max_length: int,
    device: str,
    trust_remote_code: bool,
) -> Any:
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as error:
        raise RuntimeError(
            "리랭커 실행 패키지가 없습니다. "
            "pip install -r requirements-embedding.txt 를 실행하세요."
        ) from error
    if not model_path.is_dir():
        raise FileNotFoundError(f"리랭커 모델이 없습니다: {model_path.resolve()}")
    selected_device = None if device == "auto" else device
    return CrossEncoder(
        str(model_path.resolve()),
        max_length=max_length,
        device=selected_device,
        trust_remote_code=trust_remote_code,
        local_files_only=True,
    )


class CrossEncoderReranker:
    """로컬 cross-encoder를 지연 로딩해 질문-근거 쌍을 재정렬한다."""

    def __init__(
        self,
        *,
        model_path: Path,
        max_length: int = 512,
        batch_size: int = 16,
        device: str = "auto",
        trust_remote_code: bool = False,
        model_loader: Callable[..., Any] = load_cross_encoder,
    ) -> None:
        if max_length < 1 or batch_size < 1:
            raise ValueError("리랭커 길이와 배치 크기는 1 이상이어야 합니다.")
        if device not in {"auto", "cpu", "cuda", "mps"}:
            raise ValueError(f"지원하지 않는 reranker device: {device}")
        self.model_path = model_path
        self.max_length = max_length
        self.batch_size = batch_size
        self.device = device
        self.trust_remote_code = trust_remote_code
        self._model_loader = model_loader
        self._model: Any | None = None
        self._lock = RLock()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self.is_loaded:
            return
        with self._lock:
            if self.is_loaded:
                return
            self._model = self._model_loader(
                self.model_path,
                max_length=self.max_length,
                device=self.device,
                trust_remote_code=self.trust_remote_code,
            )

    def rerank(
        self,
        query: str,
        candidates: Sequence[TextCandidate],
        *,
        top_k: int,
    ) -> list[TextCandidate]:
        if top_k < 1:
            raise ValueError("top_k는 1 이상이어야 합니다.")
        if not candidates:
            return []
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("리랭킹 질문은 비어 있을 수 없습니다.")
        self.load()
        assert self._model is not None
        pairs = []
        candidate_indexes = []
        for candidate_index, candidate in enumerate(candidates):
            rerank_texts = tuple(getattr(candidate, "rerank_texts", ()))
            texts = rerank_texts or (candidate.text,)
            for text in texts:
                normalized_text = text.strip()
                if normalized_text:
                    pairs.append((normalized_query, normalized_text))
                    candidate_indexes.append(candidate_index)
        if not pairs:
            raise ValueError("리랭킹 후보 본문이 비어 있습니다.")
        with self._lock:
            raw_scores = self._model.predict(
                pairs,
                batch_size=self.batch_size,
                show_progress_bar=False,
            )
        scores = [float(score) for score in raw_scores]
        if len(scores) != len(pairs):
            raise ValueError("리랭커 점수 수가 질문-본문 쌍 수와 일치하지 않습니다.")
        best_scores = [float("-inf")] * len(candidates)
        for candidate_index, score in zip(candidate_indexes, scores, strict=True):
            best_scores[candidate_index] = max(best_scores[candidate_index], score)
        ranked = sorted(
            zip(candidates, best_scores, range(len(candidates)), strict=True),
            key=lambda item: (-item[1], -item[0].similarity, item[2]),
        )
        return [candidate for candidate, _score, _index in ranked[:top_k]]
