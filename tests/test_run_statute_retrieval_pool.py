# test_run_statute_retrieval_pool.py
"""
Description: 법령 검색 후보 풀링 실행기의 데이터 검증, 중복 제거,
블라인드 순서와 Chroma 검색 결과 변환을 외부 모델 호출 없이 테스트한다.
Author: ooheunsu
Date: 2026-08-31
Before:
    - 세 임베딩 모델의 검색 결과를 통합하는 평가 실행기가 작성된 상태.

After:
    - 모델 다운로드와 API 과금 없이 후보 검수표 생성 규칙을 검증 가능.
"""

import json
import tempfile
import unittest
from pathlib import Path

from ml.evaluation.run_statute_retrieval_pool import (
    MODEL_CONFIGS,
    build_blind_pool,
    load_cases,
    query_collection,
    selected_configs,
    validate_collections,
)


class FakeCollection:
    def __init__(self, metadata=None):
        self.metadata = metadata or {}

    def count(self):
        return 1

    def query(self, **_kwargs):
        return {
            "ids": [["law:001248:article:0003"]],
            "documents": [["법령 본문"]],
            "metadatas": [[{"law_name": "주택임대차보호법"}]],
            "distances": [[0.1]],
        }


def sample_case(query_id="statute_retrieval_v01_q001"):
    return {
        "query_id": query_id,
        "question": "질문",
        "purpose": "목적",
        "category": "direct_fact",
        "critical": False,
        "dataset_snapshot": {"source_sha256": "source-hash"},
    }


def candidate(chunk_id, law_name="법령"):
    return {
        "rank": 1,
        "chunk_id": chunk_id,
        "distance": 0.1,
        "document": "본문",
        "metadata": {
            "law_name": law_name,
            "article_label": "제1조",
            "article_title": "목적",
        },
    }


class RunStatuteRetrievalPoolTests(unittest.TestCase):
    def test_loads_jsonl_and_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cases.jsonl"
            row = {"query_id": "q1", "question": "질문"}
            path.write_text(
                json.dumps(row) + "\n" + json.dumps(row) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "중복 query_id"):
                load_cases(path)

    def test_rejects_unknown_model_key(self):
        with self.assertRaisesRegex(ValueError, "지원하지 않는 모델"):
            selected_configs(["unknown"])

    def test_validates_collection_source_and_model_metadata(self):
        config = MODEL_CONFIGS["kure_v1"]
        metadata = {
            "model_name": config.model_name,
            "model_revision": config.revision,
            "embedding_dimension": config.dimension,
            "source_sha256": "source-hash",
        }
        collection = FakeCollection(metadata)

        result = validate_collections(
            [config],
            [sample_case()],
            collection_loader=lambda _config: collection,
        )

        self.assertIs(result[config.key], collection)

    def test_rejects_collection_from_another_corpus(self):
        config = MODEL_CONFIGS["kure_v1"]
        collection = FakeCollection(
            {
                "model_name": config.model_name,
                "model_revision": config.revision,
                "embedding_dimension": config.dimension,
                "source_sha256": "other-hash",
            }
        )
        with self.assertRaisesRegex(ValueError, "source_sha256 불일치"):
            validate_collections(
                [config],
                [sample_case()],
                collection_loader=lambda _config: collection,
            )

    def test_converts_collection_query_to_ranked_rows(self):
        rankings = query_collection(
            FakeCollection(),
            [[0.0]],
            ["q1"],
            top_k=1,
        )

        self.assertEqual(rankings["q1"][0]["rank"], 1)
        self.assertEqual(
            rankings["q1"][0]["chunk_id"],
            "law:001248:article:0003",
        )

    def test_blind_pool_deduplicates_and_hides_model_origin(self):
        rankings = {
            "model_a": {
                "statute_retrieval_v01_q001": [candidate("chunk-a")]
            },
            "model_b": {
                "statute_retrieval_v01_q001": [
                    candidate("chunk-a"),
                    candidate("chunk-b"),
                ]
            },
        }

        pool = build_blind_pool([sample_case()], rankings)

        self.assertEqual(len(pool[0]["candidates"]), 2)
        self.assertNotIn("model", pool[0]["candidates"][0])
        self.assertNotIn("rank", pool[0]["candidates"][0])
        self.assertIsNone(pool[0]["candidates"][0]["relevance"])

    def test_blind_order_is_stable(self):
        rankings = {
            "model": {
                "statute_retrieval_v01_q001": [
                    candidate("chunk-a"),
                    candidate("chunk-b"),
                ]
            }
        }

        first = build_blind_pool([sample_case()], rankings)
        second = build_blind_pool([sample_case()], rankings)

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
