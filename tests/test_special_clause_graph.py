"""
Description: 특약 생성 LangGraph의 비식별 질의와 근거·생성 연결을 검증한다.
외부 검색기나 생성 모델 호출 없이 입력 경계와 상태를 회귀 테스트한다.
Author: ooheunsu
Date: 2026-10-31
Before:
    - 특약 생성 그래프의 질의 비식별화와 생성 결과 연결 테스트가 없는 상태.
After:
    - 요청별 근거 ID와 특약 초안 상태를 검증할 수 있음.
"""

from __future__ import annotations

import unittest

from ml.agent.contract_review_graph import ContractReviewEvidence
from ml.agent.special_clause_graph import SpecialClauseGeneration, SpecialClauseGraph


class FakeEvidenceRetriever:
    def retrieve(self, queries):
        return [
            ContractReviewEvidence(
                item_id=queries[0].item_id,
                source_type="statute",
                source_id="law:001248:article:0003",
                title="주택임대차보호법 제3조",
                text="법령 원문",
                rank=1,
                similarity=0.9,
                metadata={},
            )
        ]


class FakeGenerator:
    def generate(self, queries, evidence):
        return [
            SpecialClauseGeneration(
                item_id=queries[0].item_id,
                status="draft_ready",
                draft_text="임대인은 계약기간 중 추가 담보권을 설정하지 않는다.",
                needs_input=[],
                evidence_source_ids=[evidence[0].source_id],
            )
        ]


class SpecialClauseGraphTests(unittest.TestCase):
    def test_redacts_identifier_and_contact_from_retrieval_query(self):
        result = SpecialClauseGraph().prepare(
            ["홍길동(900101-1234567)의 010-1234-5678 연락처를 특약에 넣고 싶어요."],
        )

        query = result.queries[0].query
        self.assertNotIn("900101-1234567", query)
        self.assertNotIn("010-1234-5678", query)
        self.assertIn("식별번호", query)
        self.assertIn("연락처 또는 계좌번호", query)

    def test_returns_evidence_bound_generation(self):
        result = SpecialClauseGraph().prepare(
            ["임대인이 계약 중 추가 담보권을 설정하지 못하게 하고 싶어요."],
            evidence_retriever=FakeEvidenceRetriever(),
            generator=FakeGenerator(),
        )

        self.assertFalse(result.generation_unavailable)
        self.assertEqual(result.generations[0].item_id, "request-1")
        self.assertEqual(
            result.generations[0].evidence_source_ids,
            ["law:001248:article:0003"],
        )


if __name__ == "__main__":
    unittest.main()
