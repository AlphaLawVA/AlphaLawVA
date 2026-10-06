# test_generate_easy_definitions_local.py
"""
Description: 쉬운 법률 정의 실험의 층화 표본과 다중 정의 1:1 검증을 확인한다.
Author: choeminju
Date: 2026-10-05
Before:
    - 로컬 쉬운 정의 실험 모듈이 구현된 상태.
After:
    - 표본 개수, 정의 순서와 누락 검증이 자동 확인됨.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ml.evaluation.legal_terms.generate_easy_definitions_local import (
    build_sample,
    load_prepared_sample,
    validate_output,
)


def make_row(term: str, count: int, *, ready: bool = False) -> dict:
    """테스트용 용어 검토 행을 만든다."""
    return {
        "match_key": term,
        "term": term,
        "source_type": "full_term",
        "source_domains": ["precedent"],
        "legal_definition_count": count,
        "legal_definitions": [
            {
                "target_code": f"{term}-{index}",
                "sense_no": f"{index:03d}",
                "definition": f"{term} 정의 {index}" + ("가" * index),
                "category": "법률",
                "source_link": f"https://example.test/{term}/{index}",
            }
            for index in range(1, count + 1)
        ],
        "review_status": "ready" if ready else "sense_selection_required",
        "include_in_glossary": True,
        "easy_definition_ready": ready,
    }


class EasyDefinitionExperimentTests(unittest.TestCase):
    """표본 구성과 모델 출력 검증 규칙을 확인한다."""

    def test_build_sample_keeps_requested_single_and_multi_counts(self) -> None:
        single_rows = [make_row(f"단일{i}", 1, ready=True) for i in range(12)]
        review_rows = [make_row(f"다중{i}", 2 + (i % 3)) for i in range(24)]

        sample = build_sample(
            single_rows,
            review_rows,
            single_count=10,
            multi_count=20,
        )

        self.assertEqual(len(sample), 30)
        self.assertEqual(
            sum(row["sample_group"] == "single" for row in sample),
            10,
        )
        self.assertEqual(
            sum(row["sample_group"] == "multi" for row in sample),
            20,
        )
        self.assertTrue(any(row["definition_count"] >= 3 for row in sample))

    def test_validate_output_accepts_exact_definition_mapping(self) -> None:
        row = build_sample(
            [make_row("단일", 1, ready=True)],
            [make_row("다중", 2)],
            single_count=1,
            multi_count=1,
        )[0]
        parsed = {
            "term": row["term"],
            "easy_definitions": [
                {
                    "definition_id": definition["definition_id"],
                    "hard_words": [],
                    "easy_definition": "쉽게 쓴 정의",
                }
                for definition in row["definitions"]
            ],
        }

        self.assertEqual(validate_output(row, parsed), [])

    def test_validate_output_rejects_missing_or_reordered_definition(self) -> None:
        row = build_sample(
            [make_row("단일", 1, ready=True)],
            [make_row("다중", 2)],
            single_count=1,
            multi_count=1,
        )[0]
        parsed = {
            "term": row["term"],
            "easy_definitions": [
                {
                    "definition_id": row["definitions"][1]["definition_id"],
                    "hard_words": [],
                    "easy_definition": "순서가 바뀐 정의",
                }
            ],
        }

        self.assertEqual(
            validate_output(row, parsed),
            ["definition_id_order_or_count_mismatch"],
        )

    def test_validate_output_rejects_missing_hard_words(self) -> None:
        row = build_sample(
            [make_row("단일", 1, ready=True)],
            [make_row("다중", 2)],
            single_count=1,
            multi_count=1,
        )[0]
        parsed = {
            "term": row["term"],
            "easy_definitions": [
                {
                    "definition_id": definition["definition_id"],
                    "easy_definition": "쉽게 쓴 정의",
                }
                for definition in row["definitions"]
            ],
        }

        self.assertEqual(
            validate_output(row, parsed),
            [
                "hard_words_not_list:0",
                "hard_words_not_list:1",
            ],
        )

    def test_load_prepared_sample_derives_group_from_definition_count(self) -> None:
        rows = [
            {
                "match_key": "다중",
                "term": "다중",
                "definitions": [
                    {
                        "definition_id": "d1",
                        "category": "법률",
                        "source_definition": "첫 번째 정의",
                    },
                    {
                        "definition_id": "d2",
                        "category": "법률",
                        "source_definition": "두 번째 정의",
                    },
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.jsonl"
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )

            sample = load_prepared_sample(path)

        self.assertEqual(sample[0]["definition_count"], 2)
        self.assertEqual(sample[0]["sample_group"], "multi")


if __name__ == "__main__":
    unittest.main()
