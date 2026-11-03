"""
Description: Gemini 특약 생성기의 구조화 응답과 근거 ID 검증을 확인한다.
외부 호출 없이 주입된 응답으로 허용된 특약 초안만 반환되는지 검증한다.
Author: ooheunsu
Date: 2026-10-31
Before:
    - Gemini 특약 생성기의 근거 ID 검증 테스트가 없는 상태.
After:
    - 알 수 없는 요청과 제공되지 않은 근거 ID를 회귀 테스트할 수 있음.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from api.services.gemini_special_clause_generator import GeminiSpecialClauseGenerator
from ml.agent.contract_review_graph import (
    ContractReviewEvidence,
    ContractReviewRetrievalQuery,
)


class GeminiSpecialClauseGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.generator = GeminiSpecialClauseGenerator(
            api_key="test-key",
            usage_manifest_path=None,
        )
        self.queries = [
            ContractReviewRetrievalQuery(
                item_id="request-1",
                category="special_clause_request",
                query="부동산 계약 특약 작성 요청: 추가 담보권 설정 금지",
            )
        ]
        self.evidence = [
            ContractReviewEvidence(
                item_id="request-1",
                source_type="statute",
                source_id="law:001248:article:0003",
                title="주택임대차보호법 제3조",
                text="법령 원문",
                rank=1,
                similarity=0.9,
                metadata={},
            )
        ]

    def test_returns_draft_with_provided_evidence_id(self):
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    '{"items":[{"item_id":"request-1",'
                                    '"status":"draft_ready",'
                                    '"draft_text":"임대인은 계약기간 중 추가 담보권을 설정하지 않는다.",'
                                    '"title":"추가 담보권 설정 금지",'
                                    '"reason":"보증금 회수 위험을 줄이기 위한 문구입니다.",'
                                    '"strategy":"금지 기간과 위반 시 처리 방법을 합의하세요.",'
                                    '"needs_input":[],"evidence_source_ids":'
                                    '["law:001248:article:0003"]}]}'
                                )
                            }
                        ]
                    }
                }
            ],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
        }
        with patch.object(self.generator, "_post_json", return_value=response):
            result = self.generator.generate(self.queries, self.evidence)

        self.assertEqual(result[0].status, "draft_ready")
        self.assertEqual(result[0].title, "추가 담보권 설정 금지")
        self.assertEqual(result[0].evidence_source_ids, ["law:001248:article:0003"])

    def test_rejects_unprovided_evidence_id(self):
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    '{"items":[{"item_id":"request-1",'
                                    '"status":"needs_input","draft_text":"",'
                                    '"title":"추가 정보 확인","reason":"","strategy":"",'
                                    '"needs_input":["적용 기간을 알려주세요."],'
                                    '"evidence_source_ids":["invented-source"]}]}'
                                )
                            }
                        ]
                    }
                }
            ]
        }
        with patch.object(self.generator, "_post_json", return_value=response):
            with self.assertRaisesRegex(ValueError, "제공되지 않은 근거"):
                self.generator.generate(self.queries, self.evidence)

    def test_discards_draft_text_when_more_input_is_required(self):
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    '{"items":[{"item_id":"request-1",'
                                    '"status":"needs_input",'
                                    '"draft_text":"기한을 정한 특약 초안입니다.",'
                                    '"title":"추가 정보 확인","reason":"설명","strategy":"전략",'
                                    '"needs_input":["금지 기한을 알려주세요."],'
                                    '"evidence_source_ids":'
                                    '["law:001248:article:0003"]}]}'
                                )
                            }
                        ]
                    }
                }
            ]
        }
        with patch.object(self.generator, "_post_json", return_value=response):
            result = self.generator.generate(self.queries, self.evidence)

        self.assertEqual(result[0].status, "needs_input")
        self.assertIsNone(result[0].draft_text)
        self.assertEqual(result[0].needs_input, ["금지 기한을 알려주세요."])


if __name__ == "__main__":
    unittest.main()
