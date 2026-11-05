"""
Description: 계약서 검토 그래프의 출력 검증·재작성(1회)·제외 흐름을 검증한다.
검색기와 분석기를 가짜로 주입해 문제 응답이 다시 쓰이거나 제외되는지 확인한다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 분석 결과의 법적 단정이나 근거 없는 수치를 서비스 실행 중에 걸러내지 못했음.
After:
    - 차단 대상 문제는 한 번 다시 쓰게 하고 그래도 남으면 그 항목만 제외함.
"""

import unittest

from ml.agent.contract_review_graph import (
    ContractReviewAnalysis,
    ContractReviewEvidence,
    ContractReviewGraph,
)
from tests.test_registry_attention_items import _contract

TERM = "임차인이 계약을 해제하면 보증금의 20%를 위약금으로 지급한다."
EVIDENCE_TEXT = "조문: 제551조(해지, 해제와 손해배상)\n해지 또는 해제는 손해배상의 청구에 영향을 미치지 아니한다."


class FakeRetriever:
    def retrieve(self, queries):
        return [
            ContractReviewEvidence(
                item_id=query.item_id,
                source_type="statute",
                source_id=f"law:{query.item_id}",
                title="민법 제551조",
                text=EVIDENCE_TEXT,
                rank=1,
                similarity=0.9,
                metadata={},
            )
            for query in queries
            if query.category == "special_term"
        ]


def _analysis(
    item_id,
    summary="위약금 비율을 확인하세요.",
    revision="임차인이 계약을 해제하면 보증금의 20%를 위약금으로 지급한다.",
):
    return ContractReviewAnalysis(
        item_id=item_id,
        summary=summary,
        review_status="check",
        revision=revision,
        negotiation="비율을 협의하세요.",
        evidence_source_ids=[f"law:{item_id}"],
    )


class ScriptedAnalyzer:
    """호출 순서대로 미리 정한 응답을 돌려주는 가짜 분석기다."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def analyze(self, queries, evidence, feedback=None):
        self.calls.append(
            {"item_ids": [q.item_id for q in queries], "feedback": feedback}
        )
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _prepare(analyzer, terms=(TERM,)):
    return ContractReviewGraph().prepare(
        _contract(special_terms=list(terms)),
        None,
        evidence_retriever=FakeRetriever(),
        analyzer=analyzer,
    )


class ContractReviewValidationTests(unittest.TestCase):
    def test_clean_analysis_is_kept_without_retry(self) -> None:
        analyzer = ScriptedAnalyzer([_analysis("term-1")])

        result = _prepare(analyzer)

        self.assertEqual(len(analyzer.calls), 1)
        self.assertEqual([a.item_id for a in result.analyses], ["term-1"])
        self.assertEqual(result.validation_dropped_item_ids, [])

    def test_problem_is_repaired_once_with_feedback(self) -> None:
        analyzer = ScriptedAnalyzer(
            [_analysis("term-1", summary="이 조항은 무효입니다.")],
            [_analysis("term-1")],
        )

        result = _prepare(analyzer)

        self.assertEqual(len(analyzer.calls), 2)
        self.assertIsNone(analyzer.calls[0]["feedback"])
        self.assertIn("term-1", analyzer.calls[1]["feedback"])
        self.assertTrue(analyzer.calls[1]["feedback"]["term-1"])
        self.assertEqual(result.analyses[0].summary, "위약금 비율을 확인하세요.")
        self.assertEqual(result.validation_dropped_item_ids, [])

    def test_still_invalid_after_one_repair_is_dropped(self) -> None:
        analyzer = ScriptedAnalyzer(
            [_analysis("term-1", summary="이 조항은 무효입니다.")],
            [_analysis("term-1", summary="여전히 무효입니다.")],
        )

        result = _prepare(analyzer)

        self.assertEqual(len(analyzer.calls), 2)
        self.assertEqual(result.analyses, [])
        self.assertEqual(result.validation_dropped_item_ids, ["term-1"])

    def test_repair_failure_drops_only_invalid_items(self) -> None:
        analyzer = ScriptedAnalyzer(
            [_analysis("term-1", summary="무효입니다."), _analysis("term-2", revision="하자는 임대인이 책임진다.")],
            RuntimeError("모델 호출 실패"),
        )

        result = _prepare(analyzer, terms=(TERM, "임대인은 하자를 책임진다."))

        self.assertEqual([a.item_id for a in result.analyses], ["term-2"])
        self.assertEqual(result.validation_dropped_item_ids, ["term-1"])
        self.assertFalse(result.analysis_unavailable)

    def test_repair_resends_only_failing_items(self) -> None:
        analyzer = ScriptedAnalyzer(
            [_analysis("term-1", summary="무효입니다."), _analysis("term-2", revision="하자는 임대인이 책임진다.")],
            [_analysis("term-1")],
        )

        result = _prepare(analyzer, terms=(TERM, "임대인은 하자를 책임진다."))

        self.assertEqual(analyzer.calls[1]["item_ids"], ["term-1"])
        self.assertEqual(sorted(a.item_id for a in result.analyses), ["term-1", "term-2"])

    def test_without_analyzer_nothing_is_validated(self) -> None:
        result = _prepare(None)

        self.assertEqual(result.analyses, [])
        self.assertEqual(result.validation_dropped_item_ids, [])


if __name__ == "__main__":
    unittest.main()
