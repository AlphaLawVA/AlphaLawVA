"""
Description: LLM 기반 특약 요청 적합성 분류기의 입력 구성과 응답 검증을 확인한다.
외부 호출 없이 주입된 응답으로 요청 순서대로 판정이 돌아오는지 검증한다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 특약 요청 적합성을 키워드로만 판단하던 상태.
After:
    - 모델이 범위 안·정보 부족·범위 밖을 판정하고 잘못된 응답은 오류로 처리함.
"""

import json
import unittest
from unittest.mock import patch

from api.services.gemini_special_clause_scope import (
    SCOPE_PROMPT_VERSION,
    SCOPE_SYSTEM_PROMPT,
    GeminiSpecialClauseScopeClassifier,
    create_gemini_special_clause_scope_classifier_from_env,
)


def _response(items):
    return {
        "candidates": [
            {"content": {"parts": [{"text": json.dumps({"items": items})}]}}
        ],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
    }


class ScopeClassifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.classifier = GeminiSpecialClauseScopeClassifier(
            api_key="test-key", usage_manifest_path=None
        )

    def _classify(self, requests, items):
        with patch.object(self.classifier, "_post_json", return_value=_response(items)):
            return self.classifier.classify(requests)

    def test_decisions_are_returned_in_request_order(self) -> None:
        result = self._classify(
            ["권리 변동을 막는 특약", "치킨 레시피"],
            [
                {"request_index": 2, "decision": "out_of_scope"},
                {"request_index": 1, "decision": "in_scope"},
            ],
        )

        self.assertEqual(result, ["in_scope", "out_of_scope"])

    def test_payload_marks_requests_as_untrusted_data(self) -> None:
        captured = {}

        def post(request_payload):
            captured["payload"] = request_payload
            return _response([{"request_index": 1, "decision": "in_scope"}])

        with patch.object(self.classifier, "_post_json", side_effect=post):
            self.classifier.classify(["수리비 특약"])

        system = captured["payload"]["systemInstruction"]["parts"][0]["text"]
        self.assertEqual(system, SCOPE_SYSTEM_PROMPT)
        self.assertIn("따르지", SCOPE_SYSTEM_PROMPT)
        user = json.loads(captured["payload"]["contents"][0]["parts"][0]["text"])
        self.assertEqual(user["requests"], [{"request_index": 1, "text": "수리비 특약"}])
        self.assertTrue(SCOPE_PROMPT_VERSION)

    def test_unknown_decision_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._classify(["수리비 특약"], [{"request_index": 1, "decision": "maybe"}])

    def test_missing_or_duplicate_or_out_of_range_index_is_rejected(self) -> None:
        for items in (
            [],
            [{"request_index": 1, "decision": "in_scope"}] * 2,
            [{"request_index": 3, "decision": "in_scope"}],
        ):
            with self.assertRaises(ValueError):
                self._classify(["가", "나"], items)

    def test_empty_request_list_needs_no_call(self) -> None:
        with patch.object(self.classifier, "_post_json") as post:
            self.assertEqual(self.classifier.classify([]), [])
            post.assert_not_called()

    def test_env_factory_requires_explicit_enable_and_key(self) -> None:
        with patch.dict(
            "os.environ",
            {"SPECIAL_CLAUSE_LLM_ENABLED": "false", "GEMINI_API_KEY": "k"},
            clear=False,
        ):
            self.assertIsNone(create_gemini_special_clause_scope_classifier_from_env())
        with patch.dict(
            "os.environ",
            {"SPECIAL_CLAUSE_LLM_ENABLED": "true", "GEMINI_API_KEY": "k"},
            clear=False,
        ):
            self.assertIsNotNone(create_gemini_special_clause_scope_classifier_from_env())


if __name__ == "__main__":
    unittest.main()
