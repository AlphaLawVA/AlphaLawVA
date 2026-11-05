# special_clause_service.py
"""
Description: 자연어 특약 요청을 근거 검색과 사용자 확인용 초안 생성으로 연결한다.
근거 또는 생성기가 없으면 안전하게 생성 대기 상태만 반환한다.
Author: ooheunsu
Date: 2026-10-31
Before:
    - 특약 생성 요청을 생성 대기 상태로만 반환하는 초기 서비스가 있는 상태.
After:
    - RAG 근거와 Gemini 구조화 출력이 연결된 특약 생성 응답을 반환할 수 있음.
"""

from __future__ import annotations

from typing import Callable
from uuid import uuid4

from api.models.generation_results import GenerationResultKind
from api.models.contracts import (
    EvidenceStatus,
    GenerationStatus,
    SpecialClauseDraftItem,
    SpecialClauseDraftRequest,
    SpecialClauseDraftResponse,
)
from ml.agent.contract_review_graph import ContractReviewEvidenceRetriever
from ml.agent.special_clause_graph import SpecialClauseGenerator, SpecialClauseGraph
from ml.agent.special_clause_scope import (
    OUT_OF_SCOPE_MESSAGE,
    TOO_SHORT_QUESTION,
    ScopeDecision,
)


def _api_status(generation_status: str) -> str:
    """생성기 내부 상태를 API 상태 이름으로 바꾼다(정보 부족은 needs_information으로 통일)."""

    return "needs_information" if generation_status == "needs_input" else generation_status


class SpecialClauseService:
    """자연어 특약 요청을 근거 기반 사용자 확인용 결과로 구성한다."""

    def __init__(
        self,
        *,
        graph: SpecialClauseGraph | None = None,
        evidence_retriever: ContractReviewEvidenceRetriever | None = None,
        generator: SpecialClauseGenerator | None = None,
    ) -> None:
        self._graph = graph or SpecialClauseGraph()
        self._evidence_retriever = evidence_retriever
        self._generator = generator
        self._result_recorder: Callable[..., None] | None = None

    def set_result_recorder(self, recorder: Callable[..., None] | None) -> None:
        """생성 결과를 사용자 저장 요청에 대비해 잠시 보관하는 함수를 연결한다."""

        self._result_recorder = recorder

    def create_draft(
        self,
        payload: SpecialClauseDraftRequest,
    ) -> SpecialClauseDraftResponse:
        evidence_status = EvidenceStatus.PENDING_RETRIEVAL
        generation_status = GenerationStatus.PENDING
        try:
            result = self._graph.prepare(
                payload.requests,
                evidence_retriever=self._evidence_retriever,
                generator=self._generator,
            )
        except (FileNotFoundError, RuntimeError, ValueError):
            result = self._graph.prepare(payload.requests)
            if self._evidence_retriever is not None:
                evidence_status = EvidenceStatus.UNAVAILABLE
            if self._generator is not None:
                generation_status = GenerationStatus.UNAVAILABLE
        else:
            if self._evidence_retriever is not None:
                evidence_status = EvidenceStatus.RETRIEVED
            if self._generator is not None and result.evidence:
                generation_status = (
                    GenerationStatus.UNAVAILABLE
                    if result.generation_unavailable
                    else GenerationStatus.GENERATED
                )

        generations = {
            generation.item_id: generation for generation in result.generations
        }
        items = []
        for index, request_text in enumerate(payload.requests, start=1):
            decision = result.scope_decisions.get(f"request-{index}")
            if decision == ScopeDecision.OUT_OF_SCOPE.value:
                items.append(
                    SpecialClauseDraftItem(
                        request_index=index,
                        request_text=request_text,
                        status="out_of_scope",
                        reason=OUT_OF_SCOPE_MESSAGE,
                    )
                )
                continue
            if decision == ScopeDecision.NEEDS_INFORMATION.value:
                items.append(
                    SpecialClauseDraftItem(
                        request_index=index,
                        request_text=request_text,
                        status="needs_information",
                        needs_input=[TOO_SHORT_QUESTION],
                    )
                )
                continue
            generation = generations.get(f"request-{index}")
            items.append(
                SpecialClauseDraftItem(
                    request_index=index,
                    request_text=request_text,
                    status=(
                        _api_status(generation.status)
                        if generation
                        else "pending_generation"
                    ),
                    draft_text=generation.draft_text if generation else None,
                    title=generation.title if generation else None,
                    reason=generation.reason if generation else None,
                    strategy=generation.strategy if generation else None,
                    needs_input=generation.needs_input if generation else [],
                    evidence_source_ids=(
                        generation.evidence_source_ids if generation else []
                    ),
                )
            )
        all_terminal = all(item.status != "pending_generation" for item in items)
        response = SpecialClauseDraftResponse(
            draft_id=uuid4(),
            status=(
                "completed"
                if generation_status == GenerationStatus.GENERATED or all_terminal
                else "pending_generation"
            ),
            items=items,
            evidence=[
                {
                    "item_id": item.item_id,
                    "source_type": item.source_type,
                    "source_id": item.source_id,
                    "title": item.title,
                    "text": item.text,
                    "rank": item.rank,
                    "similarity": item.similarity,
                    "metadata": item.metadata,
                }
                for item in result.evidence
            ],
            evidence_status=evidence_status,
            generation_status=generation_status,
            message=(
                "부동산 계약 특약과 관련된 요청이 아니어서 초안을 만들지 않았습니다."
                if items and all(item.status == "out_of_scope" for item in items)
                else self._message(evidence_status, generation_status)
            ),
            disclaimer=(
                "이 결과는 법률 자문이나 최종 계약서가 아닙니다. 특약 초안과 "
                "사실관계·근거 원문을 당사자가 직접 확인해야 합니다."
            ),
        )
        self._record_result(response)
        return response

    def _record_result(self, response: SpecialClauseDraftResponse) -> None:
        if self._result_recorder is None:
            return
        savable = any(
            item.status in {"draft_ready", "needs_information"} for item in response.items
        )
        first_request = next(
            (
                item.request_text
                for item in response.items
                if item.status != "out_of_scope"
            ),
            response.items[0].request_text,
        )
        self._result_recorder(
            kind=GenerationResultKind.SPECIAL_CLAUSE,
            source_id=response.draft_id,
            title=f"특약 초안 · {first_request[:40]}",
            payload=response.model_dump(mode="json"),
            saveable=savable,
        )

    @staticmethod
    def _message(
        evidence_status: EvidenceStatus,
        generation_status: GenerationStatus,
    ) -> str:
        if generation_status == GenerationStatus.GENERATED:
            return "근거가 연결된 특약 초안 또는 추가 확인 항목을 구성했습니다."
        if generation_status == GenerationStatus.UNAVAILABLE:
            return "검색 근거는 찾았지만 특약 초안 생성은 현재 사용할 수 없습니다."
        if evidence_status == EvidenceStatus.RETRIEVED:
            return "검색 근거를 찾았습니다. 특약 초안 생성은 아직 연결되지 않았습니다."
        if evidence_status == EvidenceStatus.UNAVAILABLE:
            return "검색 근거를 불러올 수 없어 특약 초안 생성을 진행하지 않았습니다."
        return "특약 생성 요청을 접수했습니다. 검색과 생성 모델 연결 전에는 초안을 반환하지 않습니다."
