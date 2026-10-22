# test_certified_letter_retrieval.py
"""기존 BGE 검색 결과의 내용증명 근거 변환과 중복 제거를 검증한다."""

import unittest
from pathlib import Path
from types import SimpleNamespace

from ml.agent.certified_letter.retrieval import (
    BgeM3PrecedentSearchAdapter,
    BgeM3StatuteSearchAdapter,
)
from ml.rag.precedent_retriever import PrecedentChunkMatch, PrecedentSearchResult
from ml.rag.reranker import CrossEncoderReranker, DenseSimilarityReranker
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


def make_precedent(
    *,
    precedent_id: str,
    similarity: float,
    matched_texts: tuple[str, ...] = (),
):
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
        matched_chunks=[
            PrecedentChunkMatch(
                rank=index,
                chunk_id=f"precedent:{precedent_id}:reason:{index:04d}",
                distance=1.0 - similarity,
                similarity=similarity,
                text=text,
                chunk_type="이유",
                section="이유",
                section_chunk_index=index - 1,
                metadata={"precedent_id": precedent_id},
            )
            for index, text in enumerate(matched_texts, start=1)
        ],
        metadata={"easy_summary": "보증금 반환과 관련된 판례입니다."},
    )


class FakeSearchMany:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search_many(self, queries, *, top_k):
        self.calls.append((tuple(queries), top_k))
        return self.results


class FakeCrossEncoder:
    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    def predict(self, pairs, *, batch_size, show_progress_bar):
        self.calls.append((pairs, batch_size, show_progress_bar))
        return self.scores


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
        high = make_precedent(
            precedent_id="64441",
            similarity=0.90,
            matched_texts=("첫 번째 적중 청크", "두 번째 적중 청크"),
        )
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
        self.assertEqual(
            results[0].rerank_texts,
            ("첫 번째 적중 청크", "두 번째 적중 청크"),
        )

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

    def test_cross_encoder_reranker_loads_lazily_and_uses_model_scores(self):
        candidates = [
            make_precedent(precedent_id="1", similarity=0.9),
            make_precedent(precedent_id="2", similarity=0.5),
            make_precedent(precedent_id="3", similarity=0.7),
        ]
        model = FakeCrossEncoder([0.1, 0.95, 0.4])
        loader_calls = []

        def load_model(path, **options):
            loader_calls.append((path, options))
            return model

        reranker = CrossEncoderReranker(
            model_path=Path("/models/bge-reranker-v2-m3"),
            batch_size=2,
            device="cpu",
            model_loader=load_model,
        )

        self.assertFalse(reranker.is_loaded)
        results = reranker.rerank("보증금 반환", candidates, top_k=2)

        self.assertTrue(reranker.is_loaded)
        self.assertEqual(
            [result.precedent_id for result in results],
            ["2", "3"],
        )
        self.assertEqual(len(loader_calls), 1)
        self.assertEqual(model.calls[0][1:], (2, False))

        reranker.rerank("보증금 반환", candidates, top_k=1)
        self.assertEqual(len(loader_calls), 1)

    def test_cross_encoder_uses_best_score_across_precedent_chunks(self):
        candidates = [
            SimpleNamespace(
                precedent_id="1",
                similarity=0.9,
                text="대표 청크 1",
                rerank_texts=("청크 1-A", "청크 1-B"),
            ),
            SimpleNamespace(
                precedent_id="2",
                similarity=0.8,
                text="대표 청크 2",
                rerank_texts=("청크 2-A",),
            ),
        ]
        model = FakeCrossEncoder([0.1, 0.95, 0.7])
        reranker = CrossEncoderReranker(
            model_path=Path("/models/bge-reranker-v2-m3"),
            model_loader=lambda *_args, **_kwargs: model,
        )

        results = reranker.rerank("보증금 반환", candidates, top_k=2)

        self.assertEqual([result.precedent_id for result in results], ["1", "2"])
        self.assertEqual(len(model.calls[0][0]), 3)


if __name__ == "__main__":
    unittest.main()
