# test_certified_letter_gemini.py
"""
Description: Gemini 내용증명 어댑터의 구조화 요청·응답 검증과 사용량 기록을 확인한다.
가짜 HTTP 응답으로 실제 유료 호출 없이 모델 연결 계약을 검증한다.
Author: choeminju
Date: 2026-10-22
Before:
    - Gemini 내용증명 생성 어댑터를 자동 검증하는 테스트가 없는 상태.
After:
    - 요청 스키마, 안전한 파싱, 오류 처리와 비용 manifest가 회귀 테스트됨.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from ml.agent.certified_letter.gemini import GeminiStructuredLetterModel
from ml.agent.certified_letter.prompts import PromptBundle


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def make_draft_payload():
    return {
        "title": "임대차보증금 반환 촉구서",
        "sender": {
            "name": "김민지",
            "address": "서울특별시 중구 세종대로 1",
            "detail_address": "101동 101호",
        },
        "recipient": {
            "name": "홍길동",
            "address": "서울특별시 종로구 사직로 1",
            "detail_address": None,
        },
        "body": (
            "계약 종료에 따라 보증금 반환을 요청합니다.\n\n"
            "이 내용증명을 받은 날부터 7일 이내 이행하여 주시기 바랍니다."
        ),
        "strategy_summary": "계약 종료 사실과 반환 기한을 명확히 통지합니다.",
        "follow_up_actions": ["발송 전 계약 정보를 확인합니다."],
        "warnings": ["이 문서는 법률 검토 전 초안입니다."],
    }


class GeminiStructuredLetterModelTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.manifest_path = Path(self.temporary_directory.name) / "usage.jsonl"
        self.model = GeminiStructuredLetterModel(
            api_key="test-key",
            model="models/gemini-3.5-flash-lite",
            usage_manifest_path=self.manifest_path,
        )
        self.prompt = PromptBundle(
            version="certified_letter_test",
            system="시스템 규칙",
            user='{"private_fact": "외부 기록 금지"}',
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def response_payload(value):
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(value, ensure_ascii=False),
                            }
                        ]
                    }
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 1000,
                "candidatesTokenCount": 500,
                "totalTokenCount": 1500,
            },
        }

    @patch("ml.agent.certified_letter.gemini.urlopen")
    def test_invokes_structured_output_and_records_metadata_only(self, mock_urlopen):
        mock_urlopen.return_value = FakeHttpResponse(
            self.response_payload(make_draft_payload())
        )

        draft = self.model.invoke(self.prompt)

        self.assertEqual(draft.sender.name, "김민지")
        self.assertIn("\n\n", draft.body)
        request = mock_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertTrue(
            request.full_url.endswith(
                "/models/gemini-3.5-flash-lite:generateContent"
            )
        )
        self.assertEqual(
            payload["generationConfig"]["thinkingConfig"],
            {"thinkingLevel": "minimal"},
        )
        self.assertNotIn("temperature", payload["generationConfig"])
        self.assertFalse(
            payload["generationConfig"]["responseJsonSchema"][
                "additionalProperties"
            ]
        )

        usage = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(usage["success"])
        self.assertEqual(usage["model"], "gemini-3.5-flash-lite")
        self.assertEqual(usage["prompt_version"], "certified_letter_test")
        self.assertEqual(usage["estimated_cost_usd"], 0.00155)
        self.assertNotIn("private_fact", usage)

    @patch("ml.agent.certified_letter.gemini.urlopen")
    def test_rejects_schema_mismatch_and_records_failure(self, mock_urlopen):
        invalid = make_draft_payload()
        invalid["unknown"] = "not allowed"
        mock_urlopen.return_value = FakeHttpResponse(self.response_payload(invalid))

        with self.assertRaisesRegex(ValueError, "스키마"):
            self.model.invoke(self.prompt)

        usage = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertFalse(usage["success"])
        self.assertEqual(usage["error_type"], "ValueError")

    @patch("ml.agent.certified_letter.gemini.urlopen")
    def test_wraps_connection_failure_without_exposing_prompt(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("secret provider detail")

        with self.assertRaisesRegex(RuntimeError, "호출할 수 없습니다") as context:
            self.model.invoke(self.prompt)

        self.assertNotIn("private_fact", str(context.exception))

    def test_rejects_invalid_configuration(self):
        with self.assertRaises(ValueError):
            GeminiStructuredLetterModel(api_key="")
        with self.assertRaises(ValueError):
            GeminiStructuredLetterModel(
                api_key="test",
                base_url="http://example.com",
            )


if __name__ == "__main__":
    unittest.main()
