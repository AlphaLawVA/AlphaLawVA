# contract_review_retriever.py
"""
Description: 계약서 검토용 비식별 질의를 법령과 판례 검색기로 전달하고
출처 메타데이터를 보존한 근거 목록으로 변환한다.
Author: ooheunsu
Date: 2026-10-27
Before:
    - 계약서 검토 그래프가 비식별 검색 질의까지만 구성한 상태.
After:
    - 주입된 법령·판례 검색 결과를 항목별 근거 객체로 변환할 수 있음.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Protocol

from ml.agent.contract_review_graph import (
    ContractReviewEvidence,
    ContractReviewRetrievalQuery,
)
from ml.rag.precedent_eligibility import (
    create_eligibility_filtered_precedent_retriever_from_env,
)
from ml.rag.precedent_retriever import BgeM3PrecedentRetriever, PrecedentSearchResult
from ml.rag.reranker import (
    SHARED_CROSS_ENCODER_MODEL_LOADER,
    CrossEncoderReranker,
)
from ml.rag.statute_retriever import (
    BgeM3StatuteRetriever,
    StatuteSearchResult,
    load_bge_model,
)


class StatuteRetriever(Protocol):
    """계약서 검토에 필요한 법령 검색 최소 인터페이스다."""

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
    ) -> list[StatuteSearchResult]: ...


class PrecedentRetriever(Protocol):
    """계약서 검토에 필요한 판례 검색 최소 인터페이스다."""

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
    ) -> list[PrecedentSearchResult]: ...


logger = logging.getLogger(__name__)

MAX_RERANK_PASSAGE_CHARS = 1500  # 리랭커 입력 길이(약 512토큰)에 맞춘 안전 절단. 2026-11-12 시험과 같은 값.

# 민간임대주택·공공주택 특별법 계열은 임대사업자로 등록한 주택에만 적용되는데,
# "임대차", "관리비", "수선" 같은 일반 임대차와 겹치는 낱말 때문에 일반 개인 간
# 전월세에도 계속 후보로 올라온다(2026-11-13 조합2·5 테스트에서 확인). 계약서
# 원문에 등록임대 관련 표현이 없으면 이 법령군은 후보에서 뺀다.
_REGISTERED_RENTAL_BUSINESS_LAW = re.compile(
    r"민간임대주택|공공주택\s*특별법|공공건설임대주택|주거약자"
)
_REGISTERED_RENTAL_BUSINESS_MARKER = re.compile(
    r"임대사업자|등록임대|민간임대주택|공공임대주택|공공주택"
)


@dataclass(frozen=True)
class _RerankView:
    """리랭커에 넘길 조문 표현이다. 시험과 같이 법령명·조문 표기를 본문 앞에 붙인다."""

    source_id: str
    text: str
    similarity: float


def _rerank_view(evidence: ContractReviewEvidence) -> _RerankView:
    metadata = evidence.metadata or {}
    head = " ".join(
        part
        for part in (metadata.get("law_name"), metadata.get("article_label"), metadata.get("article_title"))
        if part
    )
    passage = f"{head}\n{evidence.text}" if head else evidence.text
    return _RerankView(evidence.source_id, passage[:MAX_RERANK_PASSAGE_CHARS], evidence.similarity)



class ContractReviewRagRetriever:
    """검색기 구현을 계약서 검토 그래프의 근거 인터페이스로 맞춘다."""

    DEFAULT_EVIDENCE_TOP_K = 3
    DEFAULT_MIN_SIMILARITY = 0.35
    # 판례는 법령과 섞지 않고 종류별로 따로 고르며 최소 유사도는 두지 않는다(2026-11-12 팀 결정).
    # 이전의 0.2는 검증 자료 없이 임시로 정한 값이었다.
    DEFAULT_PRECEDENT_EVIDENCE_TOP_K = 2
    DEFAULT_PRECEDENT_MIN_SIMILARITY: float | None = None

    def __init__(
        self,
        *,
        statute_retriever: StatuteRetriever | None = None,
        precedent_retriever: PrecedentRetriever | None = None,
        statute_top_k: int = 3,
        precedent_top_k: int = 3,
        evidence_top_k: int = DEFAULT_EVIDENCE_TOP_K,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
        precedent_evidence_top_k: int = DEFAULT_PRECEDENT_EVIDENCE_TOP_K,
        precedent_min_similarity: float | None = DEFAULT_PRECEDENT_MIN_SIMILARITY,
        statute_reranker: Any | None = None,
        precedent_reranker: Any | None = None,
    ) -> None:
        if min(
            statute_top_k,
            precedent_top_k,
            evidence_top_k,
            precedent_evidence_top_k,
        ) < 1:
            raise ValueError("검색 결과 수는 1 이상이어야 합니다.")
        if not -1 <= min_similarity <= 1 or (
            precedent_min_similarity is not None and not -1 <= precedent_min_similarity <= 1
        ):
            raise ValueError("최소 유사도는 -1 이상 1 이하여야 합니다.")
        self._statute_retriever = statute_retriever
        self._precedent_retriever = precedent_retriever
        self._statute_top_k = statute_top_k
        self._precedent_top_k = precedent_top_k
        self._evidence_top_k = evidence_top_k
        self._min_similarity = min_similarity
        self._precedent_evidence_top_k = precedent_evidence_top_k
        self._precedent_min_similarity = precedent_min_similarity
        self._statute_reranker = statute_reranker
        self._precedent_reranker = precedent_reranker
        self._lock = RLock()

    def retrieve(
        self,
        queries: list[ContractReviewRetrievalQuery],
    ) -> list[ContractReviewEvidence]:
        """질의별 법령·판례 출처를 보존한 근거 목록을 반환한다."""

        with self._lock:
            evidence: list[ContractReviewEvidence] = []
            for query in queries:
                candidates: list[ContractReviewEvidence] = []
                if self._statute_retriever is not None:
                    candidates.extend(self._retrieve_statutes(query))
                if self._precedent_retriever is not None:
                    candidates.extend(self._retrieve_precedents(query))
                evidence.extend(self._select_relevant_evidence(candidates, query.query))
            return evidence

    def _select_relevant_evidence(
        self,
        candidates: list[ContractReviewEvidence],
        query_text: str | None = None,
    ) -> list[ContractReviewEvidence]:
        """법령과 판례를 종류별 기준·개수로 골라 항목별로 보존한다.

        리랭커가 있으면(법령·판례 각각) 기준을 넘은 후보를 질문과 함께 다시 정렬해 상위를 고른다.
        """

        def relevant(source_type: str, minimum: float | None) -> list[ContractReviewEvidence]:
            return sorted(
                (
                    candidate
                    for candidate in candidates
                    if candidate.source_type == source_type
                    and (minimum is None or candidate.similarity >= minimum)
                ),
                key=lambda candidate: (-candidate.similarity, candidate.rank, candidate.source_id),
            )

        statutes = relevant("statute", self._min_similarity)
        statutes = self._rerank(
            self._statute_reranker, statutes, query_text, "법령 리랭커 실패, dense 유사도 순서를 사용합니다."
        )[: self._evidence_top_k]
        precedents = relevant("precedent", self._precedent_min_similarity)
        precedents = self._rerank(
            self._precedent_reranker, precedents, query_text, "판례 리랭커 실패, dense 유사도 순서를 사용합니다."
        )[: self._precedent_evidence_top_k]
        return statutes + precedents

    @staticmethod
    def _rerank(
        reranker: Any | None,
        evidence: list[ContractReviewEvidence],
        query_text: str | None,
        failure_message: str,
    ) -> list[ContractReviewEvidence]:
        """리랭커로 후보를 재정렬한다. 실패하면 dense 유사도 순서를 그대로 쓴다."""

        if reranker is None or not query_text or len(evidence) < 2:
            return evidence
        by_id = {item.source_id: item for item in evidence}
        try:
            ranked = reranker.rerank(
                query_text,
                [_rerank_view(item) for item in evidence],
                top_k=len(evidence),
            )
            return [by_id[view.source_id] for view in ranked]
        except Exception:  # noqa: BLE001 - 리랭커 장애로 검토·생성이 멈추지 않게 한다.
            logger.warning(failure_message, exc_info=True)
            return evidence

    def _retrieve_statutes(
        self,
        query: ContractReviewRetrievalQuery,
    ) -> list[ContractReviewEvidence]:
        assert self._statute_retriever is not None
        # 계약서 원문 자체가 등록임대 관련이면(드묾) 그 법령군도 그대로 둔다.
        allow_registered_rental_business_law = bool(
            _REGISTERED_RENTAL_BUSINESS_MARKER.search(query.query)
        )
        return [
            ContractReviewEvidence(
                item_id=query.item_id,
                source_type="statute",
                source_id=result.chunk_id,
                title=" ".join(
                    part
                    for part in (result.law_name, result.article_label)
                    if part
                )
                or result.chunk_id,
                text=result.text,
                rank=result.rank,
                similarity=result.similarity,
                metadata={
                    "law_id": result.law_id,
                    "law_name": result.law_name,
                    "article_label": result.article_label,
                    "article_title": result.article_title,
                    "effective_date": result.effective_date,
                    "source_node_id": result.source_node_id,
                },
            )
            for result in self._statute_retriever.search(
                query.query,
                top_k=self._statute_top_k,
            )
            if allow_registered_rental_business_law
            or not _REGISTERED_RENTAL_BUSINESS_LAW.search(result.law_name or "")
        ]

    def _retrieve_precedents(
        self,
        query: ContractReviewRetrievalQuery,
    ) -> list[ContractReviewEvidence]:
        assert self._precedent_retriever is not None
        results = self._precedent_retriever.search(
            query.query,
            top_k=self._precedent_top_k,
        )
        evidence = []
        for result in results:
            matched_chunk = result.matched_chunks[0] if result.matched_chunks else None
            evidence.append(
                ContractReviewEvidence(
                    item_id=query.item_id,
                    source_type="precedent",
                    source_id=result.precedent_id,
                    title=result.case_name or result.case_no or result.precedent_id,
                    text=result.text,
                    rank=result.rank,
                    similarity=result.similarity,
                    metadata={
                        "case_no": result.case_no,
                        "case_name": result.case_name,
                        "court_name": result.court_name,
                        "decision_date": result.decision_date,
                        "decision_year": result.decision_year,
                        "case_type": result.case_type,
                        "judgment_type": result.judgment_type,
                        "matched_section": (
                            matched_chunk.section if matched_chunk else None
                        ),
                        "matched_chunk_type": (
                            matched_chunk.chunk_type if matched_chunk else None
                        ),
                        "matched_section_chunk_index": (
                            matched_chunk.section_chunk_index
                            if matched_chunk
                            else None
                        ),
                    },
                )
            )
        return evidence


class SharedBgeM3ModelLoader:
    """법령과 판례 검색기가 같은 BGE-M3 인스턴스를 재사용하게 한다."""

    def __init__(self, loader: Callable[[Any, str], Any]) -> None:
        self._loader = loader
        self._model: Any | None = None
        self._lock = RLock()

    def __call__(self, model_cache: Any, device: str) -> Any:
        with self._lock:
            if self._model is None:
                self._model = self._loader(model_cache, device)
            return self._model


_SHARED_BGE_MODEL_LOADER = SharedBgeM3ModelLoader(load_bge_model)


DEFAULT_RERANKER_NAME = "kure-reranker-nano"
DEFAULT_RERANKER_CANDIDATES = 10  # 리랭커에 넘기는 dense 후보 수(2026-11-12 시험: 후보 10개로 Recall@3 +15%p)


def _create_statute_reranker_from_env() -> CrossEncoderReranker | None:
    """법령 리랭커(KURE-Reranker-nano)를 기본으로 켜되, 끄거나 모델이 없으면 만들지 않는다."""

    enabled = os.getenv("RAG_STATUTE_RERANKER_ENABLED", "true").lower()
    if enabled not in {"1", "true", "yes"}:
        return None
    configured = os.getenv("RAG_STATUTE_RERANKER_DIR", "").strip()
    if configured:
        model_path = Path(configured).expanduser()
    else:
        local_data = Path(
            os.getenv("ALPHALAWVA_LOCAL_DATA_DIR", Path(__file__).resolve().parents[2] / "local_data")
        )
        model_path = local_data / "models" / "rerankers" / DEFAULT_RERANKER_NAME
    if not model_path.is_dir():
        logger.warning("법령 리랭커 모델이 없어 dense 순서를 씁니다: %s", model_path)
        return None
    return CrossEncoderReranker(
        model_path=model_path,
        max_length=int(os.getenv("RAG_STATUTE_RERANKER_MAX_LENGTH", "512")),
        batch_size=int(os.getenv("RAG_STATUTE_RERANKER_BATCH_SIZE", "16")),
        device=os.getenv("RAG_STATUTE_RERANKER_DEVICE", "auto").strip() or "auto",
        model_loader=SHARED_CROSS_ENCODER_MODEL_LOADER,
    )


def _create_rag_retriever_from_env(
    enabled_variable: str,
    *,
    precedent_evidence_top_k: int,
) -> ContractReviewRagRetriever | None:
    """명시적으로 활성화한 기능에서만 지연 로딩 RAG 검색기를 만든다."""

    enabled = os.getenv(enabled_variable, "false").lower()
    if enabled not in {"1", "true", "yes"}:
        return None

    # 법령·판례 모두 같은 KURE-Reranker-nano로 확정돼(2026-11-14) 리랭커 인스턴스 하나를 공유한다.
    reranker = _create_statute_reranker_from_env()
    candidates = int(
        os.getenv("RAG_STATUTE_RERANKER_CANDIDATES", str(DEFAULT_RERANKER_CANDIDATES))
    )
    widened = max(candidates, 3) if reranker is not None else 3
    precedent_retriever = create_eligibility_filtered_precedent_retriever_from_env(
        BgeM3PrecedentRetriever(model_loader=_SHARED_BGE_MODEL_LOADER)
    )
    return ContractReviewRagRetriever(
        statute_retriever=BgeM3StatuteRetriever(model_loader=_SHARED_BGE_MODEL_LOADER),
        precedent_retriever=precedent_retriever,
        statute_top_k=widened,
        precedent_top_k=widened,
        precedent_evidence_top_k=precedent_evidence_top_k,
        statute_reranker=reranker,
        precedent_reranker=reranker,
    )


def create_contract_review_rag_retriever_from_env() -> ContractReviewRagRetriever | None:
    """계약서 검토용 RAG 검색기를 환경 설정에 따라 만든다."""

    return _create_rag_retriever_from_env(
        "CONTRACT_REVIEW_RAG_ENABLED",
        precedent_evidence_top_k=ContractReviewRagRetriever.DEFAULT_PRECEDENT_EVIDENCE_TOP_K,
    )


def create_special_clause_rag_retriever_from_env() -> ContractReviewRagRetriever | None:
    """특약 생성용 RAG 검색기를 환경 설정에 따라 만든다."""

    return _create_rag_retriever_from_env(
        "SPECIAL_CLAUSE_RAG_ENABLED",
        precedent_evidence_top_k=3,
    )
