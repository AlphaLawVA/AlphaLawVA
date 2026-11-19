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

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID, uuid4

from api.models.generation_results import GenerationResultKind
from api.models.contracts import (
    ContractJobError,
    EvidenceStatus,
    GenerationStatus,
    SpecialClauseDraftAccepted,
    SpecialClauseDraftItem,
    SpecialClauseDraftJobResponse,
    SpecialClauseDraftRequest,
    SpecialClauseDraftResponse,
)
from api.services.job_store import build_request_fingerprint
from api.services.special_clause_jobs import (
    SpecialClauseJob,
    SpecialClauseJobNotFoundError,
    SpecialClauseJobStore,
    SpecialClauseJobTransitionError,
)
from api.services.precedent_case_repository import PrecedentCaseRepository
from api.services.precedent_evidence_enricher import enrich_precedent_evidence_metadata
from ml.agent.contract_review_graph import ContractReviewEvidenceRetriever
from ml.agent.special_clause_graph import (
    SpecialClauseGenerator,
    SpecialClauseGraph,
    SpecialClauseScopeClassifier,
)
from ml.agent.stage_tracking import StageCallback
from ml.agent.special_clause_scope import (
    OUT_OF_SCOPE_MESSAGE,
    TOO_SHORT_QUESTION,
    ScopeDecision,
)


logger = logging.getLogger(__name__)


def _api_status(generation_status: str) -> str:
    """생성기 내부 상태를 API 상태 이름으로 바꾼다(정보 부족은 needs_information으로 통일)."""

    return "needs_information" if generation_status == "needs_input" else generation_status


@dataclass(frozen=True)
class SpecialClauseSubmission:
    response: SpecialClauseDraftAccepted
    is_new: bool


