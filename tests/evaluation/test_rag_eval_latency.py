# test_rag_eval_latency.py
"""
Description: 응답 속도 측정 도구의 통계와 호출 루프를 가짜 모델과 가짜 시계로 검증한다. 네트워크를 쓰지 않는다.
Author: ooheunsu
Date: 2026-11-07
Before:
    - ml.evaluation.rag_eval.latency_eval이 존재.
After:
    - 백분위수, 오류 처리, 비용 추정이 오프라인에서 검증됨.
"""

import json
import unittest

from ml.agent.contract_review_graph import ContractReviewEvidence, ContractReviewRetrievalQuery
from ml.evaluation.rag_eval import latency_eval as le

QUERY = ContractReviewRetrievalQuery(item_id="request-1", category="special_clause_request", query="특약 요청 테스트")
EVIDENCE = [
    ContractReviewEvidence(
        item_id="request-1", source_type="statute", source_id="law:1", title="t", text="원문",
        rank=1, similarity=0.9, metadata={},
    )
]


def _response(tokens=100):
    item = {"item_id": "request-1", "status": "draft_ready", "draft_text": "당사자는 합의한 범위에서 이행한다.",
            "title": "특약 초안", "reason": "필요한 이유입니다.", "strategy": "합의할 범위를 확인하세요.",
            "needs_input": [], "evidence_source_ids": ["law:1"]}
    return {"candidates": [{"content": {"parts": [{"text": json.dumps({"items": [item]}, ensure_ascii=False)}]}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": tokens}}


class LatencyTest(unittest.TestCase):
    def test_percentile_uses_observed_values(self):
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        self.assertEqual(le.percentile(values, 50), 5)
        self.assertEqual(le.percentile(values, 95), 10)
        with self.assertRaises(ValueError):
            le.percentile([], 50)

    def test_summarize(self):
        s = le.summarize_latency([2.0, 4.0], [100, 300])
        self.assertEqual(s["p50_s"], 2.0)
        self.assertEqual(s["tokens_per_second"], round(400 / 6.0, 1))

    def test_measure_model_times_each_call(self):
        ticks = iter(range(0, 100))
        result = le.measure_model(
            model="gemini-3.5-flash-lite", query=QUERY, evidence=EVIDENCE, repeats=3,
            post_json=lambda payload: _response(), clock=lambda: float(next(ticks)),
        )
        self.assertEqual(result["summary"]["calls"], 3)
        self.assertEqual(result["summary"]["errors"], 0)
        self.assertEqual(result["durations_s"], [1.0, 1.0, 1.0])

    def test_failed_calls_are_recorded_not_fatal(self):
        calls = {"n": 0}

        def flaky(payload):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("연결 끊김")
            return _response()

        result = le.measure_model(
            model="gemini-3.5-flash-lite", query=QUERY, evidence=EVIDENCE, repeats=3, post_json=flaky,
        )
        self.assertEqual(result["summary"]["errors"], 1)
        self.assertEqual(result["summary"]["calls"], 2)  # 성공한 호출만 통계에 쓴다

    def test_estimate_cost(self):
        self.assertGreater(le.estimate_cost(["gemini-3.5-flash-lite", "gpt-6.1-terra"], 10)["estimated_cost_usd"], 0)
        self.assertIsNone(le.estimate_cost(["unknown-model"], 10)["estimated_cost_usd"])

    def test_render_summary_has_columns(self):
        text = le.render_summary("t", [{"model": "m", "summary": le.summarize_latency([1.0], [10]) | {"errors": 0}}], "c")
        self.assertIn("중앙값", text)
        self.assertIn("| m |", text)


if __name__ == "__main__":
    unittest.main()
