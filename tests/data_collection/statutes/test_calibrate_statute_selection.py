import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / "ml" / "data_collection" / "statutes"
sys.path.insert(0, str(SCRIPTS_DIR))

from calibrate_statute_selection import (  # noqa: E402
    calculate_metrics,
    find_parent_decision,
    suggest_decision,
)


def result(**overrides: object) -> dict:
    base = {
        "law_name": "테스트법",
        "title_direct_terms": [],
        "title_supporting_terms": [],
        "core_article_terms": [],
        "supporting_article_terms": [],
        "scope_warnings": [],
    }
    base.update(overrides)
    return base


class StatuteSelectionCalibrationTest(unittest.TestCase):
    def test_parent_decision_is_inherited_by_enforcement_decree(self) -> None:
        labels = {"주택법": {"human_decision": "포함"}}
        self.assertEqual(
            find_parent_decision("주택법 시행령", labels),
            ("포함", "주택법"),
        )

    def test_scope_warning_is_auto_exclude_candidate(self) -> None:
        status, _ = suggest_decision(
            result(scope_warnings=["상가"]),
            {},
        )
        self.assertEqual(status, "자동 제외 후보")

    def test_direct_title_and_core_article_is_auto_include_candidate(self) -> None:
        status, _ = suggest_decision(
            result(title_direct_terms=["주택임대차"], core_article_terms=["대항력"]),
            {},
        )
        self.assertEqual(status, "자동 포함 후보")

    def test_no_signal_is_auto_exclude_candidate(self) -> None:
        status, _ = suggest_decision(result(), {})
        self.assertEqual(status, "자동 제외 후보")

    def test_mixed_weak_signal_requires_review(self) -> None:
        status, _ = suggest_decision(
            result(title_supporting_terms=["주택"]),
            {},
        )
        self.assertEqual(status, "추가 검토 필요")

    def test_metrics_only_score_automatic_suggestions(self) -> None:
        metrics = calculate_metrics(
            [
                {"human_decision": "포함", "suggestion": "자동 포함 후보"},
                {"human_decision": "제외", "suggestion": "자동 제외 후보"},
                {"human_decision": "포함", "suggestion": "추가 검토 필요"},
            ]
        )
        self.assertEqual(metrics["auto_decided"], 2)
        self.assertEqual(metrics["accuracy_on_auto_decided"], 1.0)


if __name__ == "__main__":
    unittest.main()
