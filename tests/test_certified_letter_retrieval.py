# test_certified_letter_retrieval.py
"""기존 BGE 검색 결과의 내용증명 근거 변환과 중복 제거를 검증한다."""

import unittest

from ml.agent.certified_letter.retrieval import (
    BgeM3PrecedentSearchAdapter,
    BgeM3StatuteSearchAdapter,
)
from ml.rag.precedent_retriever import PrecedentSearchResult
from ml.rag.reranker import DenseSimilarityReranker
from ml.rag.statute_retriever import StatuteSearchResult


def make_statute(
    *,
    chunk_id: str,
    similarity: float,
    law_name: str | None = "주택임대차보호법",
    article_label: str | None = "제4조",
):
    return StatuteSearchResult(
        rank=1,
        chunk_id=chunk_id,
        distance=1.0 - similarity,
        similarity=similarity,
        text="임대차 기간과 보증금 반환에 관한 조문",
        law_id="001",
        law_name=law_name,
        article_label=article_label,
        article_title="임대차기간 등",
        effective_date="2026-01-01",
        heading_path=None,
        source_node_id="law:001:article:4",
        metadata={"source_url": "https://law.go.kr/example"},
    )


def make_precedent(*, precedent_id: str, similarity: float):
    return PrecedentSearchResult(
        rank=1,
        precedent_id=precedent_id,
        distance=1.0 - similarity,
        similarity=similarity,
        text="임대차 종료와 보증금 반환이 문제 된 판례",
        case_no="2006다62492",
        case_no_list="2006다62492",
        case_name="임대차보증금등",
        court_name="대법원",
        decision_date="2008-09-25",
        decision_year=2008,
        case_type="민사",
        judgment_type="판결",
        source_path="precedents/64441.json",
        matched_chunks=[],
        metadata={"easy_summary": "보증금 반환과 관련된 판례입니다."},
    )


class FakeSearchMany:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search_many(self, queries, *, top_k):
        self.calls.append((tuple(queries), top_k))
        return self.results


class CertifiedLetterRetrievalTests(unittest.TestCase):
    def test_statute_adapter_merges_same_article_and_keeps_best_score(self):
        low = make_statute(chunk_id="law:001:4:a", similarity=0.70)
        high = make_statute(chunk_id="law:001:4:b", similarity=0.93)
        invalid = make_statute(
            chunk_id="law:invalid",
            similarity=0.99,
            law_name=None,
        )
        backend = FakeSearchMany([[low, invalid], [high]])
        adapter = BgeM3StatuteSearchAdapter(backend)

        results = adapter.search(["질의 1", "질의 2"], candidate_limit=20)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].chunk_id, "law:001:4:b")
        self.assertEqual(results[0].statute_id, "001")
        self.assertEqual(results[0].source_node_id, "law:001:article:4")
        self.assertEqual(backend.calls, [(('질의 1', '질의 2'), 20)])

    def test_precedent_adapter_merges_same_precedent_and_preserves_source(self):
        low = make_precedent(precedent_id="64441", similarity=0.72)
        high = make_precedent(precedent_id="64441", similarity=0.90)
        other = make_precedent(precedent_id="75552", similarity=0.85)
        adapter = BgeM3PrecedentSearchAdapter(
            FakeSearchMany([[low, other], [high]])
        )

        results = adapter.search(["질의 1", "질의 2"], candidate_limit=20)

        self.assertEqual(
            [result.precedent_id for result in results],
            ["64441", "75552"],
        )
        self.assertEqual(results[0].summary, "보증금 반환과 관련된 판례입니다.")
        self.assertTrue(results[0].source_url.endswith("precSeq=64441"))

    def test_dense_similarity_reranker_is_stable_baseline(self):
        candidates = [
            make_precedent(precedent_id="1", similarity=0.4),
            make_precedent(precedent_id="2", similarity=0.9),
            make_precedent(precedent_id="3", similarity=0.7),
        ]

        results = DenseSimilarityReranker().rerank(
            "보증금 반환",
            candidates,
            top_k=2,
        )

        self.assertEqual(
            [result.precedent_id for result in results],
            ["2", "3"],
        )
        with self.assertRaises(ValueError):
            DenseSimilarityReranker().rerank("질의", candidates, top_k=0)


if __name__ == "__main__":
    unittest.main()
