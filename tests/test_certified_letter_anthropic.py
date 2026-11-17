"""Claude 내용증명 어댑터의 구조화 호출과 사용량 기록을 검증한다."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml.agent.certified_letter.anthropic import AnthropicStructuredLetterModel
from ml.agent.certified_letter.prompts import PromptBundle


class _FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def _draft_payload():
    return {
        "title": "중도금 지급 요청",
        "sender": {"name": "홍길동", "address": None, "detail_address": None},
        "recipient": {"name": "김철수", "address": None, "detail_address": None},
        "body": "중도금 지급을 요청합니다.",
        "strategy_summary": "미지급 중도금의 지급을 요청합니다.",
        "follow_up_actions": ["계약 정보를 확인합니다."],
        "warnings": ["이 문서는 법률 검토 전 초안입니다."],
        "used_statute_source_ids": ["statute:001:565"],
    }


class AnthropicStructuredLetterModelTests(unittest.TestCase):
    def test_invokes_messages_api_with_structured_output(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = Path(temporary_directory) / "usage.jsonl"
            model = AnthropicStructuredLetterModel(
                api_key="test-key",
                usage_manifest_path=manifest_path,
            )
            prompt = PromptBundle(
                version="certified_letter_test",
                system="시스템 규칙",
                user='{"private_fact": "외부 기록 금지"}',
            )
            response = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(_draft_payload(), ensure_ascii=False),
                    }
                ],
                "usage": {"input_tokens": 1000, "output_tokens": 500},
            }
            with patch("ml.agent.certified_letter.anthropic.urlopen") as mock_urlopen:
                mock_urlopen.return_value = _FakeHttpResponse(response)
                draft = model.invoke(prompt)

            self.assertEqual(draft.sender.name, "홍길동")
            self.assertEqual(
                draft.used_statute_source_ids,
                ("statute:001:565",),
            )
            request = mock_urlopen.call_args.args[0]
            payload = json.loads(request.data.decode("utf-8"))
            self.assertEqual(request.full_url, "https://api.anthropic.com/v1/messages")
            self.assertEqual(payload["model"], "claude-sonnet-5-5")
            self.assertEqual(
                payload["output_config"]["format"]["type"],
                "json_schema",
            )
            usage = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(usage["provider"], "anthropic")
            self.assertEqual(usage["model"], "claude-sonnet-5-5")
            self.assertNotIn("private_fact", usage)

    def test_saves_malformed_json_and_retries_once(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            manifest_path = temporary_path / "usage.jsonl"
            failed_response_dir = temporary_path / "failed"
            model = AnthropicStructuredLetterModel(
                api_key="test-key",
                usage_manifest_path=manifest_path,
                failed_response_dir=failed_response_dir,
            )
            prompt = PromptBundle(
                version="certified_letter_test",
                system="시스템 규칙",
                user="사용자 입력",
            )
            malformed = {
                "content": [{"type": "text", "text": '{"title":'}],
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 4096,
                    "output_tokens_details": {"thinking_tokens": 300},
                },
                "stop_reason": "max_tokens",
            }
            completed = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(_draft_payload(), ensure_ascii=False),
                    }
                ],
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "output_tokens_details": {"thinking_tokens": 20},
                },
                "stop_reason": "end_turn",
            }
            with patch("ml.agent.certified_letter.anthropic.urlopen") as mock_urlopen:
                mock_urlopen.side_effect = [
                    _FakeHttpResponse(malformed),
                    _FakeHttpResponse(completed),
                ]
                draft = model.invoke(prompt)

            self.assertEqual(draft.title, "중도금 지급 요청")
            self.assertEqual(mock_urlopen.call_count, 2)
            records = [
                json.loads(line)
                for line in manifest_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertFalse(records[0]["success"])
            self.assertEqual(records[0]["error_type"], "JSONDecodeError")
            self.assertEqual(records[0]["finish_reason"], "max_tokens")
            self.assertEqual(records[0]["reasoning_tokens"], 300)
            self.assertTrue(records[0]["failed_response_saved"])
            self.assertTrue(records[1]["success"])
            self.assertEqual(records[1]["retry_count"], 1)
            self.assertEqual(records[1]["finish_reason"], "end_turn")
            failed_files = list(failed_response_dir.glob("*.json.txt"))
            self.assertEqual(len(failed_files), 1)
            self.assertEqual(failed_files[0].read_text(encoding="utf-8"), '{"title":')


if __name__ == "__main__":
    unittest.main()
