# test_precedent_search_service.py
"""
Description: 판례 검색 서비스의 목록·상세·내부 원문 응답 조합을 검증한다.
가짜 검색기와 임시 final_cases를 사용해 외부 모델 없이 공개 I/O 동작을 확인한다.
Author: choeminju
Date: 2026-10-14
Before:
    - 검색기 결과와 판례 원본을 API 응답으로 조합하는 서비스가 정의된 상태.
After:
    - 검색 상태, 요약 우선순위, 청크 근거와 선택형 LLM 설명 동작이 검증됨.
"""

import json
import tempfile
import unittest
from pathlib import Path

from api.models.precedents import (
    PrecedentSearchRequest,
    SearchStatus,
    SimilarityExplanationStatus,
    SummarySource,
)
from api.services.precedent_case_repository import PrecedentCaseRepository
from api.services.precedent_search_service import (
    SUMMARY_PREVIEW_MAX_LENGTH,
    PrecedentSearchBackendUnavailableError,
    PrecedentSearchService,
    ScopeDecision,
)
from ml.rag.precedent_retriever import (
    PrecedentChunkMatch,
    PrecedentSearchResult,
)


class FakeRetriever:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, *, top_k=10):
        self.calls.append((query, top_k))
        return list(self.results)


class RejectingScopeGuard:
    def evaluate(self, query):
        return ScopeDecision(False, f"검색 범위가 아닙니다: {query}")


class FailingRetriever:
    def search(self, _query, *, top_k=10):
        raise FileNotFoundError(f"missing search backend for top_k={top_k}")


class FakeExplainer:
    model_name = "local-test-model"

    def __init__(self, *, should_fail=False):
        self.should_fail = should_fail

    def explain(self, **_kwargs):
        if self.should_fail:
            raise RuntimeError("local model unavailable")
        return "임대차 종료 시점과 경매 신청이라는 사실관계가 유사합니다."


def make_chunk() -> PrecedentChunkMatch:
    return PrecedentChunkMatch(
        rank=1,
        chunk_id="precedent:64441:reason:0001",
        distance=0.08,
        similarity=0.92,
        text="임대인이 근저당권을 실행하여 임의경매를 신청하였다.",
        chunk_type="이유",
        section="이유",
        section_chunk_index=0,
        metadata={"precedent_id": "64441"},
    )


def make_result() -> PrecedentSearchResult:
    return PrecedentSearchResult(
        rank=1,
        precedent_id="64441",
        distance=0.08,
        similarity=0.92,
        text="검색기 대표 청크",
        case_no="검색기 사건번호",
        case_no_list="검색기 사건번호",
        case_name="검색기 사건명",
        court_name="검색기 법원",
        decision_date="2000-01-01",
        decision_year=2000,
        case_type="민사",
        judgment_type="판결",
        source_path=None,
        matched_chunks=[make_chunk()],
        metadata={},
    )


class PrecedentSearchServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        cases_dir = Path(self.temporary_directory.name)
        case_data = {
            "판례일련번호": "64441",
            "사건번호": "2006다62492,62508",
            "사건번호목록": ["2006다62492", "2006다62508"],
            "사건명": "임대차보증금등·지료등",
            "법원명": "대법원",
            "선고일자": "2008-09-25",
            "사건종류명": "민사",
            "판결유형": "판결",
            "판시사항": "계약해지 효과가 발생하는지 여부",
            "판결요지": "",
            "참조조문": "민법 제543조",
            "참조판례": "",
            "주문": "사건을 원심법원에 환송한다.",
            "청구취지": "",
            "원심판결": "청주지방법원 판결",
            "이유": "임대차 종료 시점을 판단한다.",
            "생성요약": "임대차계약 종료 여부를 판단한 사건이다.",
            "쉬운요약": "가" * (SUMMARY_PREVIEW_MAX_LENGTH + 20),
        }
        (cases_dir / "64441.json").write_text(
            json.dumps(case_data, ensure_ascii=False),
            encoding="utf-8",
        )
        self.repository = PrecedentCaseRepository(cases_dir)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_service(self, **kwargs):
        retriever = kwargs.pop("retriever", FakeRetriever([make_result()]))
        service = PrecedentSearchService(
            retriever=retriever,
            repository=self.repository,
            **kwargs,
        )
        return service, retriever

    def test_search_builds_precedent_level_result_from_final_case(self):
        service, retriever = self.make_service()

        response = service.search(
            PrecedentSearchRequest(query="임대인이 경매를 신청했습니다.", limit=5)
        )

        self.assertEqual(response.status, SearchStatus.COMPLETED)
        self.assertEqual(retriever.calls, [("임대인이 경매를 신청했습니다.", 5)])
        self.assertEqual(response.result_count, 1)
        item = response.results[0]
        self.assertEqual(item.precedent.case_name, "임대차보증금등·지료등")
        self.assertEqual(item.preview.source, SummarySource.EASY_SUMMARY)
        self.assertEqual(len(item.preview.summary), SUMMARY_PREVIEW_MAX_LENGTH)
        self.assertTrue(item.preview.summary.endswith("..."))
        self.assertIn(str(response.search_id), item.links.detail)
        self.assertTrue(item.links.official_source.endswith("precSeq=64441"))

    def test_search_returns_no_results_without_fabricating_cases(self):
        service, _ = self.make_service(retriever=FakeRetriever([]))

        response = service.search(PrecedentSearchRequest(query="보증금 반환"))

        self.assertEqual(response.status, SearchStatus.NO_RESULTS)
        self.assertEqual(response.results, [])
        self.assertIsNotNone(response.message)

    def test_search_wraps_unavailable_retrieval_backend(self):
        service, _ = self.make_service(retriever=FailingRetriever())

        with self.assertRaises(PrecedentSearchBackendUnavailableError):
            service.search(PrecedentSearchRequest(query="보증금 반환"))

    def test_scope_guard_can_reject_before_retrieval(self):
        service, retriever = self.make_service(scope_guard=RejectingScopeGuard())

        response = service.search(PrecedentSearchRequest(query="감자"))

        self.assertEqual(response.status, SearchStatus.OUT_OF_SCOPE)
        self.assertEqual(retriever.calls, [])
        self.assertIn("감자", response.message)

    def test_detail_returns_chunks_when_llm_is_not_connected(self):
        service, _ = self.make_service()
        search_response = service.search(PrecedentSearchRequest(query="경매 신청"))

        detail = service.get_detail(search_response.search_id, "64441")

        self.assertIsNone(detail.official_fields.holding)
        self.assertEqual(
            detail.similarity_explanation.status,
            SimilarityExplanationStatus.NOT_GENERATED,
        )
        self.assertIsNone(detail.similarity_explanation.matched_reason)
        self.assertEqual(
            detail.similarity_explanation.matched_chunks[0].chunk_id,
            "precedent:64441:reason:0001",
        )

    def test_detail_uses_optional_local_llm_explainer(self):
        service, _ = self.make_service(similarity_explainer=FakeExplainer())
        search_response = service.search(PrecedentSearchRequest(query="경매 신청"))

        detail = service.get_detail(search_response.search_id, "64441")

        explanation = detail.similarity_explanation
        self.assertEqual(explanation.status, SimilarityExplanationStatus.GENERATED)
        self.assertEqual(explanation.model, "local-test-model")
        self.assertIn("사실관계", explanation.matched_reason)

    def test_detail_reports_local_llm_failure_without_hiding_chunks(self):
        service, _ = self.make_service(
            similarity_explainer=FakeExplainer(should_fail=True)
        )
        search_response = service.search(PrecedentSearchRequest(query="경매 신청"))

        detail = service.get_detail(search_response.search_id, "64441")

        explanation = detail.similarity_explanation
        self.assertEqual(explanation.status, SimilarityExplanationStatus.FAILED)
        self.assertIsNone(explanation.matched_reason)
        self.assertEqual(len(explanation.matched_chunks), 1)

    def test_full_text_omits_empty_sections_and_keeps_official_fields(self):
        service, _ = self.make_service()

        response = service.get_full_text("64441")

        self.assertEqual(
            [section.key for section in response.sections],
            ["order", "lower_court_decision", "reason"],
        )
        self.assertEqual(response.official_fields.issue, "계약해지 효과가 발생하는지 여부")
        self.assertIsNone(response.official_fields.holding)
        self.assertIsNone(response.links.detail)


if __name__ == "__main__":
    unittest.main()
