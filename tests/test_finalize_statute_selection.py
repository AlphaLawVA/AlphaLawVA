import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from finalize_statute_selection import finalize_row, validate_decisions  # noqa: E402


DECISIONS = {
    "related_priority": {"include": ["포함법"], "reason": "사람 검토"},
    "boundary_cases": {"reason": "경계 제외"},
    "likely_excluded": {"reason": "자동 제외"},
}


class FinalizeStatuteSelectionTest(unittest.TestCase):
    def test_related_priority_uses_explicit_include_list(self) -> None:
        included = {
            "law_name": "포함법",
            "selection_status": "3차 사람 검토 필요",
            "review_recommendation": "관련성 확인 우선",
        }
        excluded = dict(included, law_name="제외법")
        self.assertEqual(finalize_row(included, DECISIONS)[0], "포함")
        self.assertEqual(finalize_row(excluded, DECISIONS)[0], "제외")

    def test_boundary_and_likely_excluded_are_excluded(self) -> None:
        for direction in ("경계 사례", "제외 가능성 높음"):
            row = {
                "law_name": "테스트법",
                "selection_status": "3차 사람 검토 필요",
                "review_recommendation": direction,
            }
            self.assertEqual(finalize_row(row, DECISIONS)[0], "제외")

    def test_validate_decisions_rejects_missing_include_name(self) -> None:
        rows = [
            {"law_name": f"법{index}", "review_recommendation": "관련성 확인 우선"}
            for index in range(12)
        ]
        with self.assertRaises(ValueError):
            validate_decisions(rows, DECISIONS)


if __name__ == "__main__":
    unittest.main()
