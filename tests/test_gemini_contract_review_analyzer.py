# test_gemini_contract_review_analyzer.py
"""
Description: Gemini 계약서 검토 분석기의 구조화 응답과 근거 ID 검증을 확인한다.
외부 호출 없이 주입된 응답으로 허용된 근거만 반환되는지 검증한다.
Author: ooheunsu
Date: 2026-10-28
Before:
    - 계약서 검토 Gemini 분석기의 근거 연결 검증 테스트가 없는 상태.
After:
    - 알 수 없는 검토 항목과 제공되지 않은 근거 ID를 회귀 테스트할 수 있음.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from api.services.gemini_contract_review_analyzer import GeminiContractReviewAnalyzer
from ml.agent.contract_review_graph import (
    ContractReviewEvidence,
    ContractReviewRetrievalQuery,
)


class GeminiContractReviewAnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = GeminiContractReviewAnalyzer(
            api_key="test-key",
            usage_manifest_path=None,
        )
        self.queries = [
            ContractReviewRetrievalQuery(
                item_id="term-1",
                category="special_term",
                query="jeonse 계약 특약 검토: 추가 담보권 설정 금지",
            )
        ]
        self.evidence = [
            ContractReviewEvidence(
                item_id="term-1",
                source_type="statute",
                source_id="law:001248:article:0003",
                title="주택임대차보호법 제3조",
                text="법령 원문",
                rank=1,
                similarity=0.9,
                metadata={},
            )
        ]

    def test_returns_only_item_with_provided_evidence_id(self):
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    '{"items":[{"item_id":"term-1",'
                                    '"summary":"확인 필요: 특약 원문과 적용 범위를 비교해 주세요.",'
                                    '"evidence_source_ids":["law:001248:article:0003"]}]}'
                                )
                            }
                        ]
                    }
                }
            ],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
        }
        with patch.object(self.analyzer, "_post_json", return_value=response):
            result = self.analyzer.analyze(self.queries, self.evidence)

        self.assertEqual(result[0].item_id, "term-1")
        self.assertEqual(result[0].evidence_source_ids, ["law:001248:article:0003"])

    def test_rejects_unprovided_evidence_id(self):
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    '{"items":[{"item_id":"term-1",'
                                    '"summary":"확인 필요: 특약 원문과 적용 범위를 비교해 주세요.",'
                                    '"evidence_source_ids":["invented-source"]}]}'
                                )
                            }
                        ]
                    }
                }
            ]
        }
        with patch.object(self.analyzer, "_post_json", return_value=response):
            with self.assertRaisesRegex(ValueError, "제공되지 않은 근거"):
                self.analyzer.analyze(self.queries, self.evidence)

    def test_keeps_highest_similarity_evidence_without_source_balance(self):
        evidence = [
            *self.evidence,
            ContractReviewEvidence(
                item_id="term-1",
                source_type="statute",
                source_id="law:001248:article:0004",
                title="주택임대차보호법 제4조",
                text="다른 법령 원문",
                rank=2,
                similarity=0.8,
                metadata={},
            ),
            ContractReviewEvidence(
                item_id="term-1",
                source_type="statute",
                source_id="law:001248:article:0005",
                title="주택임대차보호법 제5조",
                text="추가 법령 원문",
                rank=3,
                similarity=0.7,
                metadata={},
            ),
            ContractReviewEvidence(
                item_id="term-1",
                source_type="precedent",
                source_id="precedent:64441",
                title="임대차보증금등",
                text="판례 원문",
                rank=1,
                similarity=0.2,
                metadata={},
            ),
        ]

        payload = self.analyzer._build_payload(self.queries, evidence)

        self.assertEqual(
            [item["source_type"] for item in payload["items"][0]["evidence"]],
            ["statute", "statute", "statute"],
        )


if __name__ == "__main__":
    unittest.main()
