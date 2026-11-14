# test_contract_review_retriever_reranker.py
"""
Description: 법령 리랭커가 후보를 재정렬해 상위를 고르고, 장애·비활성·모델 없음에서는 dense 순서로 돌아가는지 검증한다.
Author: ooheunsu
Date: 2026-11-12
Before:
    - 법령 근거를 dense 유사도 순서로만 골랐다.
After:
    - 리랭커가 있으면 후보 10개를 재정렬한 상위 3개를 쓰고, 없거나 실패하면 이전 동작을 유지한다.
"""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ml.agent.contract_review_graph import ContractReviewRetrievalQuery
from ml.rag import contract_review_retriever as module
from ml.rag.contract_review_retriever import ContractReviewRagRetriever
from ml.rag.reranker import CrossEncoderReranker


def _statute(rank, similarity):
    return SimpleNamespace(
        rank=rank, chunk_id=f"law:{rank}", law_id="1", law_name="주택임대차보호법",
        article_label=f"제{rank}조", article_title="t", text=f"법령{rank}", similarity=similarity,
        effective_date=None, source_node_id=None,
    )


class Fake:
    def __init__(self, results):
        self._results = results
        self.requested_top_k = None

    def search(self, query, *, top_k=10):
        self.requested_top_k = top_k
        return self._results[:top_k]


class FakeReranker:
    """source_id의 숫자가 큰 후보를 먼저 두는 가짜 리랭커다."""

    def __init__(self):
        self.calls = []

    def rerank(self, query, candidates, *, top_k):
        self.calls.append((query, [c.source_id for c in candidates], top_k))
        return sorted(candidates, key=lambda c: int(c.source_id.split(":")[1]), reverse=True)[:top_k]


class BrokenReranker:
    def rerank(self, query, candidates, *, top_k):
        raise RuntimeError("모델 오류")


QUERY = ContractReviewRetrievalQuery(item_id="term-1", category="special_term", query="질문")
STATUTES = [_statute(i, 0.8 - i * 0.03) for i in range(1, 11)]  # 유사도 0.77 ~ 0.50, 모두 기준(0.35) 이상


def _retriever(reranker=None, statute_top_k=10, source=None):
    return ContractReviewRagRetriever(
        statute_retriever=source or Fake(STATUTES),
        statute_top_k=statute_top_k,
        statute_reranker=reranker,
    )


class RerankerSelectionTest(unittest.TestCase):
    def test_without_reranker_dense_order_is_kept(self):
        evidence = _retriever().retrieve([QUERY])
        self.assertEqual([e.source_id for e in evidence], ["law:1", "law:2", "law:3"])

    def test_reranker_reorders_candidates_before_taking_top_three(self):
        reranker = FakeReranker()
        evidence = _retriever(reranker).retrieve([QUERY])
        self.assertEqual([e.source_id for e in evidence], ["law:10", "law:9", "law:8"])
        self.assertEqual(reranker.calls[0][0], "질문")
        self.assertEqual(len(reranker.calls[0][1]), 10)

    def test_similarity_threshold_runs_before_reranking(self):
        low = STATUTES[:2] + [_statute(i, 0.1) for i in range(3, 11)]
        reranker = FakeReranker()
        evidence = _retriever(reranker, source=Fake(low)).retrieve([QUERY])
        self.assertEqual(reranker.calls[0][1], ["law:1", "law:2"])  # 0.35 미만은 리랭커에 가지 않는다
        self.assertEqual([e.source_id for e in evidence], ["law:2", "law:1"])

    def test_single_candidate_skips_reranker(self):
        reranker = FakeReranker()
        evidence = _retriever(reranker, source=Fake(STATUTES[:1])).retrieve([QUERY])
        self.assertEqual([e.source_id for e in evidence], ["law:1"])
        self.assertEqual(reranker.calls, [])

    def test_reranker_failure_falls_back_to_dense_order(self):
        with self.assertLogs("ml.rag.contract_review_retriever", level="WARNING"):
            evidence = _retriever(BrokenReranker()).retrieve([QUERY])
        self.assertEqual([e.source_id for e in evidence], ["law:1", "law:2", "law:3"])


class RerankerEnvTest(unittest.TestCase):
    def test_enabled_by_default_when_model_directory_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {"RAG_STATUTE_RERANKER_DIR": directory}
            with patch.dict(os.environ, env, clear=False):
                os.environ.pop("RAG_STATUTE_RERANKER_ENABLED", None)
                reranker = module._create_statute_reranker_from_env()
        self.assertIsInstance(reranker, CrossEncoderReranker)
        self.assertEqual(reranker.model_path, Path(directory))
        self.assertFalse(reranker.is_loaded)  # 지연 로딩

    def test_disabled_by_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {"RAG_STATUTE_RERANKER_DIR": directory, "RAG_STATUTE_RERANKER_ENABLED": "false"}
            with patch.dict(os.environ, env, clear=False):
                self.assertIsNone(module._create_statute_reranker_from_env())

    def test_missing_model_directory_disables_reranker_with_warning(self):
        env = {"RAG_STATUTE_RERANKER_DIR": "Z:/없는/모델/폴더"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("RAG_STATUTE_RERANKER_ENABLED", None)
            with self.assertLogs("ml.rag.contract_review_retriever", level="WARNING"):
                self.assertIsNone(module._create_statute_reranker_from_env())

    def test_retriever_factory_widens_candidates_only_with_reranker(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {"SPECIAL_CLAUSE_RAG_ENABLED": "true", "RAG_STATUTE_RERANKER_DIR": directory}
            with patch.dict(os.environ, env, clear=False):
                os.environ.pop("RAG_STATUTE_RERANKER_ENABLED", None)
                with_reranker = module.create_special_clause_rag_retriever_from_env()
                os.environ["RAG_STATUTE_RERANKER_ENABLED"] = "false"
                without = module.create_special_clause_rag_retriever_from_env()
        self.assertEqual(with_reranker._statute_top_k, 10)
        self.assertIsNotNone(with_reranker._statute_reranker)
        self.assertEqual(without._statute_top_k, 3)
        self.assertIsNone(without._statute_reranker)


if __name__ == "__main__":
    unittest.main()
