# precedent_favorites.py
"""
Description: 검색 질문과 판례 결과를 유사성 설명 상태와 함께 즐겨찾기로 보관한다.
인증·영구 DB 연결 전 API 흐름을 검증할 수 있도록 프로세스 메모리 저장소를 제공한다.
Author: choeminju
Date: 2026-10-15
Before:
    - 검색 결과와 설명 상태는 검색 세션 안에서만 조회할 수 있는 상태.
After:
    - 즐겨찾기가 검색 맥락과 동일한 설명 상태를 참조하여 재사용할 수 있음.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from uuid import UUID, uuid4

from api.services.precedent_search_sessions import SearchResultInteractionState
from ml.rag.precedent_retriever import PrecedentSearchResult


class PrecedentFavoriteNotFoundError(Exception):
    """Raised when a favorite identifier is unknown."""


class PrecedentFavorite:
    def __init__(
        self,
        *,
        favorite_id: UUID,
        search_id: UUID,
        query: str,
        result: PrecedentSearchResult,
        interaction: SearchResultInteractionState,
        created_at: datetime,
    ) -> None:
        self.favorite_id = favorite_id
        self.search_id = search_id
        self.query = query
        self.result = result
        self.interaction = interaction
        self.created_at = created_at


class PrecedentFavoriteStore:
    def __init__(self) -> None:
        self._favorites: dict[UUID, PrecedentFavorite] = {}
        self._result_index: dict[tuple[UUID, str], UUID] = {}
        self._lock = RLock()

    def create(
        self,
        *,
        search_id: UUID,
        query: str,
        result: PrecedentSearchResult,
        interaction: SearchResultInteractionState,
    ) -> PrecedentFavorite:
        key = (search_id, result.precedent_id)
        with self._lock:
            existing_id = self._result_index.get(key)
            if existing_id is not None:
                return self._favorites[existing_id]

            favorite = PrecedentFavorite(
                favorite_id=uuid4(),
                search_id=search_id,
                query=query,
                result=result,
                interaction=interaction,
                created_at=datetime.now(timezone.utc),
            )
            self._favorites[favorite.favorite_id] = favorite
            self._result_index[key] = favorite.favorite_id
            return favorite

    def list(self) -> list[PrecedentFavorite]:
        with self._lock:
            return list(reversed(self._favorites.values()))

    def get(self, favorite_id: UUID) -> PrecedentFavorite:
        with self._lock:
            favorite = self._favorites.get(favorite_id)
        if favorite is None:
            raise PrecedentFavoriteNotFoundError(
                "요청한 판례 즐겨찾기를 찾을 수 없습니다."
            )
        return favorite

    def is_favorited(self, search_id: UUID, precedent_id: str) -> bool:
        with self._lock:
            return (search_id, precedent_id) in self._result_index

    def delete(self, favorite_id: UUID) -> None:
        with self._lock:
            favorite = self._favorites.pop(favorite_id, None)
            if favorite is None:
                raise PrecedentFavoriteNotFoundError(
                    "요청한 판례 즐겨찾기를 찾을 수 없습니다."
                )
            self._result_index.pop(
                (favorite.search_id, favorite.result.precedent_id),
                None,
            )
