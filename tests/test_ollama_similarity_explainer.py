# test_ollama_similarity_explainer.py
"""
Description: Ollama 판례 유사성 설명기의 요청 제한과 구조화 응답 검증을 확인한다.
가짜 로컬 HTTP 응답을 사용해 실제 모델 호출 없이 성공·실패 동작을 검사한다.
Author: choeminju
Date: 2026-10-14
Before:
    - Ollama 호환 판례 유사성 설명 어댑터가 정의된 상태.
After:
    - 근거 제한, 설정 검증, JSON 파싱과 로컬 모델 장애 처리가 자동 확인됨.
"""

import json
import os
import unittest
from unittest.mock import patch
from urllib.error import URLError

from api.services.ollama_similarity_explainer import (
    DEFAULT_MAX_CHUNKS,
    OllamaSimilarityExplainer,
    create_ollama_similarity_explainer_from_env,
)
from ml.rag.precedent_retriever import PrecedentChunkMatch


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def make_chunk(rank: int) -> PrecedentChunkMatch:
    return PrecedentChunkMatch(
        rank=rank,
        chunk_id=f"precedent:64441:reason:{rank:04d}",
        distance=0.1 + rank / 100,
        similarity=0.9 - rank / 100,
        text=f"임대차 종료와 보증금 반환을 판단한 근거 {rank}. " + "가" * 100,
        chunk_type="이유",
        section="이유",
        section_chunk_index=rank - 1,
        metadata={"precedent_id": "64441"},
    )


