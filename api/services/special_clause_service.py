# special_clause_service.py
"""
Description: 특약 생성 자연어 요청을 검증 후 생성 대기 응답으로 변환한다.
LLM과 RAG가 연결되기 전에는 특약 문안이나 법률 근거를 생성하지 않는다.
Author: ooheunsu
Date: 2026-10-24
Before:
    - 자연어 특약 요청을 API 수준에서 수신하고 안전하게 상태를 반환할 서비스가 없는 상태.
After:
    - 특약 요청 목록을 생성 대기 상태로 반환하는 초기 서비스가 제공됨.
"""

from __future__ import annotations

from uuid import uuid4

from api.models.contracts import (
    EvidenceStatus,
    SpecialClauseDraftItem,
    SpecialClauseDraftRequest,
    SpecialClauseDraftResponse,
)


class SpecialClauseService:
    """RAG·LLM 연결 전 특약 생성 요청을 접수한다."""

    def create_draft(
        self,
        payload: SpecialClauseDraftRequest,
    ) -> SpecialClauseDraftResponse:
        items = [
            SpecialClauseDraftItem(
                request_index=index,
                request_text=request_text,
                status="pending_generation",
            )
            for index, request_text in enumerate(payload.requests, start=1)
        ]
        return SpecialClauseDraftResponse(
            draft_id=uuid4(),
            status="pending_generation",
            items=items,
            evidence_status=EvidenceStatus.PENDING_RETRIEVAL,
            message=(
                "특약 생성 요청을 접수했습니다. "
                "RAG와 생성 모델 연결 전에는 문안과 법률 근거를 반환하지 않습니다."
            ),
            disclaimer=(
                "생성될 특약은 초안이며, 계약 당사자는 최종 문안과 사실관계를 "
                "직접 확인해야 합니다."
            ),
        )
