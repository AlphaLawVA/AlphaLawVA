# test_build_kure_statute_chroma.py
"""
Description: KURE-v1 법령 임베딩 구축기가 원본 해시와 Chroma 메타데이터를
고정하고 GPU 메모리 부족 시 배치를 안전하게 분할하는지 테스트한다.
Author: ooheunsu
Date: 2026-08-30
Before:
    - KURE-v1 기반 법령 ChromaDB 구축 코드가 작성된 상태.

After:
    - 실제 모델 다운로드 없이 구축 전 검증과 재시작 안전성을 확인 가능.
"""

import json
import tempfile
import unittest
from pathlib import Path

from ml.embedding.build_kure_statute_chroma import (
    MODEL_DIMENSION,
    chroma_metadata,
    corpus_sha256,
    embedding_rows,
    encode_with_oom_split,
    previous_maximum_token_count,
    validate_collection_metadata,
)


SAMPLE_CHUNK = {
    "chunk_id": "law:001248:article:0003:part:01",
    "source_node_id": "law:001248:article:0003",
    "chunk_type": "article_part",
    "retrieval_text": "법령: 주택임대차보호법\n조문: 제3조(대항력)",
    "metadata": {
        "law_id": "001248",
        "law_name": "주택임대차보호법",
        "article_label": "제3조",
        "article_title": "대항력",
        "heading_path": ["제2장 주택임대차의 효력"],
        "effective_date": "20260102",
        "contains_excluded_image": False,
        "part_index": 1,
        "part_count": 2,
        "paragraph_orders": [1],
    },
}


class OomOnceModel:
    def encode(self, texts, **_kwargs):
        if len(texts) > 1:
            raise RuntimeError("CUDA out of memory")
        return [[float(len(texts[0]))] * MODEL_DIMENSION]


class BuildKureStatuteChromaTests(unittest.TestCase):
    def test_corpus_digest_changes_when_text_changes(self):
        first = corpus_sha256([SAMPLE_CHUNK])
        changed = {**SAMPLE_CHUNK, "retrieval_text": "변경된 본문"}

        self.assertNotEqual(first, corpus_sha256([changed]))

    def test_flattens_chroma_metadata(self):
        metadata = chroma_metadata(SAMPLE_CHUNK)

        self.assertEqual(
            metadata["heading_path"],
            "제2장 주택임대차의 효력",
        )
        self.assertEqual(metadata["paragraph_orders"], "1")
        self.assertNotIn("unused", metadata)

    def test_flattens_parent_child_metadata(self):
        chunk = {
            **SAMPLE_CHUNK,
            "metadata": {
                **SAMPLE_CHUNK["metadata"],
                "parent_article_id": "law:001248:article:0003",
                "paragraph_order": 4,
                "paragraph_label": "④",
            },
        }

        metadata = chroma_metadata(chunk)

        self.assertEqual(
            metadata["parent_article_id"],
            "law:001248:article:0003",
        )
        self.assertEqual(metadata["paragraph_order"], 4)
        self.assertEqual(metadata["paragraph_label"], "④")

    def test_rejects_collection_from_another_corpus(self):
        expected = {"model_name": "nlpai-lab/KURE-v1", "source_sha256": "a"}
        actual = {"model_name": "nlpai-lab/KURE-v1", "source_sha256": "b"}

        with self.assertRaisesRegex(ValueError, "현재 모델 또는 청크와 다릅니다"):
            validate_collection_metadata(actual, expected)

    def test_splits_batch_after_cuda_oom(self):
        embeddings = encode_with_oom_split(OomOnceModel(), ["가", "나"])

        self.assertEqual(len(embeddings), 2)
        self.assertEqual(len(embeddings[0]), MODEL_DIMENSION)

    def test_rejects_wrong_embedding_dimension(self):
        with self.assertRaisesRegex(ValueError, "임베딩 차원"):
            embedding_rows([[0.0, 1.0]])

    def test_preserves_previous_maximum_token_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "manifest.json"
            manifest_path.write_text(
                json.dumps({"maximum_token_count": 4763}),
                encoding="utf-8",
            )

            self.assertEqual(
                previous_maximum_token_count(manifest_path),
                4763,
            )


if __name__ == "__main__":
    unittest.main()
