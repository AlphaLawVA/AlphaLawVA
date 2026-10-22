# test_certified_letter_graph.py
"""내용증명 LangGraph의 조기 종료, 근거 분리와 단일 재작성 경로를 검증한다."""

import unittest
from datetime import date

from ml.agent.certified_letter.graph import build_certified_letter_graph
from ml.agent.certified_letter.nodes import CertifiedLetterNodeDependencies
from ml.agent.certified_letter.state import (
    CertifiedLetterRequestData,
    ContractData,
    DraftPartyData,
    LetterDraftData,
    PartyData,
    PaymentData,
    PeriodData,
    PrecedentEvidenceData,
    PropertyData,
    StatuteEvidenceData,
    ValidationIssue,
)


def make_request(
    *,
    purpose: str = "보증금을 반환받고 싶습니다.",
    facts: str = (
        "임대인 홍길동에게 서울특별시 종로구 사직로 1로 보증금 반환을 "
        "요청했지만 아직 받지 못했습니다."
    ),
):
    return CertifiedLetterRequestData(
        purpose=purpose,
        facts=facts,
        requested_deadline=None,
        contract=ContractData(
            contract_type="jeonse",
            property=PropertyData(
                address="서울특별시 중구 세종대로 1",
                detail_address="101동 101호",
                building_type="apartment",
            ),
            period=PeriodData(
                start_date=date(2024, 10, 1),
                end_date=date(2026, 9, 30),
            ),
            payment=PaymentData(deposit=100_000_000),
            lessor=PartyData(
                name="홍길동",
                address="서울특별시 종로구 사직로 1",
            ),
            lessee=PartyData(
                name="김민지",
                address="서울특별시 중구 세종대로 1",
                detail_address="101동 101호",
            ),
        ),
    )


def make_statute() -> StatuteEvidenceData:
    return StatuteEvidenceData(
        chunk_id="statute:001:article:4",
        text="임대차 기간과 보증금 반환에 관한 조문",
        similarity=0.91,
        statute_id="001",
        law_name="주택임대차보호법",
        article_label="제4조",
    )


def make_precedent() -> PrecedentEvidenceData:
    return PrecedentEvidenceData(
        precedent_id="64441",
        text="임대차 종료와 보증금 반환이 문제 된 판례",
        similarity=0.88,
        case_number="2006다62492",
        case_name="임대차보증금등",
    )


def make_draft() -> LetterDraftData:
    return LetterDraftData(
        title="임대차보증금 반환 촉구서",
        sender=DraftPartyData(
            name="김민지",
            address="서울특별시 중구 세종대로 1",
            detail_address="101동 101호",
        ),
        recipient=DraftPartyData(
            name="홍길동",
            address="서울특별시 종로구 사직로 1",
        ),
        body="「주택임대차보호법」 제4조를 근거로 보증금 반환을 요청합니다.",
        strategy_summary="보증금 반환 의무와 이행 기한을 명확히 통지합니다.",
    )


class FakeStatuteSearch:
    def __init__(self, results=None):
        self.results = [make_statute()] if results is None else results
        self.calls = []

    def search(self, queries, *, candidate_limit):
        self.calls.append((tuple(queries), candidate_limit))
        return self.results


class FakePrecedentSearch:
    def __init__(self, results=None):
        self.results = [make_precedent()] if results is None else results
        self.calls = []

    def search(self, queries, *, candidate_limit):
        self.calls.append((tuple(queries), candidate_limit))
        return self.results


class PassThroughReranker:
    def __init__(self):
        self.calls = []

    def rerank(self, query, candidates, *, top_k):
        self.calls.append((query, tuple(candidates), top_k))
        return list(candidates)[:top_k]


class FakeGenerator:
    def __init__(self):
        self.generate_calls = []
        self.repair_calls = []

    def generate(self, request, statute_evidence):
        self.generate_calls.append((request, tuple(statute_evidence)))
        return make_draft()

    def repair(self, request, statute_evidence, draft, issues):
        self.repair_calls.append(
            (request, tuple(statute_evidence), draft, tuple(issues))
        )
        return make_draft()


class SequencedValidator:
    def __init__(self, responses=None):
        self.responses = list(responses or [[]])
        self.calls = []

    def validate(self, request, statute_evidence, draft):
        self.calls.append((request, tuple(statute_evidence), draft))
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


