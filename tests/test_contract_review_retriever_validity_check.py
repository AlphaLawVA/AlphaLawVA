# test_contract_review_retriever_validity_check.py
"""
Description: 특약 효력(강행규정 위반 무효) 보조 질의가 주제 질의로는 못 찾는
조문을 추가로 붙이고, 중복·무관 카테고리에서는 끼어들지 않는지 검증한다.
Author: ooheunsu
Date: 2026-11-15
Before:
    - 특약 항목(special_term, special_clause_request)도 주제 질의 하나만으로
      법령 후보를 골라, 강행규정처럼 어휘가 안 겹치는 조문은 못 찾았다.
After:
    - 두 카테고리에서는 주제 질의 선정 결과에 없는 조문 중 보조 질의로 찾은
      것 하나를 추가로 붙인다. 다른 카테고리와 이미 선정된 조문은 그대로 둔다.
"""

import unittest
from types import SimpleNamespace

from ml.agent.contract_review_graph import ContractReviewRetrievalQuery
from ml.rag.contract_review_retriever import ContractReviewRagRetriever


def _statute(chunk_id, similarity, law_name="주택임대차보호법", article_label="제1조"):
    return SimpleNamespace(
        rank=1, chunk_id=chunk_id, law_id="1", law_name=law_name,
        article_label=article_label, article_title="t", text="본문", similarity=similarity,
        effective_date=None, source_node_id=None,
    )


class _TopicThenValidityStatuteSource:
    """첫 호출(주제 질의)은 topic_results를, 이후 호출(보조 질의)은 validity_results를 돌려준다."""

    def __init__(self, topic_results, validity_results):
        self._responses = [topic_results, validity_results]
        self.queries: list[str] = []

    def search(self, query, *, top_k=10):
        self.queries.append(query)
        index = min(len(self.queries) - 1, len(self._responses) - 1)
        return self._responses[index][:top_k]


class ValidityCheckEvidenceTest(unittest.TestCase):
    def test_adds_validity_article_not_in_topic_selection(self):
        topic = [_statute("law:topic", 0.55, article_label="제6조의3")]
        validity = [_statute("law:validity", 0.77, article_label="제10조")]
        source = _TopicThenValidityStatuteSource(topic, validity)
        retriever = ContractReviewRagRetriever(statute_retriever=source, statute_top_k=10)
        query = ContractReviewRetrievalQuery(item_id="request-1", category="special_clause_request", query="원문")

        evidence = retriever.retrieve([query])

        self.assertEqual([e.source_id for e in evidence], ["law:topic", "law:validity"])
        self.assertEqual(source.queries[1], "원문 특약이 임차인에게 불리하여 무효가 되는지, 강행규정 위반 여부")

    def test_does_not_duplicate_already_selected_article(self):
        same = _statute("law:same", 0.6)
        source = _TopicThenValidityStatuteSource([same], [same])
        retriever = ContractReviewRagRetriever(statute_retriever=source, statute_top_k=10)
        query = ContractReviewRetrievalQuery(item_id="term-1", category="special_term", query="원문")

        evidence = retriever.retrieve([query])

        self.assertEqual([e.source_id for e in evidence], ["law:same"])

    def test_validity_candidate_below_threshold_is_dropped(self):
        topic = [_statute("law:topic", 0.55)]
        weak = [_statute("law:weak", 0.1)]
        source = _TopicThenValidityStatuteSource(topic, weak)
        retriever = ContractReviewRagRetriever(statute_retriever=source, statute_top_k=10)
        query = ContractReviewRetrievalQuery(item_id="request-1", category="special_clause_request", query="원문")

        evidence = retriever.retrieve([query])

        self.assertEqual([e.source_id for e in evidence], ["law:topic"])

    def test_other_categories_skip_validity_check(self):
        topic = [_statute("law:topic", 0.55)]
        extra = [_statute("law:extra", 0.9)]
        source = _TopicThenValidityStatuteSource(topic, extra)
        retriever = ContractReviewRagRetriever(statute_retriever=source, statute_top_k=10)
        query = ContractReviewRetrievalQuery(item_id="registry-1", category="registry_field", query="원문")

        evidence = retriever.retrieve([query])

        self.assertEqual([e.source_id for e in evidence], ["law:topic"])
        self.assertEqual(len(source.queries), 1)


if __name__ == "__main__":
    unittest.main()
