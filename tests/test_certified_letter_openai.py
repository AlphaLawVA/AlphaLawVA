# test_certified_letter_openai.py
"""
Description: OpenAI 내용증명 어댑터의 구조화 요청·응답과 사용량 기록을 검증한다.
가짜 Responses API 응답으로 실제 유료 호출 없이 GPT-6.1 Terra 연결 계약을 확인한다.
Author: choeminju
Date: 2026-11-13
Before:
    - OpenAI 내용증명 생성 어댑터를 검증하는 테스트가 없는 상태.
After:
    - 비저장 구조화 요청, 안전한 파싱, 오류 처리와 비용 기록이 회귀 테스트됨.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from ml.agent.certified_letter.openai import OpenAIStructuredLetterModel
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
        "body": "계약 종료에 따라 보증금 반환을 요청합니다.",
        "strategy_summary": "계약 종료 사실과 반환 기한을 명확히 통지합니다.",
        "follow_up_actions": ["발송 전 계약 정보를 확인합니다."],
        "warnings": ["이 문서는 법률 검토 전 초안입니다."],
    }


class OpenAIStructuredLetterModelTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.manifest_path = Path(self.temporary_directory.name) / "usage.jsonl"
        self.model = OpenAIStructuredLetterModel(
            api_key="test-key",
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
            "id": "resp_test",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(value, ensure_ascii=False),
                        }
                    ],
                }
            ],
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "total_tokens": 1500,
            },
        }

    @patch("ml.agent.certified_letter.openai.urlopen")
    def test_invokes_responses_structured_output_without_storage(
        self,
        mock_urlopen,
    ):
        mock_urlopen.return_value = FakeHttpResponse(
            self.response_payload(make_draft_payload())
        )

        draft = self.model.invoke(self.prompt)

        self.assertEqual(draft.sender.name, "김민지")
        request = mock_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(payload["model"], "gpt-6.1-terra")
        self.assertEqual(payload["reasoning"], {"effort": "medium"})
        self.assertFalse(payload["store"])
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertEqual(
            payload["text"]["format"]["type"],
            "json_schema",
        )

        usage = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(usage["success"])
        self.assertEqual(usage["provider"], "openai")
        self.assertEqual(usage["model"], "gpt-6.1-terra")
        self.assertEqual(usage["estimated_cost_usd"], 0.0035)
        self.assertNotIn("private_fact", usage)

    @patch("ml.agent.certified_letter.openai.urlopen")
    def test_rejects_incomplete_response(self, mock_urlopen):
        mock_urlopen.return_value = FakeHttpResponse(
            {"status": "incomplete", "output": [], "usage": {}}
        )

        with self.assertRaisesRegex(RuntimeError, "완료되지 않았습니다"):
            self.model.invoke(self.prompt)

    @patch("ml.agent.certified_letter.openai.urlopen")
    def test_rejects_refusal(self, mock_urlopen):
        mock_urlopen.return_value = FakeHttpResponse(
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "refusal", "refusal": "refused"}],
                    }
                ],
                "usage": {},
            }
        )

        with self.assertRaisesRegex(RuntimeError, "거부"):
            self.model.invoke(self.prompt)

    @patch("ml.agent.certified_letter.openai.urlopen")
    def test_wraps_connection_failure_without_prompt(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("secret provider detail")

        with self.assertRaisesRegex(RuntimeError, "호출할 수 없습니다") as context:
            self.model.invoke(self.prompt)

        self.assertNotIn("private_fact", str(context.exception))

    def test_rejects_invalid_configuration(self):
        with self.assertRaises(ValueError):
            OpenAIStructuredLetterModel(api_key="")
        with self.assertRaises(ValueError):
            OpenAIStructuredLetterModel(
                api_key="test",
                base_url="http://example.com",
            )
        with self.assertRaises(ValueError):
            OpenAIStructuredLetterModel(
                api_key="test",
                reasoning_effort="invalid",
            )

    @patch("ml.agent.certified_letter.openai.urlopen")
    def test_blocks_call_before_session_budget_can_be_exceeded(
        self,
        mock_urlopen,
    ):
        model = OpenAIStructuredLetterModel(
            api_key="test-key",
            max_session_cost_usd=0.000001,
            usage_manifest_path=self.manifest_path,
        )

        with self.assertRaisesRegex(RuntimeError, "비용 상한"):
            model.invoke(self.prompt)

        mock_urlopen.assert_not_called()
        self.assertFalse(self.manifest_path.exists())


if __name__ == "__main__":
    unittest.main()
