# test_precedent_api_models.py
"""
Description: 판례 검색 API 요청과 목록·상세·내부 원문 응답 모델을 검증한다.
필드 제약과 검색·LLM 설명 상태 조합이 공개 I/O 계약을 지키는지 확인한다.
Author: choeminju
Date: 2026-10-13
Before:
    - 판례 검색 API의 공개 요청과 응답 스키마가 정의된 상태.
After:
    - 정상 I/O와 잘못된 상태 조합을 자동 테스트로 검증할 수 있음.
"""

import unittest
from uuid import UUID

from pydantic import ValidationError

from api.models.precedents import (
    FullTextSection,
    MatchedChunk,
    PrecedentDetailResponse,
    PrecedentFavoriteListResponse,
    PrecedentFavoriteResponse,
    PrecedentFullTextResponse,
    PrecedentLinks,
    PrecedentMetadata,
    PrecedentOfficialFields,
    PrecedentSearchItem,
    PrecedentSearchRequest,
    PrecedentSearchResponse,
    PrecedentSummaries,
    SearchQuery,
    SearchStatus,
    SimilarityExplanation,
    SimilarityExplanationMethod,
    SimilarityExplanationStatus,
    SummaryPreview,
    SummarySource,
)


SEARCH_ID = UUID("12345678-1234-5678-1234-567812345678")


def make_metadata() -> PrecedentMetadata:
    return PrecedentMetadata(
        precedent_id="64441",
        case_number="2006다62492,62508",
        case_numbers=["2006다62492", "2006다62508"],
        case_name="임대차보증금등·지료등",
        court_name="대법원",
        decision_date="2008-09-25",
        case_type="민사",
        judgment_type="판결",
    )


def make_links() -> PrecedentLinks:
    return PrecedentLinks(
        detail=(
            "/api/v1/precedent-searches/12345678-1234-5678-1234-567812345678"
            "/results/64441"
        ),
        full_text="/api/v1/precedents/64441/full-text",
        official_source=(
            "https://law.go.kr/LSW/precInfoP.do?mode=0&precSeq=64441"
        ),
    )


def make_search_item() -> PrecedentSearchItem:
    return PrecedentSearchItem(
        rank=1,
        precedent=make_metadata(),
        similarity_score=0.91,
        matched_chunk_count=2,
        matched_chunks=[make_chunk()],
        preview=SummaryPreview(
            summary="땅 임대차계약이 묵시적으로 끝난 사건입니다.",
            source=SummarySource.EASY_SUMMARY,
        ),
        links=make_links(),
    )


def make_chunk() -> MatchedChunk:
    return MatchedChunk(
        rank=1,
        chunk_id="precedent:64441:reason:0001",
        section="이유",
        chunk_type="이유",
        section_chunk_index=0,
        similarity_score=0.91,
        text="임대인이 근저당권을 실행하여 임의경매를 신청하였다.",
    )


class PrecedentSearchRequestTests(unittest.TestCase):
    def test_strips_query_and_uses_default_limit(self):
        request = PrecedentSearchRequest(query="  보증금을 돌려받지 못했습니다.  ")

        self.assertEqual(request.query, "보증금을 돌려받지 못했습니다.")
        self.assertEqual(request.limit, 10)

    def test_rejects_short_query_invalid_limit_and_unknown_fields(self):
        with self.assertRaises(ValidationError):
            PrecedentSearchRequest(query="가")
        with self.assertRaises(ValidationError):
            PrecedentSearchRequest(query="보증금 반환", limit=21)
        with self.assertRaises(ValidationError):
            PrecedentSearchRequest(query="보증금 반환", normalized="보증금 반환")


class PrecedentSearchResponseTests(unittest.TestCase):
    def test_accepts_completed_search_with_precedent_level_results(self):
        response = PrecedentSearchResponse(
            search_id=SEARCH_ID,
            status=SearchStatus.COMPLETED,
            query=SearchQuery(text="보증금을 돌려받지 못했습니다."),
            result_count=1,
            results=[make_search_item()],
        )

        self.assertEqual(response.results[0].precedent.precedent_id, "64441")
        self.assertEqual(
            response.results[0].preview.source,
            SummarySource.EASY_SUMMARY,
        )
        self.assertFalse(response.results[0].is_favorited)

        payload = response.model_dump(mode="json")
        self.assertEqual(payload["search_id"], str(SEARCH_ID))
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["results"][0]["preview"]["source"], "easy_summary")

    def test_accepts_out_of_scope_search_with_guidance(self):
        response = PrecedentSearchResponse(
            search_id=SEARCH_ID,
            status=SearchStatus.OUT_OF_SCOPE,
            query=SearchQuery(text="감자"),
            result_count=0,
            results=[],
            message="부동산 법률 상황을 구체적으로 입력해 주세요.",
        )

        self.assertEqual(response.results, [])

    def test_rejects_inconsistent_result_count_and_status(self):
        with self.assertRaises(ValidationError):
            PrecedentSearchResponse(
                search_id=SEARCH_ID,
                status=SearchStatus.COMPLETED,
                query=SearchQuery(text="보증금 반환"),
                result_count=0,
                results=[make_search_item()],
            )
        with self.assertRaises(ValidationError):
            PrecedentSearchResponse(
                search_id=SEARCH_ID,
                status=SearchStatus.OUT_OF_SCOPE,
                query=SearchQuery(text="감자"),
                result_count=0,
                results=[],
            )

    def test_requires_detail_link_for_each_search_result(self):
        item_data = make_search_item().model_dump()
        item_data["links"]["detail"] = None

        with self.assertRaises(ValidationError):
            PrecedentSearchItem.model_validate(item_data)

    def test_requires_preview_text_and_source_together(self):
        with self.assertRaises(ValidationError):
            SummaryPreview(summary="요약만 있음")


