# test_precedent_query_scope_evaluation.py
"""
Description: 판례 질문 범위 평가기의 Ollama 요청, 구조화 출력 검증과
fail-open 지표 계산을 실제 모델 호출 없이 확인한다.
Author: choeminju
Date: 2026-10-15
Before:
    - 평가 전용 Ollama 범위 분류기와 고정 JSON Schema가 정의된 상태.
After:
    - 모델 오류가 검색을 차단하지 않고 평가 지표에 드러나는지 자동 검증됨.
"""

import json
import unittest
from collections import Counter
from unittest.mock import patch

from ml.evaluation.precedents.evaluate_query_scope_qwen import (
    DECISION_DATASET_PATH,
    DECISION_OUTPUT_SCHEMA,
    DEFAULT_DATASET_PATH,
    OUTPUT_SCHEMA,
    SYSTEM_PROMPTS,
    ClassificationAttempt,
    EvaluationCase,
    OllamaQueryScopeClassifier,
    load_evaluation_cases,
    parse_structured_output,
    summarize_model,
)


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class PrecedentQueryScopeEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.classifier = OllamaQueryScopeClassifier(
            model="qwen-test:9b",
            timeout_seconds=5,
        )

    @patch(
        "ml.evaluation.precedents.evaluate_query_scope_qwen.urlopen"
    )
    def test_uses_ollama_structured_output_without_thinking(self, mock_urlopen):
        mock_urlopen.return_value = FakeHttpResponse(
            {"message": {"content": '{"in_scope":true}'}}
        )

        attempt = self.classifier.classify("보증금을 돌려받고 싶어요.")

        self.assertTrue(attempt.prediction)
        self.assertTrue(attempt.format_compliant)
        request = mock_urlopen.call_args.args[0]
        request_payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request_payload["format"], OUTPUT_SCHEMA)
        self.assertFalse(request_payload["think"])
        self.assertEqual(request_payload["options"]["temperature"], 0)
        self.assertEqual(request_payload["options"]["num_predict"], 24)
        user_payload = json.loads(request_payload["messages"][1]["content"])
        self.assertEqual(
            user_payload,
            {"user_query": "보증금을 돌려받고 싶어요."},
        )
        self.assertEqual(
            request_payload["messages"][0]["content"],
            SYSTEM_PROMPTS["v2"],
        )

    def test_keeps_prompt_versions_selectable_for_comparison(self):
        classifier = OllamaQueryScopeClassifier(
            model="qwen-test:9b",
            prompt_version="v1",
        )

        self.assertEqual(classifier.system_prompt, SYSTEM_PROMPTS["v1"])
        with self.assertRaisesRegex(ValueError, "프롬프트 버전"):
            OllamaQueryScopeClassifier(
                model="qwen-test:9b",
                prompt_version="unknown",
            )

    def test_rejects_outputs_that_do_not_exactly_match_schema(self):
        self.assertTrue(parse_structured_output('{"in_scope":true}'))
        with self.assertRaisesRegex(ValueError, "필드"):
            parse_structured_output(
                '{"in_scope":true,"reason":"부동산 질문"}'
            )
        with self.assertRaisesRegex(ValueError, "boolean"):
            parse_structured_output('{"in_scope":"true"}')
        with self.assertRaises(json.JSONDecodeError):
            parse_structured_output("in_scope=true")

    def test_v3_uses_decision_schema_and_rejects_unknown_enum(self):
        classifier = OllamaQueryScopeClassifier(
            model="qwen-test:9b",
            prompt_version="v3",
        )

        payload = classifier.build_request_payload("전세사기 당했어요.")

        self.assertEqual(payload["format"], DECISION_OUTPUT_SCHEMA)
        self.assertEqual(
            parse_structured_output('{"decision":"ALLOW"}', "v3"),
            "ALLOW",
        )
        with self.assertRaisesRegex(ValueError, "enum"):
            parse_structured_output('{"decision":"UNKNOWN"}', "v3")

    @patch(
        "ml.evaluation.precedents.evaluate_query_scope_qwen.urlopen"
    )
    def test_returns_fail_open_attempt_for_json_parse_failure(self, mock_urlopen):
        mock_urlopen.return_value = FakeHttpResponse(
            {"message": {"content": "not-json"}}
        )

        attempt = self.classifier.classify("파이썬 정렬법 알려줘.")

        self.assertIsNone(attempt.prediction)
        self.assertFalse(attempt.format_compliant)
        self.assertTrue(attempt.effective_prediction)
        self.assertIn("JSONDecodeError", attempt.error)

    def test_summary_separates_false_blocks_and_fail_open_effect(self):
        cases = [
            EvaluationCase("in_1", "real_estate", "보증금", True, "관련"),
            EvaluationCase("in_2", "real_estate", "집주인", True, "관련"),
            EvaluationCase("out_1", "outside", "파스타", False, "무관"),
            EvaluationCase("out_2", "outside", "축구", False, "무관"),
        ]
        attempts = [
            ClassificationAttempt(True, True, 10),
            ClassificationAttempt(False, True, 20),
            ClassificationAttempt(False, True, 30),
            ClassificationAttempt(
                None,
                False,
                40,
                error="TimeoutError: timeout",
            ),
        ]

        summary = summarize_model("qwen-test:9b", cases, attempts)

        self.assertEqual(summary["raw_accuracy"], 0.6667)
        self.assertEqual(summary["fail_open_accuracy"], 0.5)
        self.assertEqual(summary["format_compliance_rate"], 0.75)
        self.assertEqual(summary["false_blocks"], ["in_2"])
        self.assertEqual(summary["false_allows_after_fail_open"], ["out_2"])
        self.assertEqual(summary["failure_ids"], ["out_2"])

    def test_versioned_dataset_keeps_intended_category_distribution(self):
        cases = load_evaluation_cases(DEFAULT_DATASET_PATH)

        self.assertEqual(len(cases), 56)
        self.assertEqual(
            Counter(case.category for case in cases),
            {
                "clear_real_estate": 8,
                "indirect_real_estate": 8,
                "ambiguous_possible_real_estate": 8,
                "hypothetical_real_estate": 8,
                "mixed_topic": 8,
                "clearly_out_of_scope": 8,
                "prompt_injection": 8,
            },
        )
        self.assertEqual(sum(case.expected_in_scope for case in cases), 44)

    def test_v3_dataset_has_four_decisions_and_allow_is_in_scope(self):
        cases = load_evaluation_cases(DECISION_DATASET_PATH)

        self.assertEqual(len(cases), 60)
        self.assertEqual(
            Counter(case.expected_decision for case in cases),
            {
                "ALLOW": 24,
                "NEEDS_CONTEXT": 12,
                "MIXED_INTENT": 12,
                "NOT_REAL_ESTATE": 12,
            },
        )
        self.assertEqual(sum(case.expected_in_scope for case in cases), 24)


if __name__ == "__main__":
    unittest.main()
