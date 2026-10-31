# test_gemini_similarity_explainer.py
"""
Description: Gemini 판례 유사성 설명기의 구조화 요청, 근거 검증과 사용량 기록을 확인한다.
가짜 HTTP 응답을 사용하여 실제 유료 API 호출 없이 성공·실패 동작을 검사한다.
Author: choeminju
Date: 2026-10-15
Before:
    - Gemini 기반 판례 유사성 설명 어댑터가 정의된 상태.
After:
    - 요청 제한, JSON 검증, 환경 설정과 비용 manifest 기록이 자동 확인됨.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from api.models.precedents import SimilarityExplanationMethod
from api.services.gemini_similarity_explainer import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_MAX_CHUNKS,
    GeminiSimilarityExplainer,
    create_gemini_similarity_explainer_from_env,
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


class GeminiSimilarityExplainerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.manifest_path = Path(self.temporary_directory.name) / "usage.jsonl"
        self.explainer = GeminiSimilarityExplainer(
            api_key="test-key",
            model="gemini-3.5-flash-lite",
            timeout_seconds=10,
            usage_manifest_path=self.manifest_path,
        )
        self.precedent = {
            "판례일련번호": "64441",
            "사건번호": "2006다62492",
            "사건명": "임대차보증금등",
            "법원명": "대법원",
            "선고일자": "2008-09-25",
            "생성요약": "임대차 종료와 보증금 반환을 판단한 사건이다.",
            "쉬운요약": "세입자가 보증금을 돌려달라고 한 사건입니다.",
            "이유": "Gemini에 전달하면 안 되는 판례 원문",
        }

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def response_payload(parsed_content):
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    parsed_content,
                                    ensure_ascii=False,
                                )
                            }
                        ]
                    }
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 1000,
                "candidatesTokenCount": 100,
                "totalTokenCount": 1100,
            },
        }

    @patch("api.services.gemini_similarity_explainer.urlopen")
    def test_explains_with_structured_output_and_records_usage(self, mock_urlopen):
        mock_urlopen.return_value = FakeHttpResponse(
            self.response_payload(
                {
                    "user_evidence": "계약이 끝났지만 보증금을 받지 못했습니다",
                    "precedent_evidence": "임대차 종료와 보증금 반환을 판단한",
                    "matched_reason": (
                        "사용자 상황과 이 판례는 임대차 종료 후 보증금 반환이 "
                        "문제 된 점에서 유사합니다."
                    ),
                }
            )
        )

        reason = self.explainer.explain(
            query="계약이 끝났지만 보증금을 받지 못했습니다.",
            precedent=self.precedent,
            matched_chunks=[make_chunk(index) for index in range(1, 6)],
        )

        self.assertIn("보증금 반환", reason)
        self.assertEqual(self.explainer.method, SimilarityExplanationMethod.GEMINI)
        request = mock_urlopen.call_args.args[0]
        request_payload = json.loads(request.data.decode("utf-8"))
        self.assertTrue(
            request.full_url.endswith(
                "/models/gemini-3.5-flash-lite:generateContent"
            )
        )
        self.assertEqual(
            request_payload["generationConfig"]["responseMimeType"],
            "application/json",
        )
        self.assertEqual(
            request_payload["generationConfig"]["thinkingConfig"],
            {"thinkingLevel": "minimal"},
        )
        user_payload = json.loads(
            request_payload["contents"][0]["parts"][0]["text"]
        )
        self.assertEqual(len(user_payload["matched_evidence"]), DEFAULT_MAX_CHUNKS)
        self.assertNotIn("이유", user_payload["precedent"])

        usage = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(usage["success"])
        self.assertEqual(usage["input_tokens"], 1000)
        self.assertEqual(usage["output_tokens"], 100)
        self.assertEqual(usage["estimated_cost_usd"], 0.00055)

    @patch("api.services.gemini_similarity_explainer.urlopen")
    def test_rejects_evidence_not_found_in_input(self, mock_urlopen):
        mock_urlopen.return_value = FakeHttpResponse(
            self.response_payload(
                {
                    "user_evidence": "사용자가 말하지 않은 상속 문제",
                    "precedent_evidence": "임대차 종료와 보증금 반환을 판단한",
                    "matched_reason": (
                        "사용자 상황과 이 판례는 상속 문제가 있는 점에서 유사합니다."
                    ),
                }
            )
        )

        with self.assertRaisesRegex(ValueError, "사용자 근거"):
            self.explainer.explain(
                query="보증금을 받지 못했습니다.",
                precedent=self.precedent,
                matched_chunks=[make_chunk(1)],
            )

        usage = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertFalse(usage["success"])
        self.assertEqual(usage["error_type"], "ValueError")

    @patch("api.services.gemini_similarity_explainer.urlopen")
    def test_wraps_gemini_connection_error(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("offline")

        with self.assertRaisesRegex(RuntimeError, "호출할 수 없습니다"):
            self.explainer.explain(
                query="보증금을 받지 못했습니다.",
                precedent=self.precedent,
                matched_chunks=[make_chunk(1)],
            )

    def test_reads_configuration_and_disable_switch_from_environment(self):
        with (
            patch("api.services.gemini_similarity_explainer.load_env_file"),
            patch.dict(
                os.environ,
                {
                    "GEMINI_API_KEY": "test-key",
                    "PRECEDENT_SIMILARITY_LLM_ENABLED": "true",
                    "PRECEDENT_SIMILARITY_GEMINI_MODEL": "models/gemini-test",
                    "PRECEDENT_SIMILARITY_GEMINI_TIMEOUT_SECONDS": "15",
                    "PRECEDENT_SIMILARITY_GEMINI_MAX_OUTPUT_TOKENS": "120",
                },
                clear=True,
            ),
        ):
            explainer = create_gemini_similarity_explainer_from_env()

        self.assertIsInstance(explainer, GeminiSimilarityExplainer)
        self.assertEqual(explainer.model_name, "gemini-test")
        self.assertEqual(explainer.timeout_seconds, 15)
        self.assertEqual(explainer.max_output_tokens, 120)

        with (
            patch("api.services.gemini_similarity_explainer.load_env_file"),
            patch.dict(
                os.environ,
                {"PRECEDENT_SIMILARITY_LLM_ENABLED": "false"},
                clear=True,
            ),
        ):
            self.assertIsNone(create_gemini_similarity_explainer_from_env())

    def test_missing_api_key_disables_explainer(self):
        with (
            patch("api.services.gemini_similarity_explainer.load_env_file"),
            patch.dict(os.environ, {}, clear=True),
        ):
            self.assertIsNone(create_gemini_similarity_explainer_from_env())

    def test_rejects_invalid_configuration_and_missing_chunks(self):
        with self.assertRaises(ValueError):
            GeminiSimilarityExplainer(api_key="")
        with self.assertRaises(ValueError):
            GeminiSimilarityExplainer(api_key="test", base_url="http://example.com")
        with self.assertRaisesRegex(ValueError, "적중 청크"):
            self.explainer.explain(
                query="보증금을 받지 못했습니다.",
                precedent=self.precedent,
                matched_chunks=[],
            )

    def test_default_model_is_available_flash_lite(self):
        self.assertEqual(DEFAULT_GEMINI_MODEL, "gemini-3.5-flash-lite")


if __name__ == "__main__":
    unittest.main()
