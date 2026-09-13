# test_build_bge_m3_statute_chroma.py
"""
Description: BGE-M3 법령 임베딩 구축기가 고정된 모델 사양과 별도 ChromaDB
경로를 사용하며 RunPod 다운로드 환경 오류를 우회하는지 테스트한다.
Author: ooheunsu
Date: 2026-08-30
Before:
    - BGE-M3 모델 사양과 공통 법령 ChromaDB 구축 코드가 작성된 상태.

After:
    - 실제 모델 다운로드 없이 BGE-M3 구축 설정과 RunPod 호환성을 검증 가능.
"""

import os
import unittest
from unittest.mock import patch

from ml.embedding.build_bge_m3_statute_chroma import (
    COLLECTION_NAME,
    DEFAULT_DB_DIR,
    MODEL_DIMENSION,
    MODEL_MAX_TOKENS,
    MODEL_NAME,
    MODEL_REVISION,
    MODEL_SPEC,
)
from ml.embedding.statute_chroma_builder import (
    disable_unavailable_hf_transfer,
    expected_collection_metadata,
)


class BuildBgeM3StatuteChromaTests(unittest.TestCase):
    def test_uses_pinned_dense_model_configuration(self):
        self.assertEqual(MODEL_NAME, "BAAI/bge-m3")
        self.assertEqual(
            MODEL_REVISION,
            "5617a9f61b028005a4858fdac845db406aefb181",
        )
        self.assertEqual(MODEL_DIMENSION, 1024)
        self.assertEqual(MODEL_MAX_TOKENS, 8192)
        self.assertIn("dense", COLLECTION_NAME)
        self.assertEqual(DEFAULT_DB_DIR.name, "bge_m3")

    def test_records_bge_model_in_collection_metadata(self):
        metadata = expected_collection_metadata(
            "corpus-hash",
            8933,
            MODEL_REVISION,
            MODEL_SPEC,
        )

        self.assertEqual(metadata["model_name"], MODEL_NAME)
        self.assertEqual(metadata["embedding_dimension"], 1024)
        self.assertEqual(metadata["source_chunk_count"], 8933)
        self.assertEqual(metadata["hnsw:space"], "cosine")

    def test_disables_missing_runpod_fast_download_package(self):
        with (
            patch.dict(
                os.environ,
                {"HF_HUB_ENABLE_HF_TRANSFER": "1"},
                clear=False,
            ),
            patch(
                "ml.embedding.statute_chroma_builder.importlib.util.find_spec",
                return_value=None,
            ),
        ):
            changed = disable_unavailable_hf_transfer()

            self.assertTrue(changed)
            self.assertNotIn("HF_HUB_ENABLE_HF_TRANSFER", os.environ)


if __name__ == "__main__":
    unittest.main()
