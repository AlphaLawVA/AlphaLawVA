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


def _precedent(rank, similarity):
    return SimpleNamespace(
        rank=rank, precedent_id=f"prec:{rank}", case_no=f"case{rank}", case_name="사건",
        text=f"판례{rank}", similarity=similarity, court_name=None, decision_date=None,
        decision_year=None, case_type=None, judgment_type=None,
    )


QUERY = ContractReviewRetrievalQuery(item_id="term-1", category="special_term", query="질문")
STATUTES = [_statute(i, 0.8 - i * 0.03) for i in range(1, 11)]  # 유사도 0.77 ~ 0.50, 모두 기준(0.35) 이상
PRECEDENTS = [_precedent(i, -0.1 + i * 0.01) for i in range(1, 11)]  # 음수 포함, 판례는 최소 유사도가 없다


def _retriever(reranker=None, statute_top_k=10, source=None, precedent_reranker=None, precedent_top_k=10, precedent_source=None):
    return ContractReviewRagRetriever(
        statute_retriever=source or Fake(STATUTES),
        statute_top_k=statute_top_k,
        statute_reranker=reranker,
        precedent_retriever=precedent_source,
        precedent_top_k=precedent_top_k,
        precedent_reranker=precedent_reranker,
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


class PrecedentRerankerSelectionTest(unittest.TestCase):
    """판례도 법령과 같은 리랭커 경로를 타는지 검증한다(2026-11-14부터 판례에도 KURE 리랭커 적용)."""

    def test_without_reranker_dense_order_is_kept(self):
        evidence = _retriever(precedent_source=Fake(PRECEDENTS)).retrieve([QUERY])
        precedents = [e for e in evidence if e.source_type == "precedent"]
        self.assertEqual([e.source_id for e in precedents], ["prec:10", "prec:9"])

    def test_reranker_reorders_candidates_before_taking_top_two(self):
        reranker = FakeReranker()
        evidence = _retriever(
            precedent_source=Fake(PRECEDENTS), precedent_reranker=reranker
        ).retrieve([QUERY])
        precedents = [e for e in evidence if e.source_type == "precedent"]
        self.assertEqual([e.source_id for e in precedents], ["prec:10", "prec:9"])
        self.assertEqual(reranker.calls[0][0], "질문")
        self.assertEqual(len(reranker.calls[0][1]), 10)

    def test_negative_similarity_candidates_still_reach_the_reranker(self):
        # 판례는 최소 유사도가 없으므로 음수 유사도도 그대로 리랭커에 들어가야 한다.
        reranker = FakeReranker()
        _retriever(precedent_source=Fake(PRECEDENTS), precedent_reranker=reranker).retrieve([QUERY])
        self.assertEqual(reranker.calls[0][1], [f"prec:{i}" for i in range(10, 0, -1)])

    def test_reranker_failure_falls_back_to_dense_order(self):
        with self.assertLogs("ml.rag.contract_review_retriever", level="WARNING"):
            evidence = _retriever(
                precedent_source=Fake(PRECEDENTS), precedent_reranker=BrokenReranker()
            ).retrieve([QUERY])
        precedents = [e for e in evidence if e.source_type == "precedent"]
        self.assertEqual([e.source_id for e in precedents], ["prec:10", "prec:9"])


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

    def test_statute_and_precedent_rerankers_share_one_model_load(self):
        """계약서 검토·특약 생성 양쪽에서 만든 리랭커도 KURE 가중치는 한 번만 로딩해야 한다."""

        loader = SimpleNamespace(calls=0)

        def fake_loader(model_path, *, max_length, device, trust_remote_code):
            loader.calls += 1
            return SimpleNamespace()

        original_model = module._SHARED_CROSS_ENCODER_MODEL_LOADER._model
        module._SHARED_CROSS_ENCODER_MODEL_LOADER._model = None
        try:
            with patch.object(module._SHARED_CROSS_ENCODER_MODEL_LOADER, "_loader", fake_loader):
                with tempfile.TemporaryDirectory() as directory:
                    env = {"RAG_STATUTE_RERANKER_DIR": directory}
                    with patch.dict(os.environ, env, clear=False):
                        os.environ.pop("RAG_STATUTE_RERANKER_ENABLED", None)
                        contract_review_reranker = module._create_statute_reranker_from_env()
                        special_clause_reranker = module._create_statute_reranker_from_env()
                        contract_review_reranker.load()
                        special_clause_reranker.load()
        finally:
            module._SHARED_CROSS_ENCODER_MODEL_LOADER._model = original_model
        self.assertEqual(loader.calls, 1)

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
        self.assertEqual(with_reranker._precedent_top_k, 10)
        # 법령·판례 리랭커는 같은 KURE-Reranker-nano 인스턴스를 공유한다.
        self.assertIs(with_reranker._precedent_reranker, with_reranker._statute_reranker)
        self.assertEqual(without._statute_top_k, 3)
        self.assertIsNone(without._statute_reranker)
        self.assertEqual(without._precedent_top_k, 3)
        self.assertIsNone(without._precedent_reranker)


if __name__ == "__main__":
    unittest.main()
