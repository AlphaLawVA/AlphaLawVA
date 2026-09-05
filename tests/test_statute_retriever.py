# test_statute_retriever.py
"""
Description: BGE-M3 법령 검색기의 지연 로딩, DB 검증과 결과 변환을
외부 모델 다운로드 없이 검증한다.
Author: ooheunsu
Date: 2026-09-05
Before:
    - BGE-M3 운영 검색 모듈과 법령 DB 메타데이터 규칙이 정의된 상태.

After:
    - 단일·배치 검색, 입력 검증과 잘못된 DB 거부 동작이 검증됨.
"""

import unittest

from ml.embedding.build_bge_m3_statute_chroma import MODEL_SPEC
from ml.rag.statute_retriever import (
    BgeM3StatuteRetriever,
    validate_bge_store,
)


class FakeArray(list):
    def tolist(self):
        return list(self)


class FakeTokenizer:
    def encode(self, text, **_kwargs):
        return text.split()


class FakeModel:
    tokenizer = FakeTokenizer()
    max_seq_length = 8

    def __init__(self):
        self.calls = []

    def encode(self, queries, **kwargs):
        self.calls.append((queries, kwargs))
        return FakeArray([[0.0] * MODEL_SPEC.dimension for _ in queries])


class FakeCollection:
    metadata = {
        "hnsw:space": "cosine",
        "model_name": MODEL_SPEC.model_name,
        "model_revision": MODEL_SPEC.revision,
        "embedding_dimension": MODEL_SPEC.dimension,
        "normalize_embeddings": True,
        "chunk_schema_version": "0.2",
        "chunking_strategy": MODEL_SPEC.chunking_strategy,
        "source_sha256": "source-hash",
        "source_chunk_count": 8933,
    }

    def __init__(self):
        self.calls = []

    def count(self):
        return 8933

    def query(self, **kwargs):
        self.calls.append(kwargs)
        count = len(kwargs["query_embeddings"])
        return {
            "ids": [["law:001248:article:0003"] for _ in range(count)],
            "documents": [["법령 본문"] for _ in range(count)],
            "metadatas": [
                [
                    {
                        "law_id": "001248",
                        "law_name": "주택임대차보호법",
                        "article_label": "제3조",
                        "article_title": "대항력 등",
                        "source_node_id": "node-1",
                    }
                ]
                for _ in range(count)
            ],
            "distances": [[0.25] for _ in range(count)],
        }


def complete_manifest():
    return {
        "status": "complete",
        "model_name": MODEL_SPEC.model_name,
        "model_revision": MODEL_SPEC.revision,
        "embedding_dimension": MODEL_SPEC.dimension,
        "collection_name": MODEL_SPEC.collection_name,
        "normalize_embeddings": True,
        "chunk_schema_version": "0.2",
        "chunking_strategy": MODEL_SPEC.chunking_strategy,
        "source_sha256": "source-hash",
        "source_chunk_count": 8933,
        "stored_count": 8933,
    }


class BgeM3StatuteRetrieverTests(unittest.TestCase):
    def make_retriever(self):
        model = FakeModel()
        collection = FakeCollection()
        loads = {"model": 0, "collection": 0}

        def model_loader(_cache, _device):
            loads["model"] += 1
            return model

        def collection_loader(_db_dir):
            loads["collection"] += 1
            return object(), collection, complete_manifest()

        retriever = BgeM3StatuteRetriever(
            model_loader=model_loader,
            collection_loader=collection_loader,
        )
        return retriever, model, collection, loads

    def test_loads_once_and_returns_source_metadata(self):
        retriever, model, collection, loads = self.make_retriever()
        self.assertFalse(retriever.is_loaded)

        first = retriever.search("보증금을 돌려받지 못했습니다")
        second = retriever.search("집주인이 바뀌었습니다", top_k=5)

        self.assertEqual(loads, {"model": 1, "collection": 1})
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(collection.calls[1]["n_results"], 5)
        self.assertEqual(first[0].law_name, "주택임대차보호법")
        self.assertEqual(first[0].article_label, "제3조")
        self.assertEqual(first[0].similarity, 0.75)
        self.assertEqual(second[0].rank, 1)

    def test_search_many_preserves_query_groups(self):
        retriever, _, _, _ = self.make_retriever()

        results = retriever.search_many(["첫 질문", "둘째 질문"])

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][0].chunk_id, "law:001248:article:0003")

    def test_rejects_empty_query_and_invalid_top_k(self):
        retriever, _, _, _ = self.make_retriever()

        with self.assertRaisesRegex(ValueError, "비어 있지 않은"):
            retriever.search("  ")
        with self.assertRaisesRegex(ValueError, "1 이상"):
            retriever.search("질문", top_k=0)

    def test_rejects_query_over_model_limit(self):
        retriever, _, _, _ = self.make_retriever()

        with self.assertRaisesRegex(ValueError, "입력 한도"):
            retriever.search("하나 둘 셋 넷 다섯 여섯 일곱 여덟 아홉")

    def test_rejects_mismatched_collection(self):
        collection = FakeCollection()
        collection.metadata = {**collection.metadata, "model_name": "other"}

        with self.assertRaisesRegex(ValueError, "model_name"):
            validate_bge_store(collection, complete_manifest())

    def test_rejects_parent_child_collection_for_article_retriever(self):
        collection = FakeCollection()
        collection.metadata = {
            **collection.metadata,
            "chunking_strategy": "paragraph_child_article_parent_v01",
        }

        with self.assertRaisesRegex(ValueError, "chunking_strategy"):
            validate_bge_store(collection, complete_manifest())


if __name__ == "__main__":
    unittest.main()
