import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / "ml" / "data_collection" / "statutes"
sys.path.insert(0, str(SCRIPTS_DIR))

from triage_statute_selection_v03 import (  # noqa: E402
    choose_stratified_sample,
    triage_candidate,
)


RULES = {
    "minimum_review_score": 4,
    "review_title_terms": ["주택", "등기", "민사"],
    "sample_size": 2,
    "sample_seed": "test",
}


def row(**overrides: object) -> dict:
    base = {
        "law_id": "000001",
        "law_name": "테스트법",
        "classification": {
            "score": 1,
            "core_article_terms": [],
            "supporting_article_terms": [],
        },
        "analysis": {
            "purpose_domain_terms": [],
            "same_context_article_count": 0,
        },
    }
    base.update(overrides)
    return base


class StatuteSelectionTriageV03Test(unittest.TestCase):
    def test_high_score_requires_review(self) -> None:
        item = row()
        item["classification"]["score"] = 4
        status, _, _ = triage_candidate(item, RULES)
        self.assertEqual(status, "3차 사람 검토 필요")

    def test_protected_title_requires_review(self) -> None:
        status, _, terms = triage_candidate(row(law_name="주택 관련 규칙"), RULES)
        self.assertEqual(status, "3차 사람 검토 필요")
        self.assertEqual(terms, ["주택"])

    def test_purpose_and_same_context_requires_review(self) -> None:
        item = row()
        item["analysis"]["purpose_domain_terms"] = ["임대차"]
        item["analysis"]["same_context_article_count"] = 1
        status, _, _ = triage_candidate(item, RULES)
        self.assertEqual(status, "3차 사람 검토 필요")

    def test_weak_candidate_is_auto_exclude(self) -> None:
        status, _, _ = triage_candidate(row(), RULES)
        self.assertEqual(status, "3차 자동 제외 후보")

    def test_sample_is_deterministic(self) -> None:
        rows = [row(law_id=f"{index:06}") for index in range(5)]
        first = choose_stratified_sample(rows, RULES)
        second = choose_stratified_sample(rows, RULES)
        self.assertEqual(
            [item["law_id"] for item in first],
            [item["law_id"] for item in second],
        )


if __name__ == "__main__":
    unittest.main()
