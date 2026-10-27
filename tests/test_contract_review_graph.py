# test_contract_review_graph.py
"""
Description: 계약서 검토 LangGraph의 규칙 기반 사전검증과 비식별 검색 질의를 검증한다.
법령·판례 검색기나 LLM을 호출하지 않고 입력 경계와 분기 결과를 확인한다.
Author: ooheunsu
Date: 2026-10-27
Before:
    - 확정 계약서와 등본을 규칙으로 교차검증하는 그래프 테스트가 없는 상태.
After:
    - 주소·소유자·금액 불일치와 특약 검색 질의를 회귀 검증할 수 있음.
"""

import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from api.models.contract_snapshots import ConfirmedContractSnapshot
from api.models.registry_snapshots import ConfirmedRegistrySnapshot
from ml.agent.contract_review_graph import ContractReviewGraph
from ml.rag.contract_review_retriever import (
    ContractReviewRagRetriever,
    create_contract_review_rag_retriever_from_env,
)
from ml.rag.precedent_retriever import PrecedentChunkMatch, PrecedentSearchResult
from ml.rag.statute_retriever import StatuteSearchResult


class FakeStatuteRetriever:
    def __init__(self) -> None:
        self.queries: list[tuple[str, int]] = []

    def search(self, query: str, *, top_k: int = 10) -> list[StatuteSearchResult]:
        self.queries.append((query, top_k))
        return [
            StatuteSearchResult(
                rank=1,
                chunk_id="law:001248:article:0003",
                distance=0.1,
                similarity=0.9,
                text="주택임대차보호법 조문 원문",
                law_id="001248",
                law_name="주택임대차보호법",
                article_label="제3조",
                article_title="대항력 등",
                effective_date="2026-01-01",
                heading_path=None,
                source_node_id="law-node-3",
                metadata={},
            )
        ]


class FakePrecedentRetriever:
    def __init__(self) -> None:
        self.queries: list[tuple[str, int]] = []

    def search(self, query: str, *, top_k: int = 10) -> list[PrecedentSearchResult]:
        self.queries.append((query, top_k))
        return [
            PrecedentSearchResult(
                rank=1,
                precedent_id="64441",
                distance=0.2,
                similarity=0.8,
                text="판례 이유 원문",
                case_no="2006다62492",
                case_no_list="2006다62492",
                case_name="임대차보증금등",
                court_name="대법원",
                decision_date="2008-09-25",
                decision_year=2008,
                case_type="민사",
                judgment_type="판결",
                source_path=None,
                matched_chunks=[
                    PrecedentChunkMatch(
                        rank=1,
                        chunk_id="precedent:64441:reason:0001",
                        distance=0.2,
                        similarity=0.8,
                        text="판례 이유 원문",
                        chunk_type="reason",
                        section="reason",
                        section_chunk_index=1,
                        metadata={},
                    )
                ],
                metadata={},
            )
        ]


class ContractReviewGraphTests(unittest.TestCase):
    def _contract_snapshot(self) -> ConfirmedContractSnapshot:
        return ConfirmedContractSnapshot.model_validate(
            {
                "snapshot_id": str(uuid4()),
                "document_id": str(uuid4()),
                "confirmation": {
                    "confirmed_by_user": True,
                    "revision": 1,
                    "confirmed_at": datetime.now(UTC).isoformat(),
                },
                "contract": {
                    "contract_type": "jeonse",
                    "property": {
                        "address": "서울특별시 테스트구 테스트로 1",
                        "detail_address": "101동 1001호",
                    },
                    "parties": [
                        {"role": "lessor", "name": "임대인"},
                        {"role": "lessee", "name": "임차인"},
                    ],
                    "lease_deposit": {"amount": 200000000},
                    "down_payment": {"amount": 10000000},
                    "remaining_balance": {"amount": 180000000},
                    "start_date": "2026-11-01",
                    "end_date": "2028-10-31",
                    "special_terms": [
                        {
                            "term_id": "term-1",
                            "order": 1,
                            "original_text": (
                                "임대인는 서울특별시 테스트구 테스트로 1의 "
                                "추가 담보권을 설정하지 않는다."
                            ),
                            "source": {"page_number": 3},
                        }
                    ],
                },
            }
        )

    def _registry_snapshot(self) -> ConfirmedRegistrySnapshot:
        return ConfirmedRegistrySnapshot.model_validate(
            {
                "snapshot_id": str(uuid4()),
                "document_id": str(uuid4()),
                "confirmation": {
                    "confirmed_by_user": True,
                    "revision": 1,
                    "confirmed_at": datetime.now(UTC).isoformat(),
                },
                "issued_at": "2026-10-25T12:00:00+09:00",
                "address": "서울특별시 다른구 다른로 2",
                "owners": [{"name": "다른소유자", "status": "active"}],
                "encumbrances": [
                    {
                        "section": "eulgu",
                        "rank_number": "4",
                        "registration_purpose": "근저당권설정",
                        "raw_text": "채권최고액 180,000,000원",
                        "source": {"page_number": 1},
                        "status": "active",
                    }
                ],
            }
        )

    def test_builds_crosscheck_items_and_redacted_queries(self):
        result = ContractReviewGraph().prepare(
            self._contract_snapshot(),
            self._registry_snapshot(),
        )

        self.assertEqual(
            {item.item_id for item in result.items},
            {
                "property_address_match",
                "registered_owner_match",
                "payment_total_match",
                "registry_checked_disclosure",
            },
        )
        queries = {item.item_id: item.query for item in result.retrieval_queries}
        self.assertIn("term-1", queries)
        self.assertIn("registry_4", queries)
        self.assertNotIn("임대인", queries["term-1"])
        self.assertNotIn("테스트로 1", queries["term-1"])
        self.assertEqual(result.evidence, [])

    def test_preserves_statute_and_precedent_source_metadata(self):
        statute_retriever = FakeStatuteRetriever()
        precedent_retriever = FakePrecedentRetriever()
        evidence_retriever = ContractReviewRagRetriever(
            statute_retriever=statute_retriever,
            precedent_retriever=precedent_retriever,
            statute_top_k=2,
            precedent_top_k=2,
        )

        result = ContractReviewGraph().prepare(
            self._contract_snapshot(),
            self._registry_snapshot(),
            evidence_retriever=evidence_retriever,
        )

        self.assertGreater(len(result.evidence), 0)
        self.assertEqual(
            {item.source_type for item in result.evidence},
            {"statute", "precedent"},
        )
        statute = next(
            item for item in result.evidence if item.source_type == "statute"
        )
        precedent = next(
            item for item in result.evidence if item.source_type == "precedent"
        )
        self.assertEqual(statute.metadata["article_label"], "제3조")
        self.assertEqual(precedent.metadata["case_no"], "2006다62492")
        self.assertTrue(statute_retriever.queries)
        self.assertTrue(precedent_retriever.queries)

    def test_requests_registry_when_registry_snapshot_is_missing(self):
        result = ContractReviewGraph().prepare(self._contract_snapshot(), None)

        self.assertIn("registry_document", [item.item_id for item in result.items])

    def test_rag_retriever_is_opt_in_by_environment(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(create_contract_review_rag_retriever_from_env())

        with patch.dict(
            "os.environ",
            {"CONTRACT_REVIEW_RAG_ENABLED": "true"},
            clear=True,
        ):
            retriever = create_contract_review_rag_retriever_from_env()

        self.assertIsInstance(retriever, ContractReviewRagRetriever)


if __name__ == "__main__":
    unittest.main()
