# test_rag_eval_generation.py
"""
Description: 생성 평가 도구를 가짜 검색기·가짜 모델·가짜 판정기로 검증한다. 네트워크와 모델 로딩 없이 실행된다.
Author: ooheunsu
Date: 2026-11-06
Before:
    - ml.evaluation.rag_eval.generation_eval과 judge가 존재.
After:
    - 검색 교체, 오프라인 실행, 판정 점수, 실호출 가드, 결과 저장이 검증됨.
"""

import json
import tempfile
import unittest
from pathlib import Path

from ml.evaluation.prompt_harness.runner import load_cases, load_pool
from ml.evaluation.rag_eval import generation_eval as ge
from ml.evaluation.rag_eval import judge
from ml.rag.contract_review_retriever import ContractReviewRagRetriever
from ml.rag.statute_retriever import StatuteSearchResult


class FakeStatuteRetriever:
    """질의와 무관하게 평가 풀의 앞 조문을 돌려주는 가짜 검색기다."""

    def __init__(self):
        self.pool = list(load_pool().values())

    def search(self, query, *, top_k=10):
        return [
            StatuteSearchResult(
                rank=i + 1, chunk_id=e["source_id"], distance=0.1 * i, similarity=0.9 - 0.1 * i,
                text=e["text"], law_id=None, law_name=e["law_name"], article_label=e["article_label"],
                article_title=e["article_title"], effective_date=e["effective_date"],
                heading_path=None, source_node_id=None, metadata={},
            )
            for i, e in enumerate(self.pool[:top_k])
        ]


class JudgeTest(unittest.TestCase):
    def test_score_judgement(self):
        scores = judge.score_judgement([
            {"item_id": "a", "claims": [{"text": "x", "supported": True}, {"text": "y", "supported": False}], "relevancy": 3},
            {"item_id": "b", "claims": [], "relevancy": 9},
        ])
        self.assertEqual(scores["a"]["faithfulness"], 0.5)
        self.assertEqual(scores["a"]["relevancy"], 0.75)
        self.assertEqual(scores["a"]["unsupported"], ["y"])
        self.assertIsNone(scores["b"]["faithfulness"])
        self.assertIsNone(scores["b"]["relevancy"])

    def test_fake_judge_flags_unknown_number(self):
        payload = judge.build_judge_payload([
            {"item_id": "a", "query": "q", "evidence": [{"source_id": "s", "text": "임대료는 5퍼센트"}],
             "output": "증액은 5퍼센트까지. 계약금은 900만원이다."}
        ])
        scores = judge.score_judgement(judge.fake_judge(payload)["items"])
        self.assertEqual(scores["a"]["faithfulness"], 0.5)


