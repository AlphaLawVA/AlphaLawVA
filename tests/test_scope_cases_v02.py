# test_scope_cases_v02.py
"""
Description: 분류 평가 케이스 v02와 분류기 프롬프트 v0.2가 애매한 생활 조건 특약을 범위 안으로 안내하는지 확인한다.
Author: ooheunsu
Date: 2026-11-06
Before:
    - 분류 케이스가 계약 핵심 항목 위주이고 생활 조건 특약 안내가 없던 상태.
After:
    - 반려동물·방역 등 케이스와 프롬프트 문구가 회귀 테스트로 보호됨.
"""

import unittest

from api.services.gemini_special_clause_scope import SCOPE_PROMPT_VERSION, SCOPE_SYSTEM_PROMPT
from ml.evaluation.prompt_harness.scope_eval import DEFAULT_SCOPE_CASES_PATH, load_scope_cases, run_scope_eval


class ScopeCasesV02Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = DEFAULT_SCOPE_CASES_PATH.with_name("scope_cases_v02.json")
        self.cases = load_scope_cases(self.path)

    def test_has_user_feedback_requests_as_in_scope(self) -> None:
        expected = {case["request"]: case["expected"] for case in self.cases}
        self.assertEqual(expected["집에서 고양이 두 마리를 키우고 싶어요"], "in_scope")
        self.assertEqual(expected["집에서 바퀴벌레가 나오면 세스코를 집주인이 불러줬으면 좋겠어요"], "in_scope")

    def test_information_questions_stay_out_of_scope(self) -> None:
        expected = {case["request"]: case["expected"] for case in self.cases}
        self.assertEqual(expected["바퀴벌레 퇴치 방법 알려줘"], "out_of_scope")

    def test_v01_cases_are_kept(self) -> None:
        v01 = load_scope_cases()
        self.assertTrue({c["request"] for c in v01} <= {c["request"] for c in self.cases})

    def test_prompt_mentions_living_conditions_and_leans_in_scope(self) -> None:
        self.assertEqual(SCOPE_PROMPT_VERSION, "special_clause_scope_v0.2")
        for phrase in ("반려동물", "해충", "애매하면 out_of_scope보다 in_scope"):
            self.assertIn(phrase, SCOPE_SYSTEM_PROMPT)

    def test_offline_run_on_v02_is_perfect(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            summary = run_scope_eval(output_dir=Path(tmp), cases_path=self.path)
        self.assertEqual(summary.correct, summary.total)


if __name__ == "__main__":
    unittest.main()
