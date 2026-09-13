# test_evaluate_parent_child_statute_retrieval.py
"""
Description: 자식 검색 결과의 부모 조문 중복 제거와 조문 라벨 평가를
실제 ChromaDB 및 모델 호출 없이 검증한다.
Author: ooheunsu
Date: 2026-09-05
Before:
    - 부모 조문 기준 검색 평가 코드가 작성된 상태.
After:
    - 자식 순위 보존, 부모 중복 제거, Top-K 부족 오류를 자동 검증 가능.
"""

import unittest

from ml.evaluation.evaluate_parent_child_statute_retrieval import (
    collapse_child_results_to_parents,
    compare_with_baseline,
)


def result(parent_ids: list[str]) -> dict:
    count = len(parent_ids)
    return {
        "ids": [[f"child-{index}" for index in range(count)]],
        "documents": [[f"document-{index}" for index in range(count)]],
        "metadatas": [
            [{"parent_article_id": parent_id} for parent_id in parent_ids]
        ],
        "distances": [[index / 10 for index in range(count)]],
    }


class EvaluateParentChildStatuteRetrievalTests(unittest.TestCase):
    def test_collapses_duplicate_parents_using_best_child_rank(self):
        rankings = collapse_child_results_to_parents(
            result(["article-a", "article-a", "article-b"]),
            ["q1"],
            parent_top_k=2,
        )["q1"]

        self.assertEqual([row["chunk_id"] for row in rankings], ["article-a", "article-b"])
        self.assertEqual(rankings[0]["child_rank"], 1)
        self.assertEqual(rankings[1]["child_rank"], 3)

    def test_rejects_child_without_parent_id(self):
        values = result(["article-a"])
        values["metadatas"][0][0] = {}

        with self.assertRaisesRegex(ValueError, "부모 조문 ID"):
            collapse_child_results_to_parents(values, ["q1"], parent_top_k=1)

    def test_rejects_too_few_unique_parents(self):
        with self.assertRaisesRegex(ValueError, "자식 후보가 부족"):
            collapse_child_results_to_parents(
                result(["article-a", "article-a"]),
                ["q1"],
                parent_top_k=2,
            )

    def test_compares_parent_child_metrics_with_article_baseline(self):
        result_template = {
            "macro": {
                "recall_at_5": 0.5,
                "recall_at_10": 0.7,
                "mrr_at_10": 0.6,
                "ndcg_at_10": 0.6,
                "hit_at_10": 0.8,
            },
            "critical": {"incomplete_recall_at_10": ["q1"]},
        }
        parent_child = {"models": {"bge_m3": result_template}}
        baseline_result = {
            **result_template,
            "macro": {**result_template["macro"], "recall_at_10": 0.6},
        }

        comparison = compare_with_baseline(
            parent_child,
            {"models": {"bge_m3": baseline_result}},
        )

        self.assertAlmostEqual(
            comparison["metric_deltas"]["bge_m3"]["recall_at_10"],
            0.1,
        )
        self.assertEqual(
            comparison["candidate_order"][0],
            "bge_m3:parent_child",
        )


if __name__ == "__main__":
    unittest.main()