class GenerationEvalTest(unittest.TestCase):
    def setUp(self):
        self.cases = load_cases()[:3] + load_cases()[-2:]
        retriever = ContractReviewRagRetriever(statute_retriever=FakeStatuteRetriever())
        self.evidence = ge.retrieve_evidence(self.cases, retriever)

    def test_retrieval_replaces_hand_picked_evidence(self):
        replaced = ge.with_retrieved_evidence(self.cases, self.evidence)
        for case in replaced:
            for item in case.items:
                self.assertTrue(item.evidence)
                self.assertEqual(len(item.evidence), 3)
        quality = ge.retrieval_quality(self.cases, self.evidence)
        self.assertEqual(quality["items"], sum(len(c.items) for c in self.cases))
        self.assertTrue(0.0 <= quality["context_recall"] <= 1.0)

    def test_fake_run_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            result = ge.run_generation_eval(
                cases=self.cases, evidence=self.evidence, retrieval_preset="dense_k3",
                model="gemini-3.5-flash-lite", output_dir=out, judge_model="gemini-3.8-flash",
            )
            for name in ("evidence.json", "results.jsonl", "config.json", "summary.md", "review_sheet.md"):
                self.assertTrue((out / name).exists(), name)
            self.assertEqual(result["summary"]["returned_items"], result["summary"]["expected_items"])
            self.assertEqual(result["summary"]["failed_items"], 0)
            self.assertIsNotNone(result["summary"]["faithfulness"])
            self.assertEqual(result["config"]["usage"].get("gemini-3.8-flash", {}).get("calls"), len(self.cases))
            preset, loaded = ge.load_evidence(out / "evidence.json")
            self.assertEqual(preset, "dense_k3")
            self.assertEqual(loaded.keys(), self.evidence.keys())

    def test_empty_retrieval_counts_as_not_returned(self):
        empty = {c.id: {i.query_item.item_id: [] for i in c.items} for c in self.cases}
        with tempfile.TemporaryDirectory() as tmp:
            result = ge.run_generation_eval(
                cases=self.cases, evidence=empty, retrieval_preset="dense_k3",
                model="gemini-3.5-flash-lite", output_dir=Path(tmp) / "r",
            )
        self.assertEqual(result["summary"]["returned_items"], 0)
        self.assertEqual(result["retrieval"]["empty_evidence_items"], result["retrieval"]["items"])

    def test_live_guards(self):
        base = dict(cases=self.cases, evidence=self.evidence, retrieval_preset="dense_k3", mode="live")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "r"
            with self.assertRaises(ge.GenerationEvalError):  # --confirm-live 없음
                ge.run_generation_eval(model="gemini-3.5-flash-lite", output_dir=out, api_key="k", **base)
            with self.assertRaises(ge.GenerationEvalError):  # 단가 모르는 모델
                ge.run_generation_eval(model="unknown-model", output_dir=out, api_key="k", confirm_live=True, **base)
            with self.assertRaises(ge.GenerationEvalError):  # 호출 한도 초과
                ge.run_generation_eval(model="gemini-3.5-flash-lite", output_dir=out, api_key="k",
                                       confirm_live=True, max_calls=1, **base)
            with self.assertRaises(ge.GenerationEvalError):  # 비용 한도 초과
                ge.run_generation_eval(model="gemini-3.5-flash-lite", output_dir=out, api_key="k",
                                       confirm_live=True, max_cost_usd=0.0001, **base)
            with self.assertRaises(ge.GenerationEvalError):  # 키 없음
                ge.run_generation_eval(model="gemini-3.5-flash-lite", output_dir=out, confirm_live=True, **base)

    def test_rejudge_from_saved_run(self):
        full_cases = load_cases()
        full_evidence = ge.retrieve_evidence(
            full_cases, ContractReviewRagRetriever(statute_retriever=FakeStatuteRetriever())
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            ge.run_generation_eval(
                cases=full_cases, evidence=full_evidence, retrieval_preset="dense_k3",
                model="gemini-3.5-flash-lite", output_dir=source,
            )
            out = Path(tmp) / "rejudged"
            result = ge.rejudge_run(source_dir=source, output_dir=out, judge_model="gemini-3.8-flash")
            self.assertIsNotNone(result["summary"]["faithfulness"])
            self.assertEqual(result["config"]["rejudged_from"], "source")
            self.assertEqual(result["config"]["judge_prompt_version"], judge.JUDGE_PROMPT_VERSION)
            with self.assertRaises(ge.GenerationEvalError):  # --confirm-live 없는 실호출 거부
                ge.rejudge_run(
                    source_dir=source, output_dir=out, judge_model="gemini-3.8-flash", mode="live"
                )

    def test_resume_reuses_error_free_cases(self):
        full_cases = load_cases()
        full_evidence = ge.retrieve_evidence(
            full_cases, ContractReviewRagRetriever(statute_retriever=FakeStatuteRetriever())
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            ge.run_generation_eval(
                cases=full_cases, evidence=full_evidence, retrieval_preset="dense_k3",
                model="gemini-3.5-flash-lite", output_dir=source, judge_model="gemini-3.8-flash",
            )
            # 한 케이스를 오류가 난 것처럼 표시한다.
            rows = [json.loads(line) for line in (source / "results.jsonl").read_text(encoding="utf-8").splitlines()]
            failed_case = rows[0]["case_id"]
            for row in rows:
                if row["case_id"] == failed_case:
                    row["error"] = "RuntimeError: HTTP 401"
            (source / "results.jsonl").write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
            )
            carried, _ = ge.load_resume(source)
            self.assertNotIn(failed_case, carried)
            self.assertEqual(len(carried), len(full_cases) - 1)
            out = Path(tmp) / "resumed"
            result = ge.run_generation_eval(
                cases=full_cases, evidence=full_evidence, retrieval_preset="dense_k3",
                model="gemini-3.5-flash-lite", output_dir=out, judge_model="gemini-3.8-flash",
                resume_from=source,
            )
            self.assertEqual(result["config"]["resumed_from"], "source")
            self.assertEqual(result["config"]["estimate"]["calls"], 2)  # 다시 돌린 1케이스: 생성+판정
            self.assertEqual(result["summary"]["returned_items"], result["summary"]["expected_items"])

    def test_judge_output_fields(self):
        fields = ge.judge_output_fields(
            {"item_id": "a", "status": "x", "summary": "요약", "needs_input": ["a", "b"],
             "reason": "", "evidence_source_ids": ["s"]}
        )
        self.assertEqual(fields, {"summary": "요약", "needs_input": "a; b"})

    def test_estimate_cost(self):
        estimate = ge.estimate_cost("gemini-3.5-flash-lite", "gemini-3.8-flash", 25)
        self.assertEqual(estimate["calls"], 50)
        self.assertGreater(estimate["estimated_cost_usd"], 0)
        self.assertIsNone(ge.estimate_cost("x", None, 25)["estimated_cost_usd"])


if __name__ == "__main__":
    unittest.main()
