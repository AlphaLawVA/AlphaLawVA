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

import json
import tempfile
import unittest
from pathlib import Path
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
                                    '"review_status":"check",'
                                    '"revision":"적용 범위와 위반 시 처리 방법을 합의해 기재한다.",'
                                    '"negotiation":"종료 시점과 책임 범위를 상대방과 확인하세요.",'
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
        self.assertEqual(result[0].review_status, "check")
        self.assertEqual(result[0].evidence_source_ids, ["law:001248:article:0003"])

    def _response(self, item: dict) -> dict:
        return {
            "candidates": [{"content": {"parts": [{"text": json.dumps({"items": [item]}, ensure_ascii=False)}]}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
        }

    def test_safe_item_drops_revision_and_negotiation(self):
        item = {
            "item_id": "term-1",
            "summary": "제공된 조문과 취지가 같아 따로 확인할 쟁점이 없습니다.",
            "review_status": "safe",
            "revision": "모델이 채운 수정안은 버려야 합니다.",
            "negotiation": "모델이 채운 협상 안내도 버립니다.",
            "evidence_source_ids": ["law:001248:article:0003"],
        }
        with patch.object(self.analyzer, "_post_json", return_value=self._response(item)):
            result = self.analyzer.analyze(self.queries, self.evidence)
        self.assertEqual(result[0].review_status, "safe")
        self.assertEqual(result[0].revision, "")
        self.assertEqual(result[0].negotiation, "")

    def test_safe_item_may_omit_revision_and_negotiation(self):
        item = {
            "item_id": "term-1",
            "summary": "제공된 조문과 취지가 같아 따로 확인할 쟁점이 없습니다.",
            "review_status": "safe",
            "evidence_source_ids": ["law:001248:article:0003"],
        }
        with patch.object(self.analyzer, "_post_json", return_value=self._response(item)):
            result = self.analyzer.analyze(self.queries, self.evidence)
        self.assertEqual(result[0].revision, "")

    def test_check_item_still_requires_revision_and_negotiation(self):
        item = {
            "item_id": "term-1",
            "summary": "적용 범위가 모호해 확인이 필요합니다.",
            "review_status": "check",
            "evidence_source_ids": ["law:001248:article:0003"],
        }
        with patch.object(self.analyzer, "_post_json", return_value=self._response(item)):
            with self.assertRaises(ValueError):  # 수정안 없는 check 항목은 서버 검증에서 거절된다
                self.analyzer.analyze(self.queries, self.evidence)

    def test_prompt_explains_summary_as_judgement_reason(self):
        from api.services.gemini_contract_review_analyzer import SYSTEM_PROMPT

        self.assertIn("왜", SYSTEM_PROMPT)
        self.assertIn("safe이면 revision과 negotiation은 반드시 빈 문자열", SYSTEM_PROMPT)

    def test_drops_only_the_item_that_cites_unprovided_evidence(self):
        self.queries.append(
            ContractReviewRetrievalQuery(
                item_id="term-2",
                category="special_term",
                query="jeonse 계약 특약 검토: 수리 책임",
            )
        )
        self.evidence.append(
            ContractReviewEvidence(
                item_id="term-2",
                source_type="statute",
                source_id="law:001248:article:0006",
                title="주택임대차보호법 제6조",
                text="법령 원문",
                rank=1,
                similarity=0.8,
                metadata={},
            )
        )
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    '{"items":['
                                    '{"item_id":"term-1","summary":"확인 필요: 범위를 비교해 주세요.",'
                                    '"review_status":"check","revision":"범위를 합의해 기재한다.",'
                                    '"negotiation":"책임 범위를 확인하세요.",'
                                    '"evidence_source_ids":["law:001248:article:0006"]},'
                                    '{"item_id":"term-2","summary":"확인 필요: 책임을 비교해 주세요.",'
                                    '"review_status":"check","revision":"책임을 합의해 기재한다.",'
                                    '"negotiation":"비용 부담을 확인하세요.",'
                                    '"evidence_source_ids":["law:001248:article:0006"]}]}'
                                )
                            }
                        ]
                    }
                }
            ]
        }
        with patch.object(self.analyzer, "_post_json", return_value=response):
            result = self.analyzer.analyze(self.queries, self.evidence)

        self.assertEqual([item.item_id for item in result], ["term-2"])

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
                                    '"review_status":"check",'
                                    '"revision":"적용 범위와 처리 방법을 합의한다.",'
                                    '"negotiation":"책임 범위를 확인하세요.",'
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

    def test_records_validation_error_message_in_usage_manifest(self):
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    '{"items":[{"item_id":"unknown",'
                                    '"summary":"확인 필요","review_status":"check",'
                                    '"revision":"수정 초안","negotiation":"협의 안내",'
                                    '"evidence_source_ids":["x"]}]}'
                                )
                            }
                        ]
                    }
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "usage.jsonl"
            analyzer = GeminiContractReviewAnalyzer(
                api_key="test-key", usage_manifest_path=manifest
            )
            with patch.object(analyzer, "_post_json", return_value=response):
                with self.assertRaises(ValueError):
                    analyzer.analyze(self.queries, self.evidence)
            record = json.loads(manifest.read_text(encoding="utf-8").splitlines()[-1])

        self.assertFalse(record["success"])
        self.assertEqual(record["error_type"], "ValueError")
        self.assertIn("알 수 없는 검토 항목", record["error_message"])

    def test_selects_statutes_and_precedents_by_separate_quota(self):
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
            ["statute", "statute", "statute", "precedent"],
        )


if __name__ == "__main__":
    unittest.main()
