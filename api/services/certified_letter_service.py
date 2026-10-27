# certified_letter_service.py
"""
Description: 내용증명 공개 API 요청을 내부 그래프 작업으로 접수·실행·조회한다.
API 모델과 ML 도메인 값을 변환하고 실패를 안전한 작업 상태로 기록한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 내용증명 LangGraph와 비동기 API 작업을 연결하는 애플리케이션 서비스가 없음.
After:
    - 중복 방지 가능한 생성 접수와 상태 조회를 제공할 수 있음.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from threading import RLock
from typing import Any, Callable, Protocol
from uuid import UUID, uuid4

from api.models.certified_letters import (
    CertifiedLetterDocument,
    CertifiedLetterDraftAccepted,
    CertifiedLetterDraftJobResponse,
    CertifiedLetterDraftRequest,
    CertifiedLetterDraftResult,
    CertifiedLetterJobError,
    CertifiedLetterOutcome,
    CertifiedLetterParty,
    FollowUpAction,
    PrecedentFullTextSection,
    PrecedentMatchedChunk,
    SimilarPrecedent,
    StatuteEvidence,
)
from api.services.certified_letter_jobs import (
    CertifiedLetterIdempotencyConflictError,
    CertifiedLetterJob,
    CertifiedLetterJobNotFoundError,
    CertifiedLetterJobStore,
    CertifiedLetterJobTransitionError,
)
from api.services.precedent_case_repository import (
    PrecedentCaseRepository,
    PrecedentCaseRepositoryError,
)
from ml.agent.certified_letter.state import (
    CertifiedLetterGraphState,
    CertifiedLetterRequestData,
    ContractPartyRole,
    ContractData,
    PartyData,
    PaymentData,
    PeriodData,
    PropertyData,
)
from ml.agent.certified_letter.validators import extract_statute_citations


logger = logging.getLogger(__name__)

PRECEDENT_FULL_TEXT_FIELDS = (
    ("order", "주문", "주문"),
    ("claim", "청구취지", "청구취지"),
    ("original_judgment", "원심판결", "원심판결"),
    ("reason", "이유", "이유"),
)


class CertifiedLetterGenerationUnavailableError(Exception):
    """생성 그래프가 구성되지 않아 새 작업을 받을 수 없는 경우."""


class CertifiedLetterGraphRunner(Protocol):
    def invoke(
        self,
        input: CertifiedLetterGraphState,
    ) -> CertifiedLetterGraphState: ...


@dataclass(frozen=True)
class CertifiedLetterSubmission:
    response: CertifiedLetterDraftAccepted
    is_new: bool


class CertifiedLetterService:
    def __init__(
        self,
        *,
        graph: CertifiedLetterGraphRunner | None = None,
        jobs: CertifiedLetterJobStore | None = None,
        precedent_repository: PrecedentCaseRepository | None = None,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self.graph = graph
        self.jobs = jobs or CertifiedLetterJobStore()
        self.precedent_repository = precedent_repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.uuid_factory = uuid_factory or uuid4
        self._requests: dict[UUID, CertifiedLetterRequestData] = {}
        self._request_lock = RLock()

    def submit(
        self,
        request: CertifiedLetterDraftRequest,
        *,
        idempotency_key: str | None = None,
    ) -> CertifiedLetterSubmission:
        if self.graph is None:
            raise CertifiedLetterGenerationUnavailableError(
                "내용증명 생성 모델이 구성되지 않았습니다."
            )
        normalized_key = _normalize_idempotency_key(idempotency_key)
        fingerprint = _request_fingerprint(request)
        now = self.clock()
        job, is_new = self.jobs.create(
            draft_id=self.uuid_factory(),
            request_fingerprint=fingerprint,
            idempotency_key=normalized_key,
            now=now,
        )
        if is_new:
            with self._request_lock:
                self._requests[job.draft_id] = _to_internal_request(request)
        return CertifiedLetterSubmission(
            response=CertifiedLetterDraftAccepted(
                draft_id=job.draft_id,
                status=job.status,
                created_at=job.created_at,
            ),
            is_new=is_new,
        )

    def process(self, draft_id: UUID) -> CertifiedLetterGraphState | None:
        if self.graph is None:
            raise CertifiedLetterGenerationUnavailableError(
                "내용증명 생성 모델이 구성되지 않았습니다."
            )
        try:
            self.jobs.mark_processing(draft_id, now=self.clock())
        except (CertifiedLetterJobNotFoundError, CertifiedLetterJobTransitionError):
            self._discard_request(draft_id)
            logger.warning(
                "내용증명 작업 실행 건너뜀: draft_id=%s reason=not_queued",
                draft_id,
            )
            return None

        try:
            request = self._take_request(draft_id)
            state = self.graph.invoke({"request": request})
            if failure := state.get("failure"):
                logger.warning(
                    "내용증명 검증 실패: draft_id=%s issue_codes=%s repair_attempts=%s",
                    draft_id,
                    [item.code for item in state.get("validation_issues", ())],
                    state.get("repair_attempts", 0),
                )
                self.jobs.mark_failed(
                    draft_id,
                    error=CertifiedLetterJobError(
                        code=failure.code,
                        message=failure.message,
                    ),
                    now=self.clock(),
                )
                return state
            outcome = CertifiedLetterOutcome(state["outcome"])
            result = (
                _to_api_result(
                    state,
                    precedent_repository=self.precedent_repository,
                )
                if outcome is CertifiedLetterOutcome.DRAFT_READY
                else None
            )
            self.jobs.mark_completed(
                draft_id,
                outcome=outcome,
                result=result,
                missing_information=state.get("missing_information", ()),
                message=state.get("message"),
                now=self.clock(),
            )
            return state
        except Exception as error:
            logger.exception(
                "내용증명 생성 작업 실패: draft_id=%s error_type=%s",
                draft_id,
                type(error).__name__,
            )
            self.jobs.mark_failed(
                draft_id,
                error=CertifiedLetterJobError(
                    code="certified_letter_generation_failed",
                    message="내용증명 초안을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                ),
                now=self.clock(),
            )
            return None

    def get(self, draft_id: UUID) -> CertifiedLetterDraftJobResponse:
        return _to_job_response(self.jobs.get(draft_id, now=self.clock()))

    def _take_request(self, draft_id: UUID) -> CertifiedLetterRequestData:
        with self._request_lock:
            return self._requests.pop(draft_id)

    def _discard_request(self, draft_id: UUID) -> None:
        with self._request_lock:
            self._requests.pop(draft_id, None)


def _to_internal_request(
    request: CertifiedLetterDraftRequest,
) -> CertifiedLetterRequestData:
    contract = request.contract
    return CertifiedLetterRequestData(
        sender_role=ContractPartyRole(request.sender_role.value),
        purpose=request.purpose,
        facts=request.facts,
        requested_deadline=request.requested_deadline,
        contract=ContractData(
            contract_type=contract.contract_type.value,
            property=PropertyData(
                address=contract.property.address,
                detail_address=contract.property.detail_address,
                building_type=contract.property.building_type,
            ),
            period=PeriodData(
                start_date=contract.period.start_date,
                end_date=contract.period.end_date,
            ),
            payment=PaymentData(
                deposit=contract.payment.deposit,
                monthly_rent=contract.payment.monthly_rent,
            ),
            lessor=_to_party(contract.lessor),
            lessee=_to_party(contract.lessee),
        ),
    )


def _to_party(value) -> PartyData:
    return PartyData(
        name=value.name,
        address=value.address,
        detail_address=value.detail_address,
        phone_number=value.phone_number,
    )


def _to_api_result(
    state: CertifiedLetterGraphState,
    *,
    precedent_repository: PrecedentCaseRepository | None = None,
) -> CertifiedLetterDraftResult:
    draft = state["draft"]
    normalized_request = state["normalized_request"]
    selected_statutes = state.get("selected_statutes", ())
    cited_statutes = extract_statute_citations(draft.body)
    no_statute_warning = (
        "본문에 직접 인용된 관련 법령 없이 작성된 초안입니다. "
        "발송 전 법적 근거를 별도로 확인해 주세요."
        if not cited_statutes
        else ""
    )
    warnings = _unique_strings(
        (
            *draft.warnings,
            *normalized_request.warnings,
            no_statute_warning,
            "이 문서는 법률 검토 전 초안입니다.",
        )
    )
    statutes = [
        StatuteEvidence(
            statute_id=item.statute_id,
            law_name=item.law_name,
            article_label=item.article_label,
            article_title=item.article_title,
            content=item.text,
            explanation=(
                item.explanation
                or "내용증명 본문의 법적 근거로 검색·선정된 조문입니다."
            ),
            effective_date=_parse_date(item.effective_date),
            source_node_id=item.source_node_id,
            source_url=item.source_url,
        )
        for item in selected_statutes
        if (
            _normalize_law_name(item.law_name),
            _normalize_article(item.article_label),
        )
        in cited_statutes
    ]
    precedents = [
        _to_similar_precedent(item, precedent_repository=precedent_repository)
        for item in state.get("selected_precedents", ())
    ]
    return CertifiedLetterDraftResult(
        document=CertifiedLetterDocument(
            title=draft.title,
            sender=CertifiedLetterParty(
                name=draft.sender.name,
                address=draft.sender.address,
                detail_address=draft.sender.detail_address,
            ),
            recipient=CertifiedLetterParty(
                name=draft.recipient.name,
                address=draft.recipient.address,
                detail_address=draft.recipient.detail_address,
            ),
            body=draft.body,
        ),
        strategy_summary=draft.strategy_summary,
        follow_up_actions=[
            FollowUpAction(order=index, action=action)
            for index, action in enumerate(draft.follow_up_actions, start=1)
        ],
        statute_evidence=statutes,
        similar_precedents=precedents,
        warnings=warnings,
    )


def _to_similar_precedent(
    item,
    *,
    precedent_repository: PrecedentCaseRepository | None,
) -> SimilarPrecedent:
    case_data = _load_precedent_presentation(
        precedent_repository,
        item.precedent_id,
    )
    precedent_summary = _optional_text(case_data.get("생성요약"))
    easy_summary = _optional_text(case_data.get("쉬운요약"))
    return SimilarPrecedent(
        precedent_id=item.precedent_id,
        case_number=_optional_text(case_data.get("사건번호")) or item.case_number,
        case_name=_optional_text(case_data.get("사건명")) or item.case_name,
        court_name=_optional_text(case_data.get("법원명")) or item.court_name,
        decision_date=(
            _parse_date(_optional_text(case_data.get("선고일자")))
            or _parse_date(item.decision_date)
        ),
        summary=easy_summary or precedent_summary or item.summary or item.text,
        precedent_summary=precedent_summary,
        easy_summary=easy_summary,
        matched_reason=item.matched_reason,
        matched_chunks=[
            PrecedentMatchedChunk(
                rank=chunk.rank,
                chunk_id=chunk.chunk_id,
                section=chunk.section,
                chunk_type=chunk.chunk_type,
                section_chunk_index=chunk.section_chunk_index,
                similarity_score=chunk.similarity,
                text=chunk.text,
            )
            for chunk in item.matched_chunks
        ],
        full_text_sections=_full_text_sections(case_data),
        source_url=item.source_url
        or (
            "https://law.go.kr/LSW/precInfoP.do?mode=0&precSeq="
            f"{item.precedent_id}"
        ),
    )


def _load_precedent_presentation(
    repository: PrecedentCaseRepository | None,
    precedent_id: str,
) -> dict[str, Any]:
    if repository is None:
        return {}
    try:
        return repository.get(precedent_id)
    except PrecedentCaseRepositoryError:
        logger.info(
            "내용증명 관련 판례 원본을 찾지 못함: precedent_id=%s",
            precedent_id,
        )
        return {}


def _full_text_sections(
    case_data: dict[str, Any],
) -> list[PrecedentFullTextSection]:
    sections = []
    for key, title, source_field in PRECEDENT_FULL_TEXT_FIELDS:
        content = _optional_text(case_data.get(source_field))
        if content is not None:
            sections.append(
                PrecedentFullTextSection(
                    position=len(sections) + 1,
                    key=key,
                    title=title,
                    content=content,
                )
            )
    return sections


def _to_job_response(job: CertifiedLetterJob) -> CertifiedLetterDraftJobResponse:
    return CertifiedLetterDraftJobResponse(
        draft_id=job.draft_id,
        status=job.status,
        outcome=job.outcome,
        result=job.result,
        missing_information=list(job.missing_information),
        message=job.message,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


def _request_fingerprint(request: CertifiedLetterDraftRequest) -> str:
    serialized = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _normalize_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    match = re.fullmatch(
        r"\s*(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*(?:일)?\s*[.]?\s*",
        value,
    )
    if match is None:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def _unique_strings(values) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_law_name(value: str) -> str:
    return "".join(value.split()).replace("ㆍ", "·")


def _normalize_article(value: str) -> str:
    compact = "".join(value.split())
    match = re.search(r"제?(\d+)조(?:의(\d+))?", compact)
    if match is None:
        return compact
    article, subarticle = match.groups()
    return f"제{article}조" + (f"의{subarticle}" if subarticle else "")


__all__ = [
    "CertifiedLetterGenerationUnavailableError",
    "CertifiedLetterIdempotencyConflictError",
    "CertifiedLetterJobNotFoundError",
    "CertifiedLetterService",
    "CertifiedLetterSubmission",
]