class PrecedentDetailResponseTests(unittest.TestCase):
    def test_allows_missing_issue_and_holding(self):
        detail = PrecedentDetailResponse(
            search_id=SEARCH_ID,
            query=SearchQuery(text="임대인이 경매를 신청했습니다."),
            precedent=make_metadata(),
            summaries=PrecedentSummaries(
                precedent_summary="임대차 종료 여부를 판단한 사건이다.",
                easy_summary="경매 신청으로 임대차가 끝났다고 본 사건입니다.",
            ),
            official_fields=PrecedentOfficialFields(),
            similarity_explanation=SimilarityExplanation(
                status=SimilarityExplanationStatus.NOT_GENERATED,
                method=SimilarityExplanationMethod.MATCHED_CHUNKS_ONLY,
                matched_chunks=[make_chunk()],
            ),
            links=make_links(),
        )

        self.assertIsNone(detail.official_fields.issue)
        self.assertIsNone(detail.official_fields.holding)
        self.assertIsNone(detail.similarity_explanation.matched_reason)

    def test_requires_grounded_fields_for_generated_explanation(self):
        explanation = SimilarityExplanation(
            status=SimilarityExplanationStatus.GENERATED,
            method=SimilarityExplanationMethod.LOCAL_LLM,
            model="local-model",
            matched_reason="임대차 종료와 경매 신청이 공통됩니다.",
            matched_chunks=[make_chunk()],
        )

        self.assertEqual(explanation.model, "local-model")

        with self.assertRaises(ValidationError):
            SimilarityExplanation(
                status=SimilarityExplanationStatus.GENERATED,
                method=SimilarityExplanationMethod.LOCAL_LLM,
                matched_chunks=[make_chunk()],
            )
        with self.assertRaises(ValidationError):
            SimilarityExplanation(
                status=SimilarityExplanationStatus.NOT_GENERATED,
                method=SimilarityExplanationMethod.LOCAL_LLM,
                matched_chunks=[make_chunk()],
            )

    def test_accepts_generating_explanation_without_reason(self):
        explanation = SimilarityExplanation(
            status=SimilarityExplanationStatus.GENERATING,
            method=SimilarityExplanationMethod.LOCAL_LLM,
            model="local-model",
            matched_chunks=[make_chunk()],
        )

        self.assertIsNone(explanation.matched_reason)

    def test_accepts_gemini_generated_explanation(self):
        explanation = SimilarityExplanation(
            status=SimilarityExplanationStatus.GENERATED,
            method=SimilarityExplanationMethod.GEMINI,
            model="gemini-2.5-flash-lite",
            matched_reason=(
                "사용자 상황과 이 판례는 보증금 반환이 문제 된 점에서 유사합니다."
            ),
            matched_chunks=[make_chunk()],
        )

        self.assertEqual(explanation.method, SimilarityExplanationMethod.GEMINI)


class PrecedentFavoriteResponseTests(unittest.TestCase):
    def test_validates_favorite_list_count(self):
        favorite = PrecedentFavoriteResponse(
            favorite_id=UUID("22345678-1234-5678-1234-567812345678"),
            created_at="2026-10-15T00:00:00Z",
            search_id=SEARCH_ID,
            query=SearchQuery(text="임대인이 경매를 신청했습니다."),
            precedent=make_metadata(),
            summaries=PrecedentSummaries(),
            official_fields=PrecedentOfficialFields(),
            similarity_explanation=SimilarityExplanation(
                status=SimilarityExplanationStatus.NOT_GENERATED,
                method=SimilarityExplanationMethod.MATCHED_CHUNKS_ONLY,
                matched_chunks=[make_chunk()],
            ),
            links=make_links(),
        )

        response = PrecedentFavoriteListResponse(
            favorite_count=1,
            favorites=[favorite],
        )
        self.assertTrue(response.favorites[0].is_favorited)

        with self.assertRaises(ValidationError):
            PrecedentFavoriteListResponse(
                favorite_count=0,
                favorites=[favorite],
            )


class PrecedentFullTextResponseTests(unittest.TestCase):
    def test_represents_non_empty_final_case_sections(self):
        response = PrecedentFullTextResponse(
            precedent=make_metadata(),
            summaries=PrecedentSummaries(),
            official_fields=PrecedentOfficialFields(issue="판시사항 원문"),
            sections=[
                FullTextSection(
                    position=1,
                    key="order",
                    title="주문",
                    content="사건을 원심법원에 환송한다.",
                ),
                FullTextSection(
                    position=2,
                    key="reason",
                    title="이유",
                    content="상고이유를 판단한다.",
                ),
            ],
            links=make_links(),
        )

        self.assertEqual([section.title for section in response.sections], ["주문", "이유"])


if __name__ == "__main__":
    unittest.main()
