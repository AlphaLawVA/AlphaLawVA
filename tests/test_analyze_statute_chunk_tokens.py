# test_analyze_statute_chunk_tokens.py
"""
Description: 법령 청크 토큰 분석기가 길이 통계와 입력 한도 초과 건수를
일관되게 계산하고 잘못된 청크 데이터를 거부하는지 테스트한다.
Author: ooheunsu
Date: 2026-08-29
Before:
    - 후보 임베딩 모델별 법령 청크 토큰 분석 코드가 작성된 상태.

After:
    - 외부 모델 다운로드 없이 토큰 분석의 핵심 계산을 자동 검증 가능.
"""

import json
import tempfile
import unittest
from pathlib import Path

from ml.embedding.analyze_statute_chunk_tokens import (
    analyze_model,
    find_cached_huggingface_snapshot,
    length_summary,
    load_chunks,
)


class AnalyzeStatuteChunkTokensTests(unittest.TestCase):
    def test_summarizes_token_lengths(self):
        summary = length_summary([10, 20, 30, 40, 100])

        self.assertEqual(summary["min"], 10)
        self.assertEqual(summary["median"], 30)
        self.assertEqual(summary["p95"], 100)
        self.assertEqual(summary["max"], 100)
        self.assertEqual(summary["mean"], 40.0)

    def test_counts_threshold_and_model_limit_exceedance(self):
        chunks = [
            {
                "chunk_id": "chunk-1",
                "retrieval_text": "a" * 100,
                "metadata": {"law_name": "민법", "article_label": "제1조"},
            },
            {
                "chunk_id": "chunk-2",
                "retrieval_text": "b" * 600,
                "metadata": {"law_name": "민법", "article_label": "제2조"},
            },
            {
                "chunk_id": "chunk-3",
                "retrieval_text": "c" * 9000,
                "metadata": {"law_name": "민법", "article_label": "제3조"},
            },
        ]
        spec = {
            "model_name": "test-model",
            "tokenizer_type": "test",
            "max_input_tokens": 8192,
        }

        result = analyze_model(chunks, spec, len)

        self.assertEqual(result["threshold_exceeded"]["512"], 2)
        self.assertEqual(result["threshold_exceeded"]["1024"], 1)
        self.assertEqual(result["max_input_exceeded"], 1)
        self.assertEqual(result["longest_chunks"][0]["chunk_id"], "chunk-3")

    def test_rejects_duplicate_chunk_ids(self):
        document = {
            "chunks": [
                {"chunk_id": "duplicate", "retrieval_text": "첫 번째"},
                {"chunk_id": "duplicate", "retrieval_text": "두 번째"},
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.json"
            path.write_text(
                json.dumps(document, ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "중복 chunk_id"):
                load_chunks(path.parent)

    def test_finds_local_huggingface_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tokenizer_cache = Path(temp_dir)
            snapshot = (
                tokenizer_cache
                / "models--nlpai-lab--KURE-v1"
                / "snapshots"
                / "revision-id"
            )
            snapshot.mkdir(parents=True)

            result = find_cached_huggingface_snapshot(
                "nlpai-lab/KURE-v1",
                tokenizer_cache,
            )

            self.assertEqual(result, snapshot)


if __name__ == "__main__":
    unittest.main()