class OllamaSimilarityExplainerTests(unittest.TestCase):
    def setUp(self):
        self.explainer = OllamaSimilarityExplainer(
            model="qwen-test:9b",
            timeout_seconds=10,
        )
        self.precedent = {
            "판례일련번호": "64441",
            "사건번호": "2006다62492",
            "사건명": "임대차보증금등",
            "법원명": "대법원",
            "선고일자": "2008-09-25",
            "생성요약": "임대차 종료와 보증금 반환을 판단한 사건이다.",
            "쉬운요약": "세입자가 보증금을 돌려달라고 한 사건입니다.",
            "이유": "LLM에 전달하면 안 되는 판례 원문",
        }

    @patch("api.services.ollama_similarity_explainer.urlopen")
    def test_explains_similarity_from_grounded_structured_response(self, mock_urlopen):
        mock_urlopen.return_value = FakeHttpResponse(
            {
                "message": {
                    "content": json.dumps(
                        {
                            "user_evidence": "계약이 끝났지만 보증금을 받지 못했습니다",
                            "precedent_evidence": "임대차 종료와 보증금 반환을 판단한",
                            "matched_reason": (
                                "사용자 상황과 이 판례는 임대차 종료 후 보증금 반환이 "
                                "문제 된 점에서 유사합니다."
                            )
                        },
                        ensure_ascii=False,
                    )
                }
            }
        )

        reason = self.explainer.explain(
            query="계약이 끝났지만 보증금을 받지 못했습니다.",
            precedent=self.precedent,
            matched_chunks=[make_chunk(index) for index in range(1, 6)],
        )

        self.assertIn("보증금 반환", reason)
        request = mock_urlopen.call_args.args[0]
        request_payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(request_payload["model"], "qwen-test:9b")
        self.assertFalse(request_payload["think"])
        self.assertEqual(
            request_payload["format"]["required"],
            ["user_evidence", "precedent_evidence", "matched_reason"],
        )
        user_payload = json.loads(request_payload["messages"][1]["content"])
        self.assertEqual(len(user_payload["matched_evidence"]), DEFAULT_MAX_CHUNKS)
        self.assertNotIn("이유", user_payload["precedent"])

    @patch("api.services.ollama_similarity_explainer.urlopen")
    def test_rejects_empty_or_invalid_model_output(self, mock_urlopen):
        mock_urlopen.return_value = FakeHttpResponse(
            {"message": {"content": "not-json"}}
        )

        with self.assertRaisesRegex(ValueError, "JSON 형식"):
            self.explainer.explain(
                query="보증금을 못 받았습니다.",
                precedent=self.precedent,
                matched_chunks=[make_chunk(1)],
            )

        mock_urlopen.return_value = FakeHttpResponse(
            {
                "message": {
                    "content": json.dumps(
                        {
                            "user_evidence": "보증금을 못 받았습니다",
                            "precedent_evidence": "보증금 반환을 판단한",
                            "matched_reason": "보증금 반환청구 여부",
                        },
                        ensure_ascii=False,
                    )
                }
            }
        )
        with self.assertRaisesRegex(ValueError, "완결된 비교 문장"):
            self.explainer.explain(
                query="보증금을 못 받았습니다.",
                precedent=self.precedent,
                matched_chunks=[make_chunk(1)],
            )

        mock_urlopen.return_value = FakeHttpResponse(
            {
                "message": {
                    "content": json.dumps(
                        {
                            "user_evidence": "사용자 원문에 없는 상속인",
                            "precedent_evidence": "임대차 종료와 보증금 반환을 판단한",
                            "matched_reason": (
                                "사용자 상황과 이 판례는 상속인이 관련된 점에서 "
                                "유사합니다."
                            ),
                        },
                        ensure_ascii=False,
                    )
                }
            }
        )
        with self.assertRaisesRegex(ValueError, "사용자 근거"):
            self.explainer.explain(
                query="보증금을 못 받았습니다.",
                precedent=self.precedent,
                matched_chunks=[make_chunk(1)],
            )

    @patch("api.services.ollama_similarity_explainer.urlopen")
    def test_validates_long_exact_evidence_without_truncating_it(self, mock_urlopen):
        source_evidence = "임대차 종료 후 '보증금'을 반환하지 않았다. " + "가" * 180
        precedent_evidence = "임대차 종료 후 보증금을 반환하지 않았다 " + "가" * 180
        chunk = make_chunk(1)
        chunk = PrecedentChunkMatch(
            rank=chunk.rank,
            chunk_id=chunk.chunk_id,
            distance=chunk.distance,
            similarity=chunk.similarity,
            text=source_evidence,
            chunk_type=chunk.chunk_type,
            section=chunk.section,
            section_chunk_index=chunk.section_chunk_index,
            metadata=chunk.metadata,
        )
        mock_urlopen.return_value = FakeHttpResponse(
            {
                "message": {
                    "content": json.dumps(
                        {
                            "user_evidence": "보증금을 받지 못했습니다",
                            "precedent_evidence": precedent_evidence,
                            "matched_reason": (
                                "사용자 상황과 이 판례는 임대차 종료 후 보증금을 "
                                "받지 못한 점에서 유사합니다."
                            ),
                        },
                        ensure_ascii=False,
                    )
                }
            }
        )

        reason = self.explainer.explain(
            query="보증금을 받지 못했습니다.",
            precedent=self.precedent,
            matched_chunks=[chunk],
        )

        self.assertIn("보증금을 받지 못한", reason)

    @patch("api.services.ollama_similarity_explainer.urlopen")
    def test_wraps_local_ollama_connection_error(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("offline")

        with self.assertRaisesRegex(RuntimeError, "호출할 수 없습니다"):
            self.explainer.explain(
                query="보증금을 못 받았습니다.",
                precedent=self.precedent,
                matched_chunks=[make_chunk(1)],
            )

    def test_reads_model_configuration_and_disable_switch_from_environment(self):
        with patch.dict(
            os.environ,
            {
                "PRECEDENT_SIMILARITY_LLM_ENABLED": "true",
                "PRECEDENT_SIMILARITY_LLM_MODEL": "qwen-custom:9b",
                "OLLAMA_BASE_URL": "http://localhost:9999/",
                "PRECEDENT_SIMILARITY_LLM_TIMEOUT_SECONDS": "15",
                "PRECEDENT_SIMILARITY_LLM_NUM_CTX": "4096",
                "PRECEDENT_SIMILARITY_LLM_NUM_PREDICT": "100",
            },
            clear=False,
        ):
            explainer = create_ollama_similarity_explainer_from_env()

        self.assertIsInstance(explainer, OllamaSimilarityExplainer)
        self.assertEqual(explainer.model_name, "qwen-custom:9b")
        self.assertEqual(explainer.base_url, "http://localhost:9999")
        self.assertEqual(explainer.timeout_seconds, 15)

        with patch.dict(
            os.environ,
            {"PRECEDENT_SIMILARITY_LLM_ENABLED": "false"},
            clear=False,
        ):
            self.assertIsNone(create_ollama_similarity_explainer_from_env())

    def test_rejects_invalid_configuration_and_missing_chunks(self):
        with self.assertRaises(ValueError):
            OllamaSimilarityExplainer(model="")
        with self.assertRaises(ValueError):
            OllamaSimilarityExplainer(base_url="not-a-url")
        with self.assertRaisesRegex(ValueError, "적중 청크"):
            self.explainer.explain(
                query="보증금을 못 받았습니다.",
                precedent=self.precedent,
                matched_chunks=[],
            )


if __name__ == "__main__":
    unittest.main()