class SpecialClauseService:
    """자연어 특약 요청을 근거 기반 사용자 확인용 결과로 구성한다."""

    def __init__(
        self,
        *,
        graph: SpecialClauseGraph | None = None,
        evidence_retriever: ContractReviewEvidenceRetriever | None = None,
        generator: SpecialClauseGenerator | None = None,
        scope_classifier: SpecialClauseScopeClassifier | None = None,
        precedent_repository: PrecedentCaseRepository | None = None,
        jobs: SpecialClauseJobStore | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._jobs = jobs or SpecialClauseJobStore()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._graph = graph or SpecialClauseGraph()
        self._evidence_retriever = evidence_retriever
        self._generator = generator
        self._scope_classifier = scope_classifier
        self._precedent_repository = precedent_repository or PrecedentCaseRepository()
        self._result_recorder: Callable[..., None] | None = None

    def set_result_recorder(self, recorder: Callable[..., None] | None) -> None:
        """생성 결과를 사용자 저장 요청에 대비해 잠시 보관하는 함수를 연결한다."""

        self._result_recorder = recorder

    def submit(
        self,
        payload: SpecialClauseDraftRequest,
        *,
        idempotency_key: str | None = None,
    ) -> SpecialClauseSubmission:
        """요청을 작업으로 접수한다. 실제 생성은 process가 맡는다."""

        job, is_new = self._jobs.create(
            draft_id=uuid4(),
            request=payload,
            request_fingerprint=build_request_fingerprint(payload.model_dump(mode="json")),
            idempotency_key=idempotency_key,
            now=self._clock(),
        )
        return SpecialClauseSubmission(
            response=SpecialClauseDraftAccepted(
                draft_id=job.draft_id,
                status=job.status,
                created_at=job.created_at,
            ),
            is_new=is_new,
        )

    def process(self, draft_id: UUID) -> SpecialClauseDraftResponse | None:
        """접수된 작업을 실행해 완료 또는 실패로 기록한다(백그라운드에서 호출)."""

        try:
            job = self._jobs.mark_processing(draft_id, now=self._clock())
        except (SpecialClauseJobNotFoundError, SpecialClauseJobTransitionError):
            logger.warning("특약 생성 작업 실행 건너뜀: draft_id=%s reason=not_queued", draft_id)
            return None

        def on_stage(stage: str) -> None:
            try:
                self._jobs.set_stage(draft_id, stage, now=self._clock())
            except (SpecialClauseJobNotFoundError, SpecialClauseJobTransitionError):
                pass

        try:
            response = self.create_draft(job.request, draft_id=draft_id, on_stage=on_stage)
            self._jobs.mark_completed(draft_id, result=response, now=self._clock())
            return response
        except Exception as error:  # noqa: BLE001 - 어떤 실패든 작업을 failed로 닫아야 한다.
            logger.exception(
                "특약 생성 작업 실패: draft_id=%s error_type=%s",
                draft_id,
                type(error).__name__,
            )
            try:
                self._jobs.mark_failed(
                    draft_id,
                    error=ContractJobError(
                        code="special_clause_generation_failed",
                        message="특약 초안을 만들지 못했습니다. 잠시 후 다시 시도해 주세요.",
                    ),
                    now=self._clock(),
                )
            except (SpecialClauseJobNotFoundError, SpecialClauseJobTransitionError):
                pass
            return None

    def get(self, draft_id: UUID) -> SpecialClauseDraftJobResponse:
        return _to_job_response(self._jobs.get(draft_id, now=self._clock()))

    def create_draft(
        self,
        payload: SpecialClauseDraftRequest,
        *,
        draft_id: UUID | None = None,
        on_stage: StageCallback | None = None,
    ) -> SpecialClauseDraftResponse:
        evidence_status = EvidenceStatus.PENDING_RETRIEVAL
        generation_status = GenerationStatus.PENDING
        try:
            result = self._graph.prepare(
                payload.requests,
                evidence_retriever=self._evidence_retriever,
                generator=self._generator,
                scope_classifier=self._scope_classifier,
                on_stage=on_stage,
            )
        except (FileNotFoundError, RuntimeError, ValueError):
            # 근거 검색·생성 실패 원인이 응답에는 드러나지 않으므로 서버 로그에 남긴다(요청 본문은 남기지 않는다).
            logger.warning("특약 근거 검색 또는 생성 단계가 실패해 unavailable로 응답합니다.", exc_info=True)
            result = self._graph.prepare(
                payload.requests, scope_classifier=self._scope_classifier
            )
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
        # 법령은 초안이 실제로 사용한 근거만, 판례는 생성과 분리한 관련 판례를 반환한다.
        cited = {
            (f"request-{item.request_index}", source_id)
            for item in items
            for source_id in item.evidence_source_ids
        }
        returned_evidence = [
            entry
            for entry in result.evidence
            if generation_status != GenerationStatus.GENERATED
            or entry.source_type == "precedent"
            or (entry.item_id, entry.source_id) in cited
        ]
        response = SpecialClauseDraftResponse(
            draft_id=draft_id or uuid4(),
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
                    "metadata": enrich_precedent_evidence_metadata(
                        source_type=item.source_type,
                        source_id=item.source_id,
                        metadata=item.metadata,
                        repository=self._precedent_repository,
                    ),
                }
                for item in returned_evidence
            ],
            evidence_status=evidence_status,
            generation_status=generation_status,
            message=(
                "부동산 계약 특약과 관련된 요청이 아니어서 초안을 만들지 않았습니다."
                if items and all(item.status == "out_of_scope" for item in items)
                else self._message(evidence_status, generation_status, items)
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
        self._result_recorder(
            kind=GenerationResultKind.SPECIAL_CLAUSE,
            source_id=response.draft_id,
            title=f"특약 초안 {len(response.items)}건",
            payload=response.model_dump(mode="json"),
            saveable=savable,
        )

    @staticmethod
    def _message(
        evidence_status: EvidenceStatus,
        generation_status: GenerationStatus,
        items: list[SpecialClauseDraftItem] | None = None,
    ) -> str:
        if generation_status == GenerationStatus.GENERATED:
            drafts = [item for item in items or [] if item.status == "draft_ready"]
            without_evidence = [item for item in drafts if not item.evidence_source_ids]
            # 법령 근거가 없다는 사실은 결과 화면의 관련 법령 영역이 따로 알리므로 문구에 반복하지 않는다.
            if without_evidence:
                return "특약 초안 또는 추가 확인 항목을 구성했습니다."
            return "근거가 연결된 특약 초안 또는 추가 확인 항목을 구성했습니다."
        if generation_status == GenerationStatus.UNAVAILABLE:
            return "검색 근거는 찾았지만 특약 초안 생성은 현재 사용할 수 없습니다."
        if evidence_status == EvidenceStatus.RETRIEVED:
            return "검색 근거를 찾았습니다. 특약 초안 생성은 아직 연결되지 않았습니다."
        if evidence_status == EvidenceStatus.UNAVAILABLE:
            return "검색 근거를 불러올 수 없어 특약 초안 생성을 진행하지 않았습니다."
        return "특약 생성 요청을 접수했습니다. 검색과 생성 모델 연결 전에는 초안을 반환하지 않습니다."


def _to_job_response(job: SpecialClauseJob) -> SpecialClauseDraftJobResponse:
    return SpecialClauseDraftJobResponse(
        draft_id=job.draft_id,
        status=job.status,
        stage=job.stage,
        result=job.result,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )
