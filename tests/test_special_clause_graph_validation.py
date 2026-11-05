"""
Description: 특약 생성 그래프의 출력 검증·재작성(1회)·제외 흐름을 검증한다.
검색기와 생성기를 가짜로 주입해 문제 응답이 다시 쓰이거나 제외되는지 확인한다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 특약 초안의 법적 단정이나 빈칸·근거 없는 수치를 서비스 실행 중에 걸러내지 못했음.
After:
    - 차단 대상 문제는 한 번 다시 쓰게 하고 그래도 남으면 그 항목만 제외함.
"""

import unittest

from ml.agent.contract_review_graph import ContractReviewEvidence
from ml.agent.special_clause_graph import SpecialClauseGeneration, SpecialClauseGraph


class FakeRetriever:
    def retrieve(self, queries):
        return [
            ContractReviewEvidence(
                item_id=query.item_id,
                source_type="statute",
                source_id=f"law:{query.item_id}",
                title="민법 제356조",
                text="조문: 제356조(저당권의 내용)",
                rank=1,
                similarity=0.9,
                metadata={},
            )
            for query in queries
        ]


def _generation(item_id, draft="임대인은 잔금 다음 날까지 권리 변동을 일으키지 않는다.", status="draft_ready"):
    return SpecialClauseGeneration(
        item_id=item_id,
        status=status,
        draft_text=draft if status == "draft_ready" else None,
        title="권리변동 금지" if status == "draft_ready" else "추가 정보 확인",
        reason="이유입니다." if status == "draft_ready" else None,
        strategy="전략입니다." if status == "draft_ready" else None,
        needs_input=[] if status == "draft_ready" else ["기간을 알려주세요."],
        evidence_source_ids=[f"law:{item_id}"],
    )


class ScriptedGenerator:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def generate(self, queries, evidence, feedback=None):
        self.calls.append({"item_ids": [q.item_id for q in queries], "feedback": feedback})
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _prepare(generator, requests=("권리 변동을 막는 특약",)):
    return SpecialClauseGraph().prepare(
        list(requests), evidence_retriever=FakeRetriever(), generator=generator
    )


class SpecialClauseValidationTests(unittest.TestCase):
    def test_clean_generation_needs_no_retry(self) -> None:
        generator = ScriptedGenerator([_generation("request-1")])

        result = _prepare(generator)

        self.assertEqual(len(generator.calls), 1)
        self.assertEqual([g.item_id for g in result.generations], ["request-1"])
        self.assertEqual(result.validation_dropped_item_ids, [])

    def test_placeholder_draft_is_repaired_once(self) -> None:
        generator = ScriptedGenerator(
            [_generation("request-1", draft="임대인은 ○○일까지 이행한다.")],
            [_generation("request-1")],
        )

        result = _prepare(generator)

        self.assertEqual(len(generator.calls), 2)
        self.assertIn("request-1", generator.calls[1]["feedback"])
        self.assertEqual(result.generations[0].draft_text, "임대인은 잔금 다음 날까지 권리 변동을 일으키지 않는다.")

    def test_still_invalid_after_repair_is_dropped(self) -> None:
        generator = ScriptedGenerator(
            [_generation("request-1", draft="이 계약은 무효로 한다.")],
            [_generation("request-1", draft="계약은 무효로 한다.")],
        )

        result = _prepare(generator)

        self.assertEqual(len(generator.calls), 2)
        self.assertEqual(result.generations, [])
        self.assertEqual(result.validation_dropped_item_ids, ["request-1"])

    def test_only_failing_requests_are_resent_and_good_ones_kept(self) -> None:
        generator = ScriptedGenerator(
            [
                _generation("request-1", draft="이 계약은 무효로 한다."),
                _generation("request-2", status="needs_input"),
            ],
            [_generation("request-1")],
        )

        result = _prepare(generator, requests=("권리 변동 금지", "집 상태 유지"))

        self.assertEqual(generator.calls[1]["item_ids"], ["request-1"])
        self.assertEqual(
            sorted(g.item_id for g in result.generations), ["request-1", "request-2"]
        )

    def test_repair_failure_drops_invalid_item(self) -> None:
        generator = ScriptedGenerator(
            [_generation("request-1", draft="계약은 무효로 한다.")],
            RuntimeError("모델 호출 실패"),
        )

        result = _prepare(generator)

        self.assertEqual(result.generations, [])
        self.assertEqual(result.validation_dropped_item_ids, ["request-1"])
        self.assertFalse(result.generation_unavailable)


class RecordingRetriever(FakeRetriever):
    def __init__(self):
        self.queries = []

    def retrieve(self, queries):
        self.queries.append([q.item_id for q in queries])
        return super().retrieve(queries)


class SpecialClauseScopeTests(unittest.TestCase):
    def test_out_of_scope_request_skips_retrieval_and_generation(self) -> None:
        retriever = RecordingRetriever()
        generator = ScriptedGenerator()

        result = SpecialClauseGraph().prepare(
            ["치킨 레시피 알려줘"], evidence_retriever=retriever, generator=generator
        )

        self.assertEqual(retriever.queries, [])
        self.assertEqual(generator.calls, [])
        self.assertEqual(result.scope_decisions, {"request-1": "out_of_scope"})
        self.assertEqual(result.generations, [])

    def test_mixed_requests_keep_original_item_ids(self) -> None:
        retriever = RecordingRetriever()
        generator = ScriptedGenerator([_generation("request-2")])

        result = SpecialClauseGraph().prepare(
            ["치킨 레시피 알려줘", "권리 변동을 막는 특약"],
            evidence_retriever=retriever,
            generator=generator,
        )

        self.assertEqual(retriever.queries, [["request-2"]])
        self.assertEqual(
            result.scope_decisions,
            {"request-1": "out_of_scope", "request-2": "in_scope"},
        )
        self.assertEqual([g.item_id for g in result.generations], ["request-2"])

    def test_too_short_request_is_not_generated(self) -> None:
        generator = ScriptedGenerator()

        result = SpecialClauseGraph().prepare(
            ["특약"], evidence_retriever=FakeRetriever(), generator=generator
        )

        self.assertEqual(generator.calls, [])
        self.assertEqual(result.scope_decisions, {"request-1": "needs_information"})


if __name__ == "__main__":
    unittest.main()
