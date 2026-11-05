"""
Description: 재작성 요청 시 Gemini 입력에 이전 문제 안내가 해당 항목에만 붙는지 검증한다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 분석기와 생성기가 재작성 안내를 입력으로 받는 경로가 없는 상태.
After:
    - feedback이 있으면 해당 항목에 previous_issues를 넣고 없으면 기존 입력과 같음.
"""

import unittest

from api.services.gemini_contract_review_analyzer import GeminiContractReviewAnalyzer
from api.services.gemini_special_clause_generator import GeminiSpecialClauseGenerator
from ml.agent.contract_review_graph import (
    ContractReviewEvidence,
    ContractReviewRetrievalQuery,
)


def _inputs():
    queries = [
        ContractReviewRetrievalQuery(item_id=f"item-{i}", category="special_term", query="질의")
        for i in (1, 2)
    ]
    evidence = [
        ContractReviewEvidence(
            item_id=f"item-{i}",
            source_type="statute",
            source_id=f"law:{i}",
            title="조문",
            text="원문",
            rank=1,
            similarity=0.9,
            metadata={},
        )
        for i in (1, 2)
    ]
    return queries, evidence


class FeedbackPayloadTests(unittest.TestCase):
    def _builders(self):
        analyzer = GeminiContractReviewAnalyzer(api_key="k", usage_manifest_path=None)
        generator = GeminiSpecialClauseGenerator(api_key="k", usage_manifest_path=None)
        return (analyzer, generator)

    def test_feedback_is_attached_only_to_matching_items(self) -> None:
        queries, evidence = _inputs()
        for builder in self._builders():
            payload = builder._build_payload(
                queries, evidence, {"item-1": ["무효 표현을 쓰지 마세요."]}
            )
            by_id = {item["item_id"]: item for item in payload["items"]}
            self.assertEqual(by_id["item-1"]["previous_issues"], ["무효 표현을 쓰지 마세요."])
            self.assertNotIn("previous_issues", by_id["item-2"])

    def test_without_feedback_payload_has_no_previous_issues(self) -> None:
        queries, evidence = _inputs()
        for builder in self._builders():
            payload = builder._build_payload(queries, evidence)
            self.assertTrue(all("previous_issues" not in item for item in payload["items"]))


if __name__ == "__main__":
    unittest.main()
