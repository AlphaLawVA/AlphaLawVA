"""검색 질문과 판례 결과를 사용자별 즐겨찾기로 저장한다."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from threading import Lock, RLock
from uuid import UUID, uuid4

import pymysql
from pymysql.cursors import DictCursor

from api.models.precedents import (
    SimilarityExplanationMethod,
    SimilarityExplanationStatus,
)
from api.services.mysql_snapshot_store import MySqlSnapshotStoreSettings
from api.services.precedent_search_sessions import (
    SearchResultInteractionState,
    SimilarityExplanationSnapshot,
)
from ml.rag.precedent_retriever import PrecedentChunkMatch, PrecedentSearchResult


ANONYMOUS_USER_ID = UUID(int=0)


class PrecedentFavoriteNotFoundError(Exception):
    """해당 사용자가 소유한 즐겨찾기가 없는 경우."""


class PrecedentFavorite:
    def __init__(
        self,
        *,
        favorite_id: UUID,
        user_id: UUID,
        search_id: UUID,
        query: str,
        result: PrecedentSearchResult,
        interaction: SearchResultInteractionState,
        created_at: datetime,
    ) -> None:
        self.favorite_id = favorite_id
        self.user_id = user_id
        self.search_id = search_id
        self.query = query
        self.result = result
        self.interaction = interaction
        self.created_at = created_at


class PrecedentFavoriteStore:
    """테스트와 DB 미사용 환경을 위한 사용자별 메모리 저장소."""

    def __init__(self) -> None:
        self._favorites: dict[UUID, PrecedentFavorite] = {}
        self._result_index: dict[tuple[UUID, UUID, str], UUID] = {}
        self._lock = RLock()

    def create(
        self,
        *,
        search_id: UUID,
        query: str,
        result: PrecedentSearchResult,
        interaction: SearchResultInteractionState,
        user_id: UUID = ANONYMOUS_USER_ID,
    ) -> PrecedentFavorite:
        key = (user_id, search_id, result.precedent_id)
        with self._lock:
            existing_id = self._result_index.get(key)
            if existing_id is not None:
                return self._favorites[existing_id]
            favorite = PrecedentFavorite(
                favorite_id=uuid4(),
                user_id=user_id,
                search_id=search_id,
                query=query,
                result=result,
                interaction=interaction,
                created_at=datetime.now(timezone.utc),
            )
            self._favorites[favorite.favorite_id] = favorite
            self._result_index[key] = favorite.favorite_id
            return favorite

    def list(self, user_id: UUID = ANONYMOUS_USER_ID) -> list[PrecedentFavorite]:
        with self._lock:
            return list(
                reversed(
                    [
                        item
                        for item in self._favorites.values()
                        if item.user_id == user_id
                    ]
                )
            )

    def get(
        self, favorite_id: UUID, user_id: UUID = ANONYMOUS_USER_ID
    ) -> PrecedentFavorite:
        with self._lock:
            favorite = self._favorites.get(favorite_id)
        if favorite is None or favorite.user_id != user_id:
            raise PrecedentFavoriteNotFoundError(
                "요청한 판례 즐겨찾기를 찾을 수 없습니다."
            )
        return favorite

    def is_favorited(
        self,
        search_id: UUID,
        precedent_id: str,
        user_id: UUID = ANONYMOUS_USER_ID,
    ) -> bool:
        with self._lock:
            return (user_id, search_id, precedent_id) in self._result_index

    def delete(self, favorite_id: UUID, user_id: UUID = ANONYMOUS_USER_ID) -> None:
        with self._lock:
            favorite = self._favorites.get(favorite_id)
            if favorite is None or favorite.user_id != user_id:
                raise PrecedentFavoriteNotFoundError(
                    "요청한 판례 즐겨찾기를 찾을 수 없습니다."
                )
            del self._favorites[favorite_id]
            self._result_index.pop(
                (favorite.user_id, favorite.search_id, favorite.result.precedent_id),
                None,
            )

    def update_interaction(self, favorite: PrecedentFavorite) -> None:
        return None


class MySqlPrecedentFavoriteStore:
    """검색 질문과 판례 검색 결과를 MySQL에 사용자별로 영속화한다."""

    def __init__(
        self,
        settings: MySqlSnapshotStoreSettings,
        *,
        initialize_schema: bool = True,
    ) -> None:
        self._settings = settings
        self._schema_ready = False
        self._schema_lock = Lock()
        if initialize_schema:
            self.ensure_schema()

    def ensure_schema(self) -> None:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS precedent_favorites (
                        favorite_id CHAR(36) NOT NULL PRIMARY KEY,
                        user_id CHAR(36) NOT NULL,
                        search_id CHAR(36) NOT NULL,
                        precedent_id VARCHAR(100) NOT NULL,
                        query TEXT NOT NULL,
                        result_payload JSON NOT NULL,
                        interaction_payload JSON NOT NULL,
                        created_at DATETIME(6) NOT NULL,
                        updated_at DATETIME(6) NOT NULL,
                        UNIQUE KEY uq_favorite_user_search_precedent (
                            user_id, search_id, precedent_id
                        ),
                        INDEX idx_favorite_user_created (user_id, created_at)
                    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci
                    """
                )
            connection.commit()
            self._schema_ready = True
        finally:
            connection.close()

    def create(
        self,
        *,
        search_id: UUID,
        query: str,
        result: PrecedentSearchResult,
        interaction: SearchResultInteractionState,
        user_id: UUID = ANONYMOUS_USER_ID,
    ) -> PrecedentFavorite:
        self._ensure_schema_ready()
        favorite_id = uuid4()
        now = datetime.now(timezone.utc)
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO precedent_favorites (
                        favorite_id, user_id, search_id, precedent_id, query,
                        result_payload, interaction_payload, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE favorite_id = favorite_id
                    """,
                    (
                        str(favorite_id),
                        str(user_id),
                        str(search_id),
                        result.precedent_id,
                        query,
                        json.dumps(result.to_dict(), ensure_ascii=False),
                        _serialize_snapshot(interaction.snapshot()),
                        _mysql_datetime(now),
                        _mysql_datetime(now),
                    ),
                )
                cursor.execute(
                    """
                    SELECT * FROM precedent_favorites
                    WHERE user_id = %s AND search_id = %s AND precedent_id = %s
                    """,
                    (str(user_id), str(search_id), result.precedent_id),
                )
                row = cursor.fetchone()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return _favorite_from_row(row)

    def list(self, user_id: UUID = ANONYMOUS_USER_ID) -> list[PrecedentFavorite]:
        self._ensure_schema_ready()
        rows = self._fetch_all(
            "SELECT * FROM precedent_favorites WHERE user_id = %s ORDER BY created_at DESC",
            (str(user_id),),
        )
        return [_favorite_from_row(row) for row in rows]

    def get(
        self, favorite_id: UUID, user_id: UUID = ANONYMOUS_USER_ID
    ) -> PrecedentFavorite:
        self._ensure_schema_ready()
        rows = self._fetch_all(
            "SELECT * FROM precedent_favorites WHERE favorite_id = %s AND user_id = %s",
            (str(favorite_id), str(user_id)),
        )
        if not rows:
            raise PrecedentFavoriteNotFoundError(
                "요청한 판례 즐겨찾기를 찾을 수 없습니다."
            )
        return _favorite_from_row(rows[0])

    def is_favorited(
        self,
        search_id: UUID,
        precedent_id: str,
        user_id: UUID = ANONYMOUS_USER_ID,
    ) -> bool:
        self._ensure_schema_ready()
        rows = self._fetch_all(
            """
            SELECT favorite_id FROM precedent_favorites
            WHERE user_id = %s AND search_id = %s AND precedent_id = %s
            """,
            (str(user_id), str(search_id), precedent_id),
        )
        return bool(rows)

    def delete(self, favorite_id: UUID, user_id: UUID = ANONYMOUS_USER_ID) -> None:
        self._ensure_schema_ready()
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                affected = cursor.execute(
                    "DELETE FROM precedent_favorites WHERE favorite_id = %s AND user_id = %s",
                    (str(favorite_id), str(user_id)),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        if not affected:
            raise PrecedentFavoriteNotFoundError(
                "요청한 판례 즐겨찾기를 찾을 수 없습니다."
            )

    def update_interaction(self, favorite: PrecedentFavorite) -> None:
        self._ensure_schema_ready()
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                affected = cursor.execute(
                    """
                    UPDATE precedent_favorites
                    SET interaction_payload = %s, updated_at = CURRENT_TIMESTAMP(6)
                    WHERE favorite_id = %s AND user_id = %s
                    """,
                    (
                        _serialize_snapshot(favorite.interaction.snapshot()),
                        str(favorite.favorite_id),
                        str(favorite.user_id),
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        if not affected:
            raise PrecedentFavoriteNotFoundError(
                "요청한 판례 즐겨찾기를 찾을 수 없습니다."
            )

    def _fetch_all(self, statement: str, values: tuple) -> list[dict]:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(statement, values)
                return list(cursor.fetchall())
        finally:
            connection.close()

    def _ensure_schema_ready(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if not self._schema_ready:
                self.ensure_schema()

    def _connect(self) -> pymysql.connections.Connection:
        return pymysql.connect(
            host=self._settings.host,
            port=self._settings.port,
            user=self._settings.user,
            password=self._settings.password,
            database=self._settings.database,
            charset="utf8mb4",
            cursorclass=DictCursor,
        )


def create_precedent_favorite_store_from_env():
    settings = MySqlSnapshotStoreSettings.from_env()
    if settings is None:
        return PrecedentFavoriteStore()
    return MySqlPrecedentFavoriteStore(settings, initialize_schema=False)


def _favorite_from_row(row: dict) -> PrecedentFavorite:
    result_payload = row["result_payload"]
    if isinstance(result_payload, str):
        result_payload = json.loads(result_payload)
    interaction_payload = row["interaction_payload"]
    if isinstance(interaction_payload, str):
        interaction_payload = json.loads(interaction_payload)
    return PrecedentFavorite(
        favorite_id=UUID(row["favorite_id"]),
        user_id=UUID(row["user_id"]),
        search_id=UUID(row["search_id"]),
        query=row["query"],
        result=_deserialize_result(result_payload),
        interaction=SearchResultInteractionState(
            initial_snapshot=_deserialize_snapshot(interaction_payload)
        ),
        created_at=_utc_datetime(row["created_at"]),
    )


def _deserialize_result(payload: dict) -> PrecedentSearchResult:
    return PrecedentSearchResult(
        **{
            **payload,
            "matched_chunks": [
                PrecedentChunkMatch(**chunk) for chunk in payload["matched_chunks"]
            ],
        }
    )


def _serialize_snapshot(snapshot: SimilarityExplanationSnapshot) -> str:
    return json.dumps(
        {
            "status": snapshot.status.value,
            "method": snapshot.method.value if snapshot.method else None,
            "model": snapshot.model,
            "matched_reason": snapshot.matched_reason,
        },
        ensure_ascii=False,
    )


def _deserialize_snapshot(payload: dict) -> SimilarityExplanationSnapshot:
    return SimilarityExplanationSnapshot(
        status=SimilarityExplanationStatus(payload["status"]),
        method=(
            SimilarityExplanationMethod(payload["method"])
            if payload.get("method")
            else None
        ),
        model=payload.get("model"),
        matched_reason=payload.get("matched_reason"),
    )


def _mysql_datetime(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utc_datetime(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )
