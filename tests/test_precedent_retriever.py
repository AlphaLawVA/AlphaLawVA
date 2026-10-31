# test_precedent_retriever.py
"""
Description: BGE-M3 판례 검색기의 지연 로딩, DB 검증과 판례 단위 결과 묶기를
외부 모델 다운로드 없이 검증한다.
Author: choeminju
Date: 2026-09-14
Before:
    - 판례 BGE-M3 운영 검색 모듈과 판례 DB 메타데이터 규칙이 정의된 상태.
After:
    - 단일·배치 검색, 입력 검증, 잘못된 DB 거부, 판례 단위 grouping 동작이 검증됨.
"""

import unittest

from ml.rag.precedent_retriever import (
    BGE_M3_DIMENSION,
    BGE_M3_MODEL_NAME,
    DEFAULT_CHUNKING_STRATEGY,
    DEFAULT_COLLECTION_NAME,
    BgeM3PrecedentRetriever,
    validate_precedent_bge_store,
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
        return FakeArray([[0.0] * BGE_M3_DIMENSION for _ in queries])


class FakeCollection:
    metadata = {
        "chunking_strategy": DEFAULT_CHUNKING_STRATEGY,
        "embedding_model": BGE_M3_MODEL_NAME,
    }

    def __init__(self):
        self.calls = []

    @property
    def name(self):
        return DEFAULT_COLLECTION_NAME

    def count(self):
        return 24267

    def query(self, **kwargs):
        self.calls.append(kwargs)
        count = len(kwargs["query_embeddings"])
        ids = [
            [
                "precedent:111:reason:0001",
                "precedent:111:summary:0001",
                "precedent:222:reason:0001",
            ]
            for _ in range(count)
        ]
        documents = [
            [
                "첫 번째 판례 이유 청크",
                "첫 번째 판례 생성요약 청크",
                "두 번째 판례 이유 청크",
            ]
            for _ in range(count)
        ]
        metadatas = [
            [
                {
                    "precedent_id": "111",
                    "case_no": "2020다111",
                    "case_no_list": "2020다111",
                    "case_name": "임대차보증금반환",
                    "court_name": "대법원",
                    "decision_date": "2024-01-01",
                    "decision_year": 2024,
                    "case_type": "민사",
                    "judgment_type": "판결",
                    "source_path": "local_data/precedents/processed/final_cases/111.json",
                    "section": "이유",
                    "chunk_type": "이유",
                    "section_chunk_index": 1,
                },
                {
                    "precedent_id": "111",
                    "case_no": "2020다111",
                    "case_name": "임대차보증금반환",
                    "court_name": "대법원",
                    "decision_date": "2024-01-01",
                    "decision_year": 2024,
                    "section": "생성요약",
                    "chunk_type": "생성요약",
                    "section_chunk_index": 1,
                },
                {
                    "precedent_id": "222",
                    "case_no": "2021다222",
                    "case_name": "건물인도",
                    "court_name": "서울중앙지방법원",
                    "decision_date": "2023-02-02",
                    "decision_year": 2023,
                    "section": "이유",
                    "chunk_type": "이유",
                    "section_chunk_index": 1,
                },
            ]
            for _ in range(count)
        ]
        distances = [[0.2, 0.1, 0.35] for _ in range(count)]
        return {
            "ids": ids,
            "documents": documents,
            "metadatas": metadatas,
            "distances": distances,
        }


def complete_manifest():
    return {
        "schema_version": "precedent_vector_db_manifest.v1",
        "chunking_strategy": DEFAULT_CHUNKING_STRATEGY,
        "embedding": "bge-m3",
        "embedding_provider": "sentence_transformers",
        "embedding_model": BGE_M3_MODEL_NAME,
        "collection": DEFAULT_COLLECTION_NAME,
        "stored_chunks": 24267,
    }


class BgeM3PrecedentRetrieverTests(unittest.TestCase):
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

        retriever = BgeM3PrecedentRetriever(
            model_loader=model_loader,
            collection_loader=collection_loader,
        )
        return retriever, model, collection, loads

    def test_loads_once_and_groups_chunks_by_precedent_id(self):
        retriever, model, collection, loads = self.make_retriever()
        self.assertFalse(retriever.is_loaded)

        first = retriever.search("보증금을 돌려받지 못했습니다")
        second = retriever.search("집주인이 바뀌었습니다", top_k=1)

        self.assertEqual(loads, {"model": 1, "collection": 1})
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(collection.calls[1]["n_results"], 5)
        self.assertEqual(len(first), 2)
        self.assertEqual(first[0].precedent_id, "111")
        self.assertEqual(first[0].case_name, "임대차보증금반환")
        self.assertEqual(first[0].distance, 0.1)
        self.assertEqual(first[0].similarity, 0.9)
        self.assertEqual(len(first[0].matched_chunks), 2)
        self.assertEqual(first[0].matched_chunks[0].chunk_id, "precedent:111:reason:0001")
        self.assertEqual(second[0].rank, 1)
        self.assertEqual(len(second), 1)

    def test_search_many_preserves_query_groups(self):
        retriever, _, _, _ = self.make_retriever()

        results = retriever.search_many(["첫 질문", "둘째 질문"], top_k=2)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][0].precedent_id, "111")
        self.assertEqual(results[1][1].precedent_id, "222")

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

    def test_rejects_invalid_candidate_multiplier(self):
        with self.assertRaisesRegex(ValueError, "candidate_multiplier"):
            BgeM3PrecedentRetriever(candidate_multiplier=0)

    def test_rejects_mismatched_collection(self):
        collection = FakeCollection()
        collection.metadata = {**collection.metadata, "embedding_model": "other"}

        with self.assertRaisesRegex(ValueError, "embedding_model"):
            validate_precedent_bge_store(collection, complete_manifest())

    def test_rejects_mismatched_manifest(self):
        collection = FakeCollection()
        manifest = {**complete_manifest(), "collection": "other"}

        with self.assertRaisesRegex(ValueError, "collection"):
            validate_precedent_bge_store(collection, manifest)


if __name__ == "__main__":
    unittest.main()
