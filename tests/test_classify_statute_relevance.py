import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from classify_statute_relevance import (  # noqa: E402
    classify_law,
    extract_article_units,
    extract_nested_contents,
    make_excerpt,
)


RULES = {
    "direct_title_terms": ["주택임대차"],
    "supporting_title_terms": ["주택", "부동산"],
    "core_article_terms": ["대항력", "우선변제권", "확정일자"],
    "supporting_article_terms": ["임대인", "임차인"],
    "residential_anchor_terms": ["주택", "임대차", "임대인", "임차인"],
    "scope_warning_terms": ["상가"],
    "scoring": {
        "seed_candidate": 8,
        "direct_title_match": 6,
        "supporting_title_match": 2,
        "core_article_match": 2,
        "supporting_article_match": 1,
        "scope_warning": -2,
        "direct_score": 9,
        "indirect_score": 2,
    },
    "max_evidence_articles": 3,
    "max_excerpt_length": 80,
}


def detail(name: str, contents: list[str]) -> dict:
    return {
        "법령": {
            "기본정보": {"법령명_한글": name},
            "조문": {
                "조문단위": [
                    {
                        "조문번호": str(index),
                        "조문제목": "테스트",
                        "조문내용": content,
                    }
                    for index, content in enumerate(contents, start=1)
                ]
            },
        }
    }


class StatuteRelevanceTest(unittest.TestCase):
    def test_extract_article_units_accepts_single_object(self) -> None:
        payload = {
            "법령": {
                "조문": {
                    "조문단위": {
                        "조문번호": "1",
                        "조문제목": "목적",
                        "조문내용": "주택의 임대차를 정한다.",
                    }
                }
            }
        }
        units = extract_article_units(payload)
        self.assertEqual(units[0]["article_number"], "1")

    def test_extract_article_units_includes_nested_paragraphs_and_items(self) -> None:
        payload = {
            "법령": {
                "조문": {
                    "조문단위": {
                        "조문번호": "3",
                        "조문제목": "적용 범위",
                        "조문내용": "제3조(적용 범위)",
                        "항": {
                            "항내용": "주택 임대차에 적용한다.",
                            "호": {"호내용": "임차인은 대항력을 가진다."},
                        },
                    }
                }
            }
        }
        units = extract_article_units(payload)
        self.assertIn("주택 임대차에 적용한다.", units[0]["content"])
        self.assertIn("임차인은 대항력을 가진다.", units[0]["content"])

    def test_extract_nested_contents_handles_list_and_single_object(self) -> None:
        value = [
            {"항내용": "첫째 항", "호": [{"호내용": "첫째 호"}]},
            {"항내용": "둘째 항"},
        ]
        self.assertEqual(
            extract_nested_contents(value),
            ["첫째 항", "첫째 호", "둘째 항"],
        )

    def test_seed_candidate_is_direct_even_without_keyword(self) -> None:
        result = classify_law(
            {"law_id": "1", "law_name": "민법"},
            detail("민법", ["계약의 일반 원칙을 정한다."]),
            RULES,
            is_seed=True,
        )
        self.assertEqual(result["automatic_label"], "직접 관련")

    def test_single_incidental_core_match_is_indirect(self) -> None:
        result = classify_law(
            {"law_id": "2", "law_name": "행정절차법"},
            detail("행정절차법", ["서식에 확정일자를 기록한다."]),
            RULES,
            is_seed=False,
        )
        self.assertEqual(result["automatic_label"], "간접 관련")

    def test_title_match_without_article_evidence_is_indirect(self) -> None:
        result = classify_law(
            {"law_id": "5", "law_name": "주택임대차 행정규칙"},
            detail("주택임대차 행정규칙", ["기관의 내부 사무를 정한다."]),
            RULES,
            is_seed=False,
        )
        self.assertEqual(result["automatic_label"], "간접 관련")

    def test_scope_warning_prevents_non_seed_direct_label(self) -> None:
        result = classify_law(
            {"law_id": "6", "law_name": "상가 주택임대차법"},
            detail(
                "상가 주택임대차법",
                [
                    "임대인과 임차인의 대항력을 정한다.",
                    "임차인의 우선변제권과 확정일자를 정한다.",
                ],
            ),
            RULES,
            is_seed=False,
        )
        self.assertEqual(result["automatic_label"], "간접 관련")

    def test_unrelated_text_is_noise_candidate(self) -> None:
        result = classify_law(
            {"law_id": "3", "law_name": "보건법"},
            detail("보건법", ["의료기관의 운영 기준을 정한다."]),
            RULES,
            is_seed=False,
        )
        self.assertEqual(result["automatic_label"], "노이즈 가능")

    def test_evidence_contains_article_identity(self) -> None:
        result = classify_law(
            {"law_id": "4", "law_name": "주택임대차법"},
            detail("주택임대차법", ["임차인은 대항력을 취득한다."]),
            RULES,
            is_seed=False,
        )
        self.assertEqual(result["evidence"][0]["article_number"], "1")
        self.assertIn("대항력", result["evidence"][0]["matched_terms"])

    def test_excerpt_is_bounded(self) -> None:
        excerpt = make_excerpt("가" * 100 + "대항력" + "나" * 100, ["대항력"], 40)
        self.assertLessEqual(len(excerpt), 46)
        self.assertIn("대항력", excerpt)


if __name__ == "__main__":
    unittest.main()
