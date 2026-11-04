"""
Description: Gemini 특약 응답의 draft_ready·needs_input 분기를 서버가 검증하는지 확인한다.
모델이 약속을 어긴 응답을 줘도 불완전한 초안이 사용자에게 노출되지 않아야 한다.
Author: ooheunsu
Date: 2026-11-04
Before:
    - 분기 검증은 일부 경우(근거 ID, needs_input의 초안 제거)만 테스트되어 있었음.
After:
    - 필수 필드 누락, 중복·미지정 항목, 질문이 남은 초안 등 분기 경계를 회귀 검증함.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from api.services.gemini_special_clause_generator import GeminiSpecialClauseGenerator
from ml.agent.contract_review_graph import (
    ContractReviewEvidence,
    ContractReviewRetrievalQuery,
)

SOURCE = "law:001248:article:0003"


def _item(**overrides):
    item = {
        "item_id": "request-1",
        "status": "draft_ready",
        "draft_text": "임대인은 계약기간 중 추가 담보권을 설정하지 않는다.",
        "title": "추가 담보권 설정 금지",
        "reason": "필요한 이유입니다.",
        "strategy": "합의할 범위입니다.",
        "needs_input": [],
        "evidence_source_ids": [SOURCE],
    }
    item.update(overrides)
    return item


class SpecialClauseBranchValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.generator = GeminiSpecialClauseGenerator(
            api_key="test-key", usage_manifest_path=None
        )
        self.queries = [
            ContractReviewRetrievalQuery(
                item_id=f"request-{index}",
                category="special_clause_request",
                query="특약 작성 요청",
            )
            for index in (1, 2)
        ]
        self.evidence = [
            ContractReviewEvidence(
                item_id=f"request-{index}",
                source_type="statute",
                source_id=SOURCE,
                title="주택임대차보호법 제3조",
                text="법령 원문",
                rank=1,
                similarity=0.9,
                metadata={},
            )
            for index in (1, 2)
        ]

    def _generate(self, items):
        response = {
            "candidates": [
                {"content": {"parts": [{"text": json.dumps({"items": items})}]}}
            ]
        }
        with patch.object(self.generator, "_post_json", return_value=response):
            return self.generator.generate(self.queries, self.evidence)

    def test_draft_ready_requires_draft_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "초안이 비어"):
            self._generate([_item(draft_text="")])

    def test_draft_ready_requires_reason_and_strategy(self) -> None:
        with self.assertRaisesRegex(ValueError, "설명 형식"):
            self._generate([_item(reason="")])
        with self.assertRaisesRegex(ValueError, "설명 형식"):
            self._generate([_item(strategy="")])

    def test_needs_input_requires_questions(self) -> None:
        with self.assertRaisesRegex(ValueError, "추가 입력 요청이 비어"):
            self._generate([_item(status="needs_input", needs_input=[])])

    def test_draft_with_open_questions_is_treated_as_needs_input(self) -> None:
        result = self._generate([_item(needs_input=["금지 기한을 알려주세요."])])

        self.assertEqual(result[0].status, "needs_input")
        self.assertIsNone(result[0].draft_text)
        self.assertIsNone(result[0].reason)
        self.assertIsNone(result[0].strategy)
        self.assertEqual(result[0].needs_input, ["금지 기한을 알려주세요."])

    def test_draft_ready_keeps_empty_questions(self) -> None:
        result = self._generate([_item()])

        self.assertEqual(result[0].status, "draft_ready")
        self.assertEqual(result[0].needs_input, [])
        self.assertTrue(result[0].draft_text)

    def test_unknown_status_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "알 수 없는"):
            self._generate([_item(status="done")])

    def test_unknown_item_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "알 수 없는"):
            self._generate([_item(item_id="request-9")])

    def test_duplicate_item_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "알 수 없는"):
            self._generate([_item(), _item()])

    def test_missing_evidence_ids_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "근거가 비어"):
            self._generate([_item(evidence_source_ids=[])])

    def test_too_many_questions_are_rejected(self) -> None:
        questions = [f"질문 {index}번 내용" for index in range(6)]
        with self.assertRaisesRegex(ValueError, "추가 입력 요청 형식"):
            self._generate([_item(status="needs_input", needs_input=questions)])

    def test_short_title_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "제목 형식"):
            self._generate([_item(title="가")])

    def test_each_request_gets_its_own_branch(self) -> None:
        result = self._generate(
            [
                _item(),
                _item(
                    item_id="request-2",
                    status="needs_input",
                    title="추가 정보 확인",
                    draft_text="",
                    reason="",
                    strategy="",
                    needs_input=["적용 기간을 알려주세요."],
                ),
            ]
        )

        self.assertEqual([item.status for item in result], ["draft_ready", "needs_input"])


if __name__ == "__main__":
    unittest.main()
