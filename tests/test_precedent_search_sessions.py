# test_precedent_search_sessions.py
"""
Description: 판례 검색 세션의 생성·조회·만료와 최대 보관 개수를 검증한다.
가짜 시계를 사용하여 대기 없이 검색 맥락의 수명주기를 확인한다.
Author: choeminju
Date: 2026-10-14
Before:
    - 검색 질문과 판례별 청크 결과를 임시 보관하는 세션 저장소가 정의된 상태.
After:
    - 세션 TTL, 오래된 세션 제거와 판례 소속 검증이 자동 확인됨.
"""

import unittest

from api.services.precedent_search_sessions import (
    PrecedentSearchSessionStore,
    SearchResultNotFoundError,
    SearchSessionNotFoundError,
    SimilarityExplanationInProgressError,
)
from api.models.precedents import (
    SimilarityExplanationMethod,
    SimilarityExplanationStatus,
)
from ml.rag.precedent_retriever import PrecedentSearchResult


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


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


class PrecedentSearchSessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.store = PrecedentSearchSessionStore(
            ttl_seconds=30,
            max_sessions=2,
            clock=self.clock,
        )

    def test_creates_and_reads_search_context(self):
        result = make_result("111")
        session = self.store.create("  보증금 반환  ", [result])

        loaded_session, loaded_result = self.store.get_result(
            session.search_id,
            "111",
        )

        self.assertEqual(loaded_session.query, "보증금 반환")
        self.assertIs(loaded_result, result)
        self.assertEqual(loaded_session.expires_at, 130.0)

    def test_expires_session_at_ttl_boundary(self):
        session = self.store.create("보증금 반환", [make_result("111")])
        self.clock.now = 130.0

        with self.assertRaises(SearchSessionNotFoundError):
            self.store.get(session.search_id)

    def test_evicts_oldest_session_when_capacity_is_reached(self):
        first = self.store.create("첫 질문", [])
        self.clock.now += 1
        second = self.store.create("둘째 질문", [])
        self.clock.now += 1
        third = self.store.create("셋째 질문", [])

        with self.assertRaises(SearchSessionNotFoundError):
            self.store.get(first.search_id)
        self.assertEqual(self.store.get(second.search_id).query, "둘째 질문")
        self.assertEqual(self.store.get(third.search_id).query, "셋째 질문")

    def test_rejects_precedent_not_in_search_results(self):
        session = self.store.create("보증금 반환", [make_result("111")])

        with self.assertRaises(SearchResultNotFoundError):
            self.store.get_result(session.search_id, "222")

    def test_keeps_interaction_state_with_search_result(self):
        session = self.store.create("보증금 반환", [make_result("111")])

        _, _, interaction = self.store.get_result_context(
            session.search_id,
            "111",
        )
        self.assertEqual(
            interaction.snapshot().status,
            SimilarityExplanationStatus.NOT_GENERATED,
        )

        self.assertTrue(
            interaction.start_generation(
                SimilarityExplanationMethod.GEMINI,
                "gemini-test-model",
            )
        )
        interaction.complete_generation(
            SimilarityExplanationMethod.GEMINI,
            "gemini-test-model",
            "유사한 이유",
        )

        snapshot = interaction.snapshot()
        self.assertEqual(snapshot.status, SimilarityExplanationStatus.GENERATED)
        self.assertEqual(snapshot.method, SimilarityExplanationMethod.GEMINI)
        self.assertEqual(snapshot.matched_reason, "유사한 이유")

    def test_rejects_duplicate_generation_while_in_progress(self):
        session = self.store.create("보증금 반환", [make_result("111")])
        _, _, interaction = self.store.get_result_context(
            session.search_id,
            "111",
        )

        interaction.start_generation(
            SimilarityExplanationMethod.GEMINI,
            "gemini-test-model",
        )

        with self.assertRaises(SimilarityExplanationInProgressError):
            interaction.start_generation(
                SimilarityExplanationMethod.GEMINI,
                "gemini-test-model",
            )

    def test_rejects_invalid_configuration_and_empty_query(self):
        with self.assertRaises(ValueError):
            PrecedentSearchSessionStore(ttl_seconds=0)
        with self.assertRaises(ValueError):
            PrecedentSearchSessionStore(max_sessions=0)
        with self.assertRaises(ValueError):
            self.store.create("  ", [])


if __name__ == "__main__":
    unittest.main()
