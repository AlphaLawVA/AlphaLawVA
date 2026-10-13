# precedent_search_sessions.py
"""
Description: 판례 검색 질문과 청크 결과를 검색 세션 단위로 임시 보관한다.
TTL과 최대 세션 수를 적용하여 상세 조회에 필요한 검색 맥락을 제한적으로 유지한다.
Author: choeminju
Date: 2026-10-14
Before:
    - 검색 목록 이후 상세 조회에서 원 질문과 적중 청크를 복원할 방법이 없는 상태.
After:
    - search_id로 유효한 검색 세션과 판례별 검색 결과를 조회할 수 있음.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from threading import RLock
from uuid import UUID, uuid4

from ml.rag.precedent_retriever import PrecedentSearchResult


DEFAULT_SESSION_TTL_SECONDS = 30 * 60
DEFAULT_MAX_SESSIONS = 500


class SearchSessionError(Exception):
    """Base error for precedent search sessions."""


class SearchSessionNotFoundError(SearchSessionError):
    """Raised when a search session is missing or expired."""


class SearchResultNotFoundError(SearchSessionError):
    """Raised when a precedent does not belong to a search session."""


@dataclass(frozen=True)
class PrecedentSearchSession:
    search_id: UUID
    query: str
    results: tuple[PrecedentSearchResult, ...]
    created_at: float
    expires_at: float


class PrecedentSearchSessionStore:
    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds는 0보다 커야 합니다.")
        if max_sessions < 1:
            raise ValueError("max_sessions는 1 이상이어야 합니다.")
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._clock = clock
        self._sessions: dict[UUID, PrecedentSearchSession] = {}
        self._lock = RLock()

    def create(
        self,
        query: str,
        results: Sequence[PrecedentSearchResult],
    ) -> PrecedentSearchSession:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("검색 질문은 비어 있을 수 없습니다.")

        now = self._clock()
        session = PrecedentSearchSession(
            search_id=uuid4(),
            query=normalized_query,
            results=tuple(results),
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        with self._lock:
            self._prune_expired(now)
            while len(self._sessions) >= self.max_sessions:
                oldest_id = next(iter(self._sessions))
                del self._sessions[oldest_id]
            self._sessions[session.search_id] = session
        return session

    def get(self, search_id: UUID) -> PrecedentSearchSession:
        now = self._clock()
        with self._lock:
            self._prune_expired(now)
            session = self._sessions.get(search_id)
        if session is None:
            raise SearchSessionNotFoundError(
                "검색 기록이 없거나 만료되었습니다. 다시 검색해 주세요."
            )
        return session

    def get_result(
        self,
        search_id: UUID,
        precedent_id: str,
    ) -> tuple[PrecedentSearchSession, PrecedentSearchResult]:
        session = self.get(search_id)
        result = next(
            (
                item
                for item in session.results
                if item.precedent_id == precedent_id
            ),
            None,
        )
        if result is None:
            raise SearchResultNotFoundError(
                "해당 검색 결과에 포함되지 않은 판례입니다."
            )
        return session, result

    def _prune_expired(self, now: float) -> None:
        expired_ids = [
            search_id
            for search_id, session in self._sessions.items()
            if session.expires_at <= now
        ]
        for search_id in expired_ids:
            del self._sessions[search_id]
