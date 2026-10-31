# test_precedent_favorites.py
"""
Description: 인메모리 판례 즐겨찾기의 검색 맥락 보관과 설명 상태 공유를 검증한다.
동일 검색 결과의 중복 저장 방지, 조회 순서와 삭제 오류를 확인한다.
Author: choeminju
Date: 2026-10-15
Before:
    - 검색 결과와 설명 상태를 참조하는 판례 즐겨찾기 저장소가 정의된 상태.
After:
    - 즐겨찾기 수명주기와 공유 상태 동작이 자동 검증됨.
"""

import unittest
from uuid import uuid4

from api.services.precedent_favorites import (
    PrecedentFavoriteNotFoundError,
    PrecedentFavoriteStore,
)
from api.services.precedent_search_sessions import SearchResultInteractionState
from ml.rag.precedent_retriever import PrecedentSearchResult


def make_result(precedent_id: str) -> PrecedentSearchResult:
    return PrecedentSearchResult(
        rank=1,
        precedent_id=precedent_id,
        distance=0.1,
        similarity=0.9,
        text="판례 청크",
        case_no=None,
        case_no_list=None,
        case_name=None,
        court_name=None,
        decision_date=None,
        decision_year=None,
        case_type=None,
        judgment_type=None,
        source_path=None,
        matched_chunks=[],
        metadata={},
    )


class PrecedentFavoriteStoreTests(unittest.TestCase):
    def test_reuses_same_favorite_and_shared_interaction(self):
        store = PrecedentFavoriteStore()
        search_id = uuid4()
        interaction = SearchResultInteractionState()
        result = make_result("111")

        first = store.create(
            search_id=search_id,
            query="보증금 반환",
            result=result,
            interaction=interaction,
        )
        second = store.create(
            search_id=search_id,
            query="보증금 반환",
            result=result,
            interaction=interaction,
        )

        self.assertIs(first, second)
        self.assertIs(first.interaction, interaction)
        self.assertTrue(store.is_favorited(search_id, "111"))
        self.assertEqual(len(store.list()), 1)

    def test_lists_newest_first_and_deletes_favorite(self):
        store = PrecedentFavoriteStore()
        first = store.create(
            search_id=uuid4(),
            query="첫 질문",
            result=make_result("111"),
            interaction=SearchResultInteractionState(),
        )
        second = store.create(
            search_id=uuid4(),
            query="둘째 질문",
            result=make_result("222"),
            interaction=SearchResultInteractionState(),
        )

        self.assertEqual(
            [item.favorite_id for item in store.list()],
            [second.favorite_id, first.favorite_id],
        )

        store.delete(first.favorite_id)
        with self.assertRaises(PrecedentFavoriteNotFoundError):
            store.get(first.favorite_id)
        with self.assertRaises(PrecedentFavoriteNotFoundError):
            store.delete(first.favorite_id)


if __name__ == "__main__":
    unittest.main()
