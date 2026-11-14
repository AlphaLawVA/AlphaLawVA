# test_contract_review_retriever_quota.py
"""
Description: 계약서 검토 근거 선택이 법령과 판례를 종류별 기준·개수로 고르는지 검증한다.
Author: ooheunsu
Date: 2026-11-06
Before:
    - 법령과 판례를 하나의 유사도 순위로 섞어 판례가 항상 밀려나던 상태.
After:
    - 유사도가 낮은 판례도 별도 기준을 넘으면 법령 옆에 함께 전달됨.
"""

import unittest
from types import SimpleNamespace

from ml.agent.contract_review_graph import ContractReviewRetrievalQuery
from ml.rag.contract_review_retriever import ContractReviewRagRetriever


def _statute(rank, similarity):
    return SimpleNamespace(
        rank=rank, chunk_id=f"law:{rank}", law_id="1", law_name="주택임대차보호법",
        article_label=f"제{rank}조", article_title="t", text="법령", similarity=similarity,
        effective_date=None, source_node_id=None,
    )


def _precedent(rank, similarity):
    return SimpleNamespace(
        rank=rank, precedent_id=f"p:{rank}", case_name="사건", case_no=f"20{rank}다1", text="판례",
        similarity=similarity, court_name=None, decision_date=None, decision_year=None,
        case_type=None, judgment_type=None,
    )


class Fake:
    def __init__(self, results):
        self._results = results

    def search(self, query, *, top_k=10):
        return self._results[:top_k]


QUERY = ContractReviewRetrievalQuery(item_id="term-1", category="special_term", query="q")


class QuotaTest(unittest.TestCase):
    def _retrieve(self, precedents, **kwargs):
        retriever = ContractReviewRagRetriever(
            statute_retriever=Fake([_statute(1, 0.72), _statute(2, 0.65), _statute(3, 0.6), _statute(4, 0.58)]),
            precedent_retriever=Fake(precedents),
            statute_top_k=4, precedent_top_k=4, **kwargs,
        )
        return retriever.retrieve([QUERY])

    def test_low_similarity_precedents_are_kept_with_own_threshold(self):
        evidence = self._retrieve([_precedent(1, 0.27), _precedent(2, 0.24), _precedent(3, 0.22)])
        kinds = [e.source_type for e in evidence]
        self.assertEqual(kinds.count("statute"), 3)
        self.assertEqual(kinds.count("precedent"), 2)  # 쿼터 2개

    def test_precedents_have_no_minimum_similarity_by_default(self):
        evidence = self._retrieve([_precedent(1, 0.15), _precedent(2, -0.1), _precedent(3, 0.05)])
        precedents = [e for e in evidence if e.source_type == "precedent"]
        self.assertEqual([e.source_id for e in precedents], ["p:1", "p:3"])  # 유사도 상위 2개, 기준 없음

    def test_explicit_precedent_threshold_still_drops_low_similarity(self):
        evidence = self._retrieve(
            [_precedent(1, 0.15), _precedent(2, -0.1)], precedent_min_similarity=0.2
        )
        self.assertNotIn("precedent", [e.source_type for e in evidence])

    def test_statute_threshold_does_not_apply_to_precedents(self):
        evidence = self._retrieve([_precedent(1, 0.3)], min_similarity=0.9)
        self.assertEqual([e.source_type for e in evidence], ["precedent"])


if __name__ == "__main__":
    unittest.main()
