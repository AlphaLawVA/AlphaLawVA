"""
Description: 프롬프트 하네스의 평가 케이스 무결성과 실행기(가짜 응답, 실호출 가드)를 검증한다.
외부 모델을 호출하지 않고 하네스가 입력 구성, 채점, 결과 기록을 올바르게 하는지 확인한다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 프롬프트 평가를 반복 실행하고 결과를 남기는 실행기가 없는 상태.
After:
    - 케이스 정합성, 오프라인 실행, 실호출 승인 가드, 위반 응답 탐지를 회귀 검증함.
"""

import json
import tempfile
import unittest
from pathlib import Path

from ml.evaluation.prompt_harness.runner import (
    HarnessConfigError,
    load_cases,
    load_pool,
    run_harness,
)


def _bad_review_response(payload):
    """법적 결론과 근거 없는 조문을 담은 위반 응답을 만든다."""

    items = []
    for item in payload["items"]:
        items.append(
            {
                "item_id": item["item_id"],
                "summary": "이 조항은 무효입니다. 민법 제999조 참고.",
                "review_status": "check",
                "revision": "수정안입니다.",
                "negotiation": "협의하세요.",
                "evidence_source_ids": [item["evidence"][0]["source_id"]],
            }
        )
    return {"candidates": [{"content": {"parts": [{"text": json.dumps({"items": items})}]}}]}


class DatasetTests(unittest.TestCase):
    def test_case_ids_are_unique_and_evidence_resolves(self) -> None:
        cases = load_cases()
        ids = [case.id for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(cases), 20)
        for case in cases:
            for item in case.items:
                self.assertTrue(item.evidence, f"{case.id}/{item.query_item.item_id}")

    def test_pool_contains_real_article_text(self) -> None:
        pool = load_pool()
        self.assertTrue(all("본문" in entry["text"] for entry in pool.values()))

    def test_injection_cases_carry_marker_in_inputs(self) -> None:
        injected = [case for case in load_cases() if case.injection_marker]
        self.assertGreaterEqual(len(injected), 3)
        for case in injected:
            joined = " ".join(
                item.query_item.query + " " + " ".join(e.text for e in item.evidence)
                for item in case.items
            )
            self.assertIn(case.injection_marker, joined, case.id)


class RunnerTests(unittest.TestCase):
    def test_fake_run_passes_all_cases_and_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = run_harness(mode="fake", target="all", output_dir=Path(directory))

            self.assertEqual(summary.failed_items, 0)
            self.assertGreater(summary.total_items, 20)
            for name in ("results.jsonl", "summary.md", "review_sheet.md", "config.json"):
                self.assertTrue((Path(directory) / name).exists(), name)
            config = json.loads((Path(directory) / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["mode"], "fake")
            self.assertIn("review_prompt_version", config)
            self.assertIn("review_prompt_sha256", config)

    def test_violating_responses_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = run_harness(
                mode="fake",
                target="review",
                output_dir=Path(directory),
                post_json_override=_bad_review_response,
            )

            self.assertGreater(summary.failed_items, 0)
            self.assertGreater(summary.check_failures["no_legal_conclusion"], 0)
            self.assertGreater(summary.check_failures["no_unsupported_article"], 0)

    def test_prompt_override_changes_recorded_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = run_harness(mode="fake", target="review", output_dir=Path(directory) / "a")
            prompt_file = Path(directory) / "candidate.txt"
            prompt_file.write_text("후보 프롬프트\n", encoding="utf-8")
            candidate = run_harness(
                mode="fake",
                target="review",
                output_dir=Path(directory) / "b",
                review_prompt_file=prompt_file,
            )

            self.assertNotEqual(base.review_prompt_sha256, candidate.review_prompt_sha256)

    def test_live_mode_requires_explicit_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(HarnessConfigError):
                run_harness(mode="live", target="review", output_dir=Path(directory))

    def test_live_mode_respects_call_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(HarnessConfigError):
                run_harness(
                    mode="live",
                    target="all",
                    output_dir=Path(directory),
                    confirm_live=True,
                    api_key="test-key",
                    max_calls=3,
                )


class CompareTests(unittest.TestCase):
    def test_rescore_and_table_for_saved_runs(self) -> None:
        from ml.evaluation.prompt_harness.compare import render_table, rescore_run

        with tempfile.TemporaryDirectory() as directory:
            clean = Path(directory) / "clean"
            bad = Path(directory) / "bad"
            run_harness(mode="fake", target="review", output_dir=clean)
            run_harness(
                mode="fake",
                target="review",
                output_dir=bad,
                post_json_override=_bad_review_response,
            )

            table = render_table({"clean": rescore_run(clean), "bad": rescore_run(bad)})

            self.assertIn("항목 반환", table)
            self.assertIn("no_legal_conclusion", table)
            self.assertEqual(rescore_run(clean)["fails"].total(), 0)


if __name__ == "__main__":
    unittest.main()
