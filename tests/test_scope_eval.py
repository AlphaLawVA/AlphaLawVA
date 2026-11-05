"""
Description: 특약 요청 적합성 분류 평가 스크립트의 케이스 정합성과 채점, 실호출 가드를 검증한다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 적합성 분류의 정확도를 같은 케이스로 반복 측정하는 방법이 없는 상태.
After:
    - 가짜 분류기로 채점 흐름을 확인하고 실호출은 승인 플래그가 있어야 실행함.
"""

import json
import tempfile
import unittest
from pathlib import Path

from ml.evaluation.prompt_harness.runner import HarnessConfigError
from ml.evaluation.prompt_harness.scope_eval import (
    DEFAULT_SCOPE_CASES_PATH,
    load_scope_cases,
    run_scope_eval,
)


class AlwaysInScope:
    def classify(self, requests):
        return ["in_scope"] * len(requests)


class ScopeEvalTests(unittest.TestCase):
    def test_cases_cover_all_three_decisions_with_unique_ids(self) -> None:
        cases = load_scope_cases()
        ids = [case["id"] for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(
            {case["expected"] for case in cases},
            {"in_scope", "needs_information", "out_of_scope"},
        )
        self.assertTrue(DEFAULT_SCOPE_CASES_PATH.exists())

    def test_fake_mode_is_perfect_and_writes_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = run_scope_eval(mode="fake", output_dir=Path(directory))

            self.assertEqual(summary.correct, summary.total)
            for name in ("results.jsonl", "summary.md"):
                self.assertTrue((Path(directory) / name).exists())

    def test_wrong_classifier_lowers_accuracy_and_lists_mistakes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = run_scope_eval(
                mode="fake", output_dir=Path(directory), classifier=AlwaysInScope()
            )

            self.assertLess(summary.correct, summary.total)
            self.assertTrue(summary.mistakes)
            text = (Path(directory) / "summary.md").read_text(encoding="utf-8")
            self.assertIn("오분류", text)
            rows = [
                json.loads(line)
                for line in (Path(directory) / "results.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), summary.total)

    def test_live_mode_requires_confirmation_and_call_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(HarnessConfigError):
                run_scope_eval(mode="live", output_dir=Path(directory))
            with self.assertRaises(HarnessConfigError):
                run_scope_eval(
                    mode="live",
                    output_dir=Path(directory),
                    confirm_live=True,
                    api_key="k",
                    max_calls=1,
                )


if __name__ == "__main__":
    unittest.main()
