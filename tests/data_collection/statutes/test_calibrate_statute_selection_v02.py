import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / "ml" / "data_collection" / "statutes"
sys.path.insert(0, str(SCRIPTS_DIR))

from calibrate_statute_selection_v02 import (  # noqa: E402
    analyze_detail,
    suggest_decision_v02,
)


RULES = {
    "direct_family_terms": ["주택", "등기", "주민등록"],
    "purpose_domain_terms": ["주택", "임대차", "부동산"],
    "explicit_out_of_domain_title_terms": ["국민건강보험", "자동차"],
    "purpose_article_title_patterns": ["목적", "적용 범위", "정의"],
    "max_purpose_excerpt_length": 100,
}
RELEVANCE_RULES = {
    "core_article_terms": ["대항력", "확정일자"],
    "residential_anchor_terms": ["주택", "임대차", "임차인"],
}


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


class StatuteSelectionCalibrationV02Test(unittest.TestCase):
    def test_out_of_domain_precedes_parent_inheritance(self) -> None:
        labels = {"국민건강보험법": {"human_decision": "포함"}}
        status, _ = suggest_decision_v02(
            result(law_name="국민건강보험법 시행령"),
            {"purpose_domain_terms": [], "same_context_article_count": 0},
            labels,
            RULES,
        )
        self.assertEqual(status, "자동 제외 후보")

    def test_included_parent_without_own_evidence_requires_review(self) -> None:
        labels = {"국세기본법": {"human_decision": "포함"}}
        status, _ = suggest_decision_v02(
            result(law_name="국세기본법 시행령"),
            {"purpose_domain_terms": [], "same_context_article_count": 0},
            labels,
            RULES,
        )
        self.assertEqual(status, "추가 검토 필요")

    def test_included_parent_and_direct_family_signal_can_include(self) -> None:
        labels = {"주민등록법": {"human_decision": "포함"}}
        status, _ = suggest_decision_v02(
            result(law_name="주민등록법 시행령"),
            {"purpose_domain_terms": ["주택"], "same_context_article_count": 0},
            labels,
            RULES,
        )
        self.assertEqual(status, "자동 포함 후보")

    def test_analyze_detail_requires_core_and_anchor_in_same_article(self) -> None:
        detail = {
            "법령": {
                "조문": {
                    "조문단위": [
                        {
                            "조문번호": "1",
                            "조문제목": "목적",
                            "조문내용": "주택 임대차의 안정을 목적으로 한다.",
                        },
                        {
                            "조문번호": "2",
                            "조문제목": "권리",
                            "조문내용": "임차인은 대항력을 가진다.",
                        },
                    ]
                }
            }
        }
        analysis = analyze_detail(detail, RELEVANCE_RULES, RULES)
        self.assertIn("주택", analysis["purpose_domain_terms"])
        self.assertEqual(analysis["same_context_article_count"], 1)


if __name__ == "__main__":
    unittest.main()
