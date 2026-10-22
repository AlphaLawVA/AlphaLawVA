# test_certified_letter_evaluation.py
"""
Description: 내용증명 오프라인 평가셋과 품질·안전 게이트를 검증한다.
정상 응답, 근거 없는 인용, 판례 본문 혼입과 누락 실행을 확인한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 합성 평가셋과 저장 응답 평가기가 구현된 상태.
After:
    - 핵심 알파 안전 게이트의 회귀를 자동 테스트로 차단할 수 있음.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from ml.evaluation.certified_letters.evaluate_outputs import (
    EvaluationCase,
    RecordedAttempt,
    evaluate,
    load_cases,
)
from ml.evaluation.certified_letters.run_generation import _usage_totals


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = (
    PROJECT_ROOT
    / "ml"
    / "evaluation"
    / "certified_letters"
    / "datasets"
    / "certified_letter_eval_v01.jsonl"
)
RUN_METADATA = {
    "provider": "test-provider",
    "model": "test-model",
    "prompt_version": "test-prompt-v1",
    "statute_embedding": "test-statute-embedding",
    "statute_chunking": "test-statute-chunking",
    "precedent_embedding": "test-precedent-embedding",
    "precedent_chunking": "test-precedent-chunking",
    "precedent_reranker": "test-reranker",
}


def ready_response(*, body: str | None = None) -> dict:
    return {
        "draft_id": "12345678-1234-5678-1234-567812345678",
        "status": "completed",
        "outcome": "draft_ready",
        "result": {
            "document": {
                "title": "전세보증금 반환 촉구서",
                "sender": {
                    "name": "이임차",
                    "address": "서울특별시 중구 테스트로 10",
                    "detail_address": "101동 1001호",
                },
                "recipient": {
                    "name": "김임대",
                    "address": "서울특별시 종로구 예시로 20",
                    "detail_address": None,
                },
                "body": body
                or (
                    "계약 종료에 따라 보증금 100,000,000원의 반환을 요청합니다. "
                    "「주택임대차보호법」 제4조를 근거로 이 내용증명을 받은 "
                    "날부터 7일 이내 이행해 주시기 바랍니다."
                ),
            },
            "strategy_summary": "계약 종료와 보증금 반환 요청을 통지합니다.",
            "follow_up_actions": [
                {"order": 1, "action": "계약 내용과 송달 주소를 확인합니다."}
            ],
            "statute_evidence": [
                {
                    "statute_id": "synthetic-statute-001",
                    "law_name": "주택임대차보호법",
                    "article_label": "제4조",
                    "article_title": None,
                    "content": "평가 테스트용 검색 근거입니다.",
                    "explanation": "본문 인용의 근거로 선택되었습니다.",
                    "effective_date": None,
                    "source_node_id": "synthetic-node-001",
                    "source_url": "https://example.test/statute/001",
                }
            ],
            "similar_precedents": [],
            "warnings": ["이 문서는 법률 검토 전 초안입니다."],
        },
        "missing_information": [],
        "message": None,
        "error": None,
        "created_at": "2026-10-22T05:30:00Z",
        "updated_at": "2026-10-22T05:30:01Z",
        "completed_at": "2026-10-22T05:30:01Z",
    }


def first_case() -> EvaluationCase:
    return load_cases(DATASET_PATH)[0]


class CertifiedLetterEvaluationTests(unittest.TestCase):
    def test_versioned_dataset_contains_only_valid_synthetic_cases(self):
        cases = load_cases(DATASET_PATH)

        self.assertEqual(len(cases), 6)
        self.assertTrue(all(item.is_synthetic for item in cases))
        self.assertEqual(len({item.case_id for item in cases}), len(cases))

    def test_passes_grounded_fact_consistent_draft(self):
        case = first_case()
        attempt = RecordedAttempt(
            case_id=case.case_id,
            response=ready_response(),
            latency_ms=1234.5,
            model_calls=1,
        )

        artifacts = evaluate(
            cases=[case],
            attempts=[attempt],
            run_metadata=RUN_METADATA,
            dataset_sha256="dataset-sha",
            attempts_sha256="attempts-sha",
        )

        self.assertTrue(artifacts.summary["overall_pass"])
        self.assertEqual(
            artifacts.summary["metrics"]["statute_citation_precision"],
            1.0,
        )
        self.assertEqual(
            artifacts.summary["metrics"]["contract_fact_consistency_rate"],
            1.0,
        )
        self.assertEqual(artifacts.summary["metrics"]["model_calls_total"], 1)

    def test_fails_unsupported_citation_invented_facts_and_precedent_body(self):
        case = first_case()
        response = ready_response(
            body=(
                "보증금 120,000,000원을 2026-12-31까지 반환하십시오. "
                "「가상법」 제999조와 대법원 판례에 따르면 받은 날부터 "
                "7일 이내 반환해야 합니다."
            )
        )
        attempt = RecordedAttempt(
            case_id=case.case_id,
            response=response,
            latency_ms=2000,
            model_calls=1,
        )

        artifacts = evaluate(
            cases=[case],
            attempts=[attempt],
            run_metadata=RUN_METADATA,
            dataset_sha256="dataset-sha",
            attempts_sha256="attempts-sha",
        )
        codes = set(artifacts.case_results[0]["validation_issue_codes"])

        self.assertFalse(artifacts.summary["overall_pass"])
        self.assertTrue(
            {
                "unsupported_statute_citation",
                "unexpected_money",
                "unexpected_date",
                "precedent_in_body",
            }.issubset(codes)
        )
        self.assertFalse(
            artifacts.summary["safety_gates"][
                "zero_unsupported_statute_citations"
            ]
        )

    def test_fails_when_attempt_is_missing(self):
        artifacts = evaluate(
            cases=[first_case()],
            attempts=[],
            run_metadata=RUN_METADATA,
            dataset_sha256="dataset-sha",
            attempts_sha256="attempts-sha",
        )

        self.assertFalse(artifacts.summary["overall_pass"])
        self.assertEqual(
            artifacts.case_results[0]["failure_reasons"],
            ["missing_attempt"],
        )

    def test_prompt_injection_case_checks_generated_text_not_json_field_names(self):
        case = load_cases(DATASET_PATH)[4]
        response = ready_response(
            body=(
                "보증금 60,000,000원의 반환을 요청합니다. "
                "「주택임대차보호법」 제4조를 근거로 이 내용증명을 받은 "
                "날부터 7일 이내 이행해 주시기 바랍니다."
            )
        )
        response["result"]["document"]["sender"] = {
            "name": "배임차",
            "address": "인천광역시 남동구 안전로 80",
            "detail_address": "404호",
        }
        response["result"]["document"]["recipient"] = {
            "name": "오임대",
            "address": "인천광역시 연수구 예시로 90",
            "detail_address": None,
        }
        attempt = RecordedAttempt(
            case_id=case.case_id,
            response=response,
            latency_ms=800,
            model_calls=1,
        )

        artifacts = evaluate(
            cases=[case],
            attempts=[attempt],
            run_metadata=RUN_METADATA,
            dataset_sha256="dataset-sha",
            attempts_sha256="attempts-sha",
        )

        self.assertTrue(artifacts.summary["overall_pass"])
        self.assertNotIn(
            "forbidden_output_term",
            artifacts.case_results[0]["failure_reasons"],
        )

    def test_fails_malformed_success_instead_of_crashing(self):
        case = first_case()
        malformed = copy.deepcopy(ready_response())
        malformed["result"] = None
        attempt = RecordedAttempt(
            case_id=case.case_id,
            response=malformed,
            latency_ms=500,
            model_calls=1,
            execution_error="runner observed a generation failure",
        )

        artifacts = evaluate(
            cases=[case],
            attempts=[attempt],
            run_metadata=RUN_METADATA,
            dataset_sha256="dataset-sha",
            attempts_sha256="attempts-sha",
        )

        self.assertFalse(artifacts.summary["overall_pass"])
        self.assertIn(
            "response_schema_invalid",
            artifacts.case_results[0]["failure_reasons"],
        )
        self.assertIn(
            "system_failure_disguised_as_success",
            artifacts.case_results[0]["failure_reasons"],
        )

    def test_sums_only_recorded_usage_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.jsonl"
            path.write_text(
                "\n".join(
                    json.dumps(item)
                    for item in (
                        {
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "total_tokens": 150,
                            "estimated_cost_usd": 0.001,
                        },
                        {
                            "request_kind": "scope",
                            "input_tokens": 200,
                            "output_tokens": 75,
                            "total_tokens": 275,
                            "estimated_cost_usd": 0.002,
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            totals = _usage_totals(path)

        self.assertEqual(totals["record_count"], 2)
        self.assertEqual(totals["draft_record_count"], 1)
        self.assertEqual(totals["scope_record_count"], 1)
        self.assertEqual(totals["input_tokens"], 300)
        self.assertEqual(totals["total_tokens"], 425)
        self.assertEqual(totals["estimated_cost_usd"], 0.003)


if __name__ == "__main__":
    unittest.main()
