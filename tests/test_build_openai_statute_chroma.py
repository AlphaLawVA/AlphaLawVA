# test_build_openai_statute_chroma.py
"""
Description: OpenAI 법령 임베딩 구축기의 비용 계산, 요청 배치 제한,
응답 순서·차원 검증과 기본 비과금 실행을 실제 API 호출 없이 테스트한다.
Author: ooheunsu
Date: 2026-08-31
Before:
    - text-embedding-3-large 비용 계산 및 ChromaDB 구축 코드가 작성된 상태.

After:
    - API 키와 과금 없이 OpenAI 구축기의 핵심 안전장치를 검증 가능.
"""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ml.embedding.build_openai_statute_chroma import (
    MODEL_DIMENSION,
    PRICE_USD_PER_MILLION_TOKENS,
    build_cost_report,
    estimated_cost_usd,
    parse_args,
    request_batches,
    response_embeddings,
)


def sample_chunk(index: int) -> dict:
    return {
        "chunk_id": f"chunk-{index}",
        "retrieval_text": f"법령 조문 {index}",
    }


class BuildOpenAIStatuteChromaTests(unittest.TestCase):
    def test_default_mode_does_not_execute_api(self):
        with patch.object(sys, "argv", ["build_openai_statute_chroma.py"]):
            args = parse_args()

        self.assertFalse(args.execute)

    def test_estimates_standard_api_cost(self):
        self.assertEqual(PRICE_USD_PER_MILLION_TOKENS, 0.13)
        self.assertEqual(estimated_cost_usd(4_000_000), 0.52)

    def test_splits_requests_by_item_and_token_limits(self):
        chunks = [sample_chunk(index) for index in range(4)]
        counts = {
            "chunk-0": 4,
            "chunk-1": 4,
            "chunk-2": 4,
            "chunk-3": 4,
        }

        batches = request_batches(
            chunks,
            counts,
            max_items=3,
            max_tokens=10,
        )

        self.assertEqual([len(batch) for batch in batches], [2, 2])

    def test_cost_report_records_corpus_and_request_count(self):
        chunks = [sample_chunk(0), sample_chunk(1)]
        report = build_cost_report(
            chunks,
            {"chunk-0": 10, "chunk-1": 20},
            "source-hash",
            1,
        )

        self.assertEqual(report["total_input_tokens"], 30)
        self.assertEqual(report["maximum_input_tokens"], 20)
        self.assertEqual(report["estimated_request_count"], 1)
        self.assertEqual(report["source_sha256"], "source-hash")

    def test_orders_response_embeddings_by_index(self):
        response = SimpleNamespace(
            data=[
                SimpleNamespace(
                    index=1,
                    embedding=[1.0] * MODEL_DIMENSION,
                ),
                SimpleNamespace(
                    index=0,
                    embedding=[0.0] * MODEL_DIMENSION,
                ),
            ]
        )

        rows = response_embeddings(response, 2)

        self.assertEqual(rows[0][0], 0.0)
        self.assertEqual(rows[1][0], 1.0)

    def test_rejects_wrong_response_dimension(self):
        response = SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=[0.0, 1.0])]
        )

        with self.assertRaisesRegex(ValueError, "임베딩 차원"):
            response_embeddings(response, 1)


if __name__ == "__main__":
    unittest.main()
