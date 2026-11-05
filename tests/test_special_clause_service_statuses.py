"""
Description: 특약 생성 서비스의 세 가지 항목 상태(draft_ready, needs_input, pending_generation)를 검증한다.
검색기·생성기를 주입된 가짜로 대체해 외부 호출 없이 서버가 상태를 정하는 방식을 확인한다.
Author: ooheunsu
Date: 2026-11-04
Before:
    - 특약 서비스가 생성 대기 상태를 내는 경로의 테스트가 없는 상태.
After:
    - 생성기 없음, 검색 실패, 항목 누락 시 pending_generation을 회귀 검증함.
"""

import unittest

from api.models.contracts import (
    EvidenceStatus,
    GenerationStatus,
    SpecialClauseDraftRequest,
)
from api.services.special_clause_service import SpecialClauseService
from ml.agent.contract_review_graph import ContractReviewEvidence
from ml.agent.special_clause_scope import OUT_OF_SCOPE_MESSAGE, TOO_SHORT_QUESTION
from ml.agent.special_clause_graph import SpecialClauseGeneration


class FakeRetriever:
    def retrieve(self, queries):
        return [
            ContractReviewEvidence(
                item_id=query.item_id,
                source_type="statute",
                source_id="law:1",
                title="조문",
                text="원문",
                rank=1,
                similarity=0.9,
                metadata={},
            )
            for query in queries
        ]


class BrokenRetriever:
    def retrieve(self, queries):
        raise RuntimeError("검색 실패")


class FakeGenerator:
    def __init__(self, generations):
        self._generations = generations

    def generate(self, queries, evidence):
        return self._generations


def _generation(item_id, status):
    return SpecialClauseGeneration(
        item_id=item_id,
        status=status,
        draft_text="특약 초안 문구" if status == "draft_ready" else None,
        title="제목입니다",
        reason="이유" if status == "draft_ready" else None,
        strategy="전략" if status == "draft_ready" else None,
        needs_input=[] if status == "draft_ready" else ["기간을 알려주세요."],
        evidence_source_ids=["law:1"],
    )


REQUEST = SpecialClauseDraftRequest(requests=["추가 담보 설정 금지", "수리비 부담"])


class SpecialClauseServiceStatusTests(unittest.TestCase):
    def test_without_retriever_and_generator_all_items_are_pending(self) -> None:
        response = SpecialClauseService().create_draft(REQUEST)

        self.assertEqual(
            [item.status for item in response.items],
            ["pending_generation", "pending_generation"],
        )
        self.assertTrue(all(item.draft_text is None for item in response.items))
        self.assertEqual(response.generation_status, GenerationStatus.PENDING)

    def test_retrieval_failure_falls_back_to_pending(self) -> None:
        service = SpecialClauseService(
            evidence_retriever=BrokenRetriever(),
            generator=FakeGenerator([]),
        )

        response = service.create_draft(REQUEST)

        self.assertEqual(response.evidence_status, EvidenceStatus.UNAVAILABLE)
        self.assertEqual(response.generation_status, GenerationStatus.UNAVAILABLE)
        self.assertTrue(
            all(item.status == "pending_generation" for item in response.items)
        )

    def test_items_get_their_own_status_and_missing_one_stays_pending(self) -> None:
        generator = FakeGenerator([_generation("request-1", "draft_ready")])
        service = SpecialClauseService(
            evidence_retriever=FakeRetriever(), generator=generator
        )

        response = service.create_draft(REQUEST)

        self.assertEqual(
            [item.status for item in response.items],
            ["draft_ready", "pending_generation"],
        )
        self.assertIsNotNone(response.items[0].draft_text)
        self.assertIsNone(response.items[1].draft_text)

    def test_needs_input_item_returns_questions_without_draft(self) -> None:
        generator = FakeGenerator(
            [
                _generation("request-1", "needs_input"),
                _generation("request-2", "draft_ready"),
            ]
        )
        service = SpecialClauseService(
            evidence_retriever=FakeRetriever(), generator=generator
        )

        response = service.create_draft(REQUEST)

        self.assertEqual(
            [item.status for item in response.items], ["needs_information", "draft_ready"]
        )
        self.assertIsNone(response.items[0].draft_text)
        self.assertEqual(response.items[0].needs_input, ["기간을 알려주세요."])


class ScopeStatusTests(unittest.TestCase):
    def test_out_of_scope_item_has_fixed_message_and_no_draft(self) -> None:
        service = SpecialClauseService(
            evidence_retriever=FakeRetriever(),
            generator=FakeGenerator([_generation("request-2", "draft_ready")]),
        )

        response = service.create_draft(
            SpecialClauseDraftRequest(requests=["치킨 레시피 알려줘", "추가 담보 설정 금지"])
        )

        self.assertEqual(
            [item.status for item in response.items], ["out_of_scope", "draft_ready"]
        )
        self.assertEqual(response.items[0].reason, OUT_OF_SCOPE_MESSAGE)
        self.assertIsNone(response.items[0].draft_text)
        self.assertEqual(response.items[0].needs_input, [])

    def test_too_short_request_asks_one_fixed_question(self) -> None:
        response = SpecialClauseService().create_draft(
            SpecialClauseDraftRequest(requests=["특약"])
        )

        self.assertEqual(response.items[0].status, "needs_information")
        self.assertEqual(response.items[0].needs_input, [TOO_SHORT_QUESTION])

    def test_all_terminal_statuses_complete_the_response(self) -> None:
        response = SpecialClauseService().create_draft(
            SpecialClauseDraftRequest(requests=["치킨 레시피 알려줘"])
        )

        self.assertEqual(response.status, "completed")
        self.assertIn("관련", response.message)


if __name__ == "__main__":
    unittest.main()