def make_dependencies(*, statutes=None, validation_responses=None):
    statute_search = FakeStatuteSearch(statutes)
    precedent_search = FakePrecedentSearch()
    statute_reranker = PassThroughReranker()
    precedent_reranker = PassThroughReranker()
    generator = FakeGenerator()
    validator = SequencedValidator(validation_responses)
    dependencies = CertifiedLetterNodeDependencies(
        statute_search=statute_search,
        precedent_search=precedent_search,
        statute_reranker=statute_reranker,
        precedent_reranker=precedent_reranker,
        generator=generator,
        validator=validator,
    )
    return dependencies, statute_search, precedent_search, generator, validator


class CertifiedLetterGraphTests(unittest.TestCase):
    def test_completes_grounded_draft_and_keeps_precedents_out_of_generator(self):
        dependencies, statute_search, precedent_search, generator, validator = (
            make_dependencies()
        )
        graph = build_certified_letter_graph(dependencies)

        result = graph.invoke({"request": make_request()})

        self.assertEqual(result["outcome"], "draft_ready")
        self.assertEqual(result["selected_precedents"][0].precedent_id, "64441")
        self.assertEqual(generator.generate_calls[0][1], (make_statute(),))
        self.assertEqual(len(generator.generate_calls[0]), 2)
        self.assertEqual(len(validator.calls), 1)
        self.assertEqual(len(statute_search.calls), 1)
        self.assertEqual(len(precedent_search.calls), 1)

        statute_query = statute_search.calls[0][0][0]
        self.assertNotIn("홍길동", statute_query)
        self.assertNotIn("서울특별시 종로구 사직로 1", statute_query)

    def test_stops_out_of_scope_before_search(self):
        dependencies, statute_search, precedent_search, generator, _validator = (
            make_dependencies()
        )
        graph = build_certified_letter_graph(dependencies)

        result = graph.invoke(
            {
                "request": make_request(
                    purpose="주말 저녁 메뉴를 추천해 주세요.",
                    facts="친구들과 먹을 저녁 음식 종류를 추천받고 싶습니다.",
                )
            }
        )

        self.assertEqual(result["outcome"], "out_of_scope")
        self.assertEqual(statute_search.calls, [])
        self.assertEqual(precedent_search.calls, [])
        self.assertEqual(generator.generate_calls, [])

    def test_stops_without_generation_when_statute_evidence_is_empty(self):
        dependencies, _statute_search, precedent_search, generator, _validator = (
            make_dependencies(statutes=[])
        )
        graph = build_certified_letter_graph(dependencies)

        result = graph.invoke({"request": make_request()})

        self.assertEqual(result["outcome"], "insufficient_evidence")
        self.assertEqual(len(precedent_search.calls), 1)
        self.assertEqual(generator.generate_calls, [])

    def test_repairs_once_then_completes(self):
        issue = ValidationIssue(
            code="unsupported_statute_citation",
            message="검색되지 않은 법령 인용이 있습니다.",
        )
        dependencies, *_others, generator, validator = make_dependencies(
            validation_responses=[[issue], []]
        )
        graph = build_certified_letter_graph(dependencies)

        result = graph.invoke({"request": make_request()})

        self.assertEqual(result["outcome"], "draft_ready")
        self.assertEqual(result["repair_attempts"], 1)
        self.assertEqual(len(generator.repair_calls), 1)
        self.assertEqual(len(validator.calls), 2)

    def test_fails_after_single_unsuccessful_repair(self):
        issue = ValidationIssue(
            code="fact_mismatch",
            message="계약 금액이 일치하지 않습니다.",
        )
        dependencies, *_others, generator, validator = make_dependencies(
            validation_responses=[[issue], [issue]]
        )
        graph = build_certified_letter_graph(dependencies)

        result = graph.invoke({"request": make_request()})

        self.assertEqual(
            result["failure"].code,
            "certified_letter_validation_failed",
        )
        self.assertNotIn("outcome", result)
        self.assertEqual(len(generator.repair_calls), 1)
        self.assertEqual(len(validator.calls), 2)

    def test_does_not_repair_non_repairable_issue(self):
        issue = ValidationIssue(
            code="unsafe_output",
            message="안전하게 자동 수정할 수 없는 결과입니다.",
            is_repairable=False,
        )
        dependencies, *_others, generator, validator = make_dependencies(
            validation_responses=[[issue]]
        )
        graph = build_certified_letter_graph(dependencies)

        result = graph.invoke({"request": make_request()})

        self.assertEqual(
            result["failure"].code,
            "certified_letter_validation_failed",
        )
        self.assertEqual(generator.repair_calls, [])
        self.assertEqual(len(validator.calls), 1)


if __name__ == "__main__":
    unittest.main()
