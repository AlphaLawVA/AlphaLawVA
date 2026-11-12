# test_llm_provider_selection.py
"""
Description: 특약 생성기와 계약서 검토 분석기가 기본으로 gpt-6.1-terra를 쓰고, 키가 없으면 대체 없이 만들어지지 않으며,
모델 이름으로 Gemini를 고를 수 있는지 검증한다.
Author: ooheunsu
Date: 2026-11-12
Before:
    - 두 기능 모두 Gemini 생성기만 환경 설정으로 만들 수 있었다.
After:
    - 기본 모델이 gpt-6.1-terra(OpenAI Chat Completions)이고 사용량 기록에 제공자와 비용이 남는다.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.services import gemini_contract_review_analyzer as review_module
from api.services import gemini_special_clause_generator as clause_module
from api.services.openai_chat_transport import OpenAIChatTransport, is_openai_model

CASES = (
    ("특약 생성", clause_module, "create_gemini_special_clause_generator_from_env",
     "SPECIAL_CLAUSE", "SPECIAL_CLAUSE_LLM_ENABLED"),
    ("계약서 검토", review_module, "create_gemini_contract_review_analyzer_from_env",
     "CONTRACT_REVIEW", "CONTRACT_REVIEW_LLM_ENABLED"),
)
MANAGED = ("OPENAI_API_KEY", "GEMINI_API_KEY", "SPECIAL_CLAUSE_LLM_MODEL", "SPECIAL_CLAUSE_GEMINI_MODEL",
           "CONTRACT_REVIEW_LLM_MODEL", "CONTRACT_REVIEW_GEMINI_MODEL")


def _environment(enabled_variable, **values):
    patcher = patch.dict(os.environ, {enabled_variable: "true", **values}, clear=False)
    patcher.start()
    for name in MANAGED:
        if name not in values:
            os.environ.pop(name, None)
    return patcher


class ProviderSelectionTest(unittest.TestCase):
    def test_openai_model_names(self):
        self.assertTrue(is_openai_model("gpt-6.1-terra"))
        self.assertFalse(is_openai_model("gemini-3.5-flash-lite"))
        self.assertFalse(is_openai_model("models/gemini-3.5-flash-lite"))

    def test_default_model_is_terra_through_openai_transport(self):
        for label, module, factory, _prefix, enabled in CASES:
            with self.subTest(label), patch.object(module, "load_env_file"):
                patcher = _environment(enabled, OPENAI_API_KEY="test-openai-key")
                try:
                    instance = getattr(module, factory)()
                finally:
                    patcher.stop()
                self.assertIsNotNone(instance)
                self.assertEqual(instance._model_name, "gpt-6.1-terra")
                self.assertEqual(instance._provider, "openai")
                self.assertIsInstance(instance._post_json.__self__, OpenAIChatTransport)
                self.assertEqual(instance._thinking_config(), {})

    def test_missing_openai_key_means_no_generator_and_no_gemini_fallback(self):
        for label, module, factory, _prefix, enabled in CASES:
            with self.subTest(label), patch.object(module, "load_env_file"):
                patcher = _environment(enabled, GEMINI_API_KEY="test-gemini-key")
                try:
                    self.assertIsNone(getattr(module, factory)())
                finally:
                    patcher.stop()

    def test_gemini_model_name_selects_gemini_and_its_key(self):
        for label, module, factory, prefix, enabled in CASES:
            with self.subTest(label), patch.object(module, "load_env_file"):
                patcher = _environment(
                    enabled, GEMINI_API_KEY="test-gemini-key", **{f"{prefix}_LLM_MODEL": "gemini-3.5-flash-lite"}
                )
                try:
                    instance = getattr(module, factory)()
                finally:
                    patcher.stop()
                self.assertEqual(instance._provider, "gemini")
                self.assertEqual(instance._model_name, "gemini-3.5-flash-lite")

    def test_legacy_gemini_model_variable_is_still_honored(self):
        label, module, factory, prefix, enabled = CASES[0]
        with patch.object(module, "load_env_file"):
            patcher = _environment(
                enabled, GEMINI_API_KEY="test-gemini-key", **{f"{prefix}_GEMINI_MODEL": "gemini-3.5-flash-lite"}
            )
            try:
                instance = getattr(module, factory)()
            finally:
                patcher.stop()
        self.assertEqual(instance._provider, "gemini")

    def test_usage_record_names_provider_and_prices_terra(self):
        for label, module, factory, _prefix, enabled in CASES:
            with self.subTest(label), tempfile.TemporaryDirectory() as directory, patch.object(module, "load_env_file"):
                manifest = Path(directory) / "usage.jsonl"
                patcher = _environment(enabled, OPENAI_API_KEY="test-openai-key")
                try:
                    instance = getattr(module, factory)()
                finally:
                    patcher.stop()
                instance._usage_manifest_path = manifest
                instance._record_usage(
                    {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "total_tokens": 2_000_000},
                    success=True,
                )
                record = json.loads(manifest.read_text(encoding="utf-8").splitlines()[0])
                self.assertEqual(record["provider"], "openai")
                self.assertEqual(record["model"], "gpt-6.1-terra")
                self.assertEqual(record["estimated_cost_usd"], 6.0)  # $1 + $5


if __name__ == "__main__":
    unittest.main()
