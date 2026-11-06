# generation_eval.py
"""
Description: 프롬프트 하네스 25케이스의 질의를 실제 법령 검색(서비스와 같은 ContractReviewRagRetriever)에 넣어 근거를 얻고,
후보 생성 모델의 출력을 규칙 채점과 판정 LLM(faithfulness, relevancy)으로 비교한다. 기본은 오프라인이다.
Author: ooheunsu
Date: 2026-11-06
Before:
    - prompt_harness 케이스, 법령 ChromaDB·BGE-M3(검색 단계), Gemini API 키(실호출 시)가 준비된 상태.
After:
    - local_data/rag_eval/generation/<실행ID>/에 evidence.json, results.jsonl, config.json, summary.md가 생성됨.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from api.services import gemini_contract_review_analyzer as review_module
from api.services import gemini_special_clause_generator as clause_module
from ml.agent.contract_review_graph import ContractReviewEvidence
from ml.evaluation.prompt_harness.checks import output_text
from ml.evaluation.prompt_harness.runner import (
    CaseItem,
    HarnessCase,
    _fake_hook,
    _run_case,
    load_cases,
)
from ml.evaluation.rag_eval import judge as judge_module
from ml.rag.contract_review_retriever import ContractReviewRagRetriever

DEFAULT_OUTPUT_ROOT = Path("local_data/rag_eval/generation")

# 1M 토큰당 (입력, 출력) USD. 공식 가격표 확인값(2026-11-06). 목록에 없는 모델은 비용을 계산하지 않는다.
PRICING_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.8-flash": (0.75, 3.75),  # 2026-12-31까지 도입가, 2027-01-01부터 (1.50, 7.50)
}
# 호출 1회 평균 토큰 가정(사전 비용 추정용). 실측 후 갱신한다.
ESTIMATED_TOKENS = {"generate": (2500, 600), "judge": (3500, 900)}

RETRIEVAL_PRESETS: dict[str, dict[str, Any]] = {
    "dense_k3": {"statute_top_k": 3, "evidence_top_k": 3, "min_similarity": 0.35},  # 서비스 기본값
    "dense_k5": {"statute_top_k": 5, "evidence_top_k": 5, "min_similarity": 0.35},
    "dense_k3_min0.5": {"statute_top_k": 3, "evidence_top_k": 3, "min_similarity": 0.5},
}


class GenerationEvalError(ValueError):
    """생성 평가 설정이 안전하지 않거나 올바르지 않을 때 발생한다."""


def estimate_cost(model: str, judge_model: str | None, case_count: int) -> dict[str, Any]:
    """호출 수와 예상 비용(가정 토큰 기준)을 계산한다. 모델 단가를 모르면 비용은 None이다."""

    def cost(name: str | None, kind: str) -> float | None:
        if name is None:
            return 0.0
        price = PRICING_USD_PER_MILLION.get(name)
        if price is None:
            return None
        tokens_in, tokens_out = ESTIMATED_TOKENS[kind]
        return (tokens_in * price[0] + tokens_out * price[1]) / 1_000_000 * case_count

    generate, judge = cost(model, "generate"), cost(judge_model, "judge")
    total = None if generate is None or judge is None else round(generate + judge, 4)
    return {
        "calls": case_count + (case_count if judge_model else 0),
        "estimated_cost_usd": total,
        "assumption": "호출당 토큰 가정: 생성 2500/600, 판정 3500/900 (입력/출력)",
    }


def retrieve_evidence(
    cases: Sequence[HarnessCase],
    retriever: ContractReviewRagRetriever,
) -> dict[str, dict[str, list[ContractReviewEvidence]]]:
    """케이스 질의마다 실제 검색을 실행한다. 모델을 호출하지 않는다."""

    result: dict[str, dict[str, list[ContractReviewEvidence]]] = {}
    for case in cases:
        evidence = retriever.retrieve([item.query_item for item in case.items])
        by_item: dict[str, list[ContractReviewEvidence]] = {
            item.query_item.item_id: [] for item in case.items
        }
        for entry in evidence:
            by_item[entry.item_id].append(entry)
        result[case.id] = by_item
    return result


def save_evidence(path: Path, preset: str, evidence: Mapping[str, Any]) -> None:
    payload = {
        "retrieval_preset": preset,
        "evidence": {
            case_id: {item: [dataclasses.asdict(e) for e in entries] for item, entries in items.items()}
            for case_id, items in evidence.items()
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load_evidence(path: Path) -> tuple[str, dict[str, dict[str, list[ContractReviewEvidence]]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    evidence = {
        case_id: {
            item: [ContractReviewEvidence(**entry) for entry in entries]
            for item, entries in items.items()
        }
        for case_id, items in payload["evidence"].items()
    }
    return payload["retrieval_preset"], evidence


def with_retrieved_evidence(
    cases: Sequence[HarnessCase],
    evidence: Mapping[str, Mapping[str, list[ContractReviewEvidence]]],
) -> list[HarnessCase]:
    """하네스 케이스의 미리 고른 근거를 검색 결과로 바꾼다. 질의와 채점 규칙은 그대로다."""

    replaced = []
    for case in cases:
        items = [
            CaseItem(query_item=item.query_item, evidence=list(evidence[case.id][item.query_item.item_id]))
            for item in case.items
        ]
        replaced.append(dataclasses.replace(case, items=items))
    return replaced


def retrieval_quality(
    original: Sequence[HarnessCase],
    evidence: Mapping[str, Mapping[str, list[ContractReviewEvidence]]],
) -> dict[str, Any]:
    """하네스에서 사람이 고른 근거를 약한 정답으로 보고 context recall·precision을 계산한다."""

    recalls, precisions, empty, total = [], [], 0, 0
    for case in original:
        for item in case.items:
            gold = {e.source_id for e in item.evidence}
            retrieved = [e.source_id for e in evidence[case.id][item.query_item.item_id]]
            total += 1
            empty += not retrieved
            recalls.append(len(gold & set(retrieved)) / len(gold) if gold else 0.0)
            precisions.append(len(gold & set(retrieved)) / len(retrieved) if retrieved else 0.0)
    return {
        "items": total,
        "context_recall": sum(recalls) / len(recalls),
        "context_precision": sum(precisions) / len(precisions),
        "empty_evidence_items": empty,
        "note": "정답은 하네스 담당자가 고른 근거(약한 정답)이며, 다른 적절한 조문을 틀린 것으로 셀 수 있다.",
    }


def judge_inputs(case: HarnessCase, records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """판정기에 넣을 항목(질의, 근거 원문, 출력 텍스트)을 만든다."""

    by_id = {item.query_item.item_id: item for item in case.items}
    inputs = []
    for record in records:
        item = by_id.get(record["item_id"])
        if item is None or not record["output"]:
            continue
        inputs.append(
            {
                "item_id": record["item_id"],
                "query": item.query_item.query,
                "evidence": [
                    {"source_id": e.source_id, "text": f"{e.title}\n{e.text}"} for e in item.evidence
                ],
                "output": output_text(record["kind"], record["output"]),
            }
        )
    return inputs


def summarize(
    records: Sequence[Mapping[str, Any]],
    judged: Mapping[str, Mapping[str, Mapping[str, Any]]],
    expected_items: int,
) -> dict[str, Any]:
    fails: Counter = Counter()
    warns: Counter = Counter()
    failed_items = warned_items = returned = 0
    faith, relevancy, unsupported = [], [], 0
    for record in records:
        if record["item_id"] == "(case)":
            fails["model_responded"] += 1
            failed_items += 1
            continue
        if record["output"] and all(c.name != "known_item_id" for c in record["checks"]):
            returned += 1
        failed = [c for c in record["checks"] if not c.passed and c.severity == "fail"]
        warned = [c for c in record["checks"] if not c.passed and c.severity == "warn"]
        failed_items += bool(failed)
        warned_items += bool(warned)
        fails.update(c.name for c in failed)
        warns.update(c.name for c in warned)
        score = judged.get(record["case_id"], {}).get(record["item_id"])
        if score:
            if score["faithfulness"] is not None:
                faith.append(score["faithfulness"])
            if score["relevancy"] is not None:
                relevancy.append(score["relevancy"])
            unsupported += len(score["unsupported"])
    mean = lambda values: None if not values else sum(values) / len(values)  # noqa: E731
    return {
        "expected_items": expected_items,
        "returned_items": returned,
        "failed_items": failed_items,
        "warned_items": warned_items,
        "check_failures": dict(fails),
        "check_warnings": dict(warns),
        "faithfulness": mean(faith),
        "answer_relevancy": mean(relevancy),
        "judged_items": len(faith),
        "unsupported_claims": unsupported,
    }


def usage_cost(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """모델별 토큰 합계와 비용(단가를 아는 모델만)."""

    totals: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = totals.setdefault(row["model"], {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        entry["calls"] += 1
        entry["input_tokens"] += row.get("input_tokens", 0)
        entry["output_tokens"] += row.get("output_tokens", 0)
    for model, entry in totals.items():
        price = PRICING_USD_PER_MILLION.get(model)
        entry["cost_usd"] = (
            None
            if price is None
            else round((entry["input_tokens"] * price[0] + entry["output_tokens"] * price[1]) / 1_000_000, 6)
        )
    return totals


def render_summary(config: Mapping[str, Any], summary: Mapping[str, Any], retrieval: Mapping[str, Any]) -> str:
    pct = lambda v: "-" if v is None else f"{v * 100:.1f}%"  # noqa: E731
    lines = [
        f"# 생성 평가 {config['run_id']}",
        "",
        f"- 모드: {config['mode']} / 생성 모델: {config['model']} / 판정 모델: {config['judge_model']}",
        f"- 검색 설정: {config['retrieval_preset']} / 케이스 {config['case_count']}개",
        f"- 프롬프트: 분석 {config['review_prompt_version']}, 특약 {config['clause_prompt_version']}, 판정 {config['judge_prompt_version']}",
        f"- 사용량: {json.dumps(config['usage'], ensure_ascii=False)}",
        "",
        "## 검색 품질 (사람이 고른 근거 대비)",
        f"- context recall {pct(retrieval['context_recall'])} / context precision {pct(retrieval['context_precision'])}"
        f" / 근거가 0건인 항목 {retrieval['empty_evidence_items']}/{retrieval['items']}",
        f"- {retrieval['note']}",
        "",
        "## 생성 품질",
        f"- 항목 반환 {summary['returned_items']}/{summary['expected_items']} / 실패 항목 {summary['failed_items']} / 경고 항목 {summary['warned_items']}",
        f"- faithfulness {pct(summary['faithfulness'])} / answer relevancy {pct(summary['answer_relevancy'])}"
        f" (판정 {summary['judged_items']}항목, 근거 없는 주장 {summary['unsupported_claims']}건)",
        "",
        "| 규칙 | 실패 | 경고 |",
        "|---|---:|---:|",
    ]
    for name in sorted(set(summary["check_failures"]) | set(summary["check_warnings"])):
        lines.append(f"| {name} | {summary['check_failures'].get(name, 0)} | {summary['check_warnings'].get(name, 0)} |")
    lines += [
        "",
        "## 한계",
        "- 판정 LLM 점수는 사람 표본 검토로 확인해야 한다(review_sheet 참고).",
        "- 25케이스라 작은 차이는 우연일 수 있다. 결과는 후보 비교이며 최종 결정이 아니다.",
        "- 서비스의 1회 재작성 노드는 거치지 않고 분석기·생성기를 한 번 호출한 결과를 채점한다.",
    ]
    return "\n".join(lines) + "\n"


def render_review_sheet(
    cases: Sequence[HarnessCase],
    records: Sequence[Mapping[str, Any]],
    judged: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> str:
    """사람 표본 검토용: 질의, 검색 근거, 출력, 판정이 근거 없다고 본 주장을 한 곳에 모은다."""

    by_case = {case.id: case for case in cases}
    lines = ["# 사람 검토표", "", "판정 LLM이 놓칠 수 있는 항목(쉬운 말, 원문 취지, 근거 일치)을 직접 확인합니다.", ""]
    for record in records:
        case = by_case.get(record["case_id"])
        item = None if case is None else next(
            (i for i in case.items if i.query_item.item_id == record["item_id"]), None
        )
        if item is None or not record["output"]:
            continue
        score = judged.get(record["case_id"], {}).get(record["item_id"]) or {}
        lines += [
            f"## {record['case_id']} / {record['item_id']}",
            f"- 질의: {item.query_item.query}",
            f"- 검색 근거: {', '.join(e.title for e in item.evidence)}",
            f"- 판정: faithfulness {score.get('faithfulness')} / relevancy {score.get('relevancy')}",
            "- 근거 없다고 판정된 주장: " + (" | ".join(score.get("unsupported", [])) or "없음"),
            "- 출력:",
            "```",
            output_text(record["kind"], record["output"]),
            "```",
            "- 사람 평가: 쉬운 말 [ ]  원문 취지 유지 [ ]  근거와 일치 [ ]  쓸 만큼 구체적 [ ]",
            "",
        ]
    return "\n".join(lines)


def run_generation_eval(
    *,
    cases: Sequence[HarnessCase],
    evidence: Mapping[str, Mapping[str, list[ContractReviewEvidence]]],
    retrieval_preset: str,
    model: str,
    output_dir: Path,
    mode: str = "fake",
    judge_model: str | None = None,
    api_key: str | None = None,
    confirm_live: bool = False,
    max_calls: int = 60,
    max_cost_usd: float = 0.5,
    judge_call: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if mode not in {"fake", "live"}:
        raise GenerationEvalError("mode는 fake|live여야 합니다.")
    estimate = estimate_cost(model, judge_model, len(cases))
    if mode == "live":
        if not confirm_live:
            raise GenerationEvalError("실제 모델 호출은 --confirm-live 가 있어야 실행합니다(유료).")
        if estimate["calls"] > max_calls:
            raise GenerationEvalError(f"예상 호출 {estimate['calls']}회가 한도 {max_calls}회를 넘습니다.")
        if estimate["estimated_cost_usd"] is None:
            raise GenerationEvalError("단가를 모르는 모델입니다. 공식 가격표를 확인해 PRICING에 추가하세요.")
        if estimate["estimated_cost_usd"] > max_cost_usd:
            raise GenerationEvalError(
                f"예상 비용 ${estimate['estimated_cost_usd']}가 한도 ${max_cost_usd}를 넘습니다."
            )
        if not api_key:
            raise GenerationEvalError("GEMINI_API_KEY 가 없습니다.")

    output_dir.mkdir(parents=True, exist_ok=True)
    save_evidence(output_dir / "evidence.json", retrieval_preset, evidence)
    replaced = with_retrieved_evidence(cases, evidence)
    usage_path = output_dir / "usage.jsonl"
    judge_usage_rows: list[dict[str, Any]] = []

    transport = None
    if judge_model and judge_call is None:
        if mode == "fake":
            judge_call = lambda payload: (judge_module.fake_judge(payload), {"input_tokens": 0, "output_tokens": 0})  # noqa: E731
        else:
            transport = review_module.GeminiContractReviewAnalyzer(
                api_key=api_key, model=judge_model, usage_manifest_path=None
            )
            judge_call = judge_module.gemini_judge_call(transport)

    records: list[dict[str, Any]] = []
    judged: dict[str, dict[str, dict[str, Any]]] = {}
    spent = 0.0
    for case in replaced:
        if case.kind == "review":
            instance: Any = review_module.GeminiContractReviewAnalyzer(
                api_key=api_key or "offline-key", model=model, usage_manifest_path=usage_path
            )
            method = "analyze"
        else:
            instance = clause_module.GeminiSpecialClauseGenerator(
                api_key=api_key or "offline-key", model=model, usage_manifest_path=usage_path
            )
            method = "generate"
        hook = _fake_hook(case) if mode == "fake" else None
        case_records = _run_case(case, instance, method, hook) if any(i.evidence for i in case.items) else []
        records.extend(case_records)
        if judge_call is not None and case_records:
            scores, usage = judge_module.judge_items(judge_inputs(case, case_records), judge_call)
            judged[case.id] = scores
            judge_usage_rows.append({"model": judge_model, **usage})
        if mode == "live":  # 비용 한도 점검: 승인 범위를 넘으면 즉시 중단한다.
            rows = _read_usage(usage_path) + judge_usage_rows
            spent = sum(entry["cost_usd"] or 0.0 for entry in usage_cost(rows).values())
            if spent > max_cost_usd:
                raise GenerationEvalError(f"실제 비용 ${spent:.4f}가 한도 ${max_cost_usd}를 넘어 중단했습니다.")

    # 근거가 0건이라 호출하지 못한 항목도 반환 실패로 센다(expected_items 기준).
    expected = sum(len(case.items) for case in cases)
    summary = summarize(records, judged, expected)
    retrieval = retrieval_quality(cases, evidence)
    gen_rows = _read_usage(usage_path)
    usage = usage_cost(gen_rows + judge_usage_rows)
    config = {
        "run_id": output_dir.name,
        "mode": mode,
        "model": model if mode == "live" else f"{model} (fake)",
        "judge_model": judge_model,
        "retrieval_preset": retrieval_preset,
        "retrieval_config": RETRIEVAL_PRESETS.get(retrieval_preset),
        "case_count": len(cases),
        "cases_version": "cases_v01",
        "review_prompt_version": review_module.PROMPT_VERSION,
        "review_prompt_sha256": hashlib.sha256(review_module.SYSTEM_PROMPT.encode()).hexdigest()[:16],
        "clause_prompt_version": clause_module.PROMPT_VERSION,
        "clause_prompt_sha256": hashlib.sha256(clause_module.SYSTEM_PROMPT.encode()).hexdigest()[:16],
        "judge_prompt_version": judge_module.JUDGE_PROMPT_VERSION,
        "estimate": estimate,
        "usage": usage,
    }
    with (output_dir / "results.jsonl").open("w", encoding="utf-8") as file:
        for record in records:
            file.write(
                json.dumps(
                    {
                        **record,
                        "checks": [c.__dict__ for c in record["checks"]],
                        "judge": judged.get(record["case_id"], {}).get(record["item_id"]),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    (output_dir / "review_sheet.md").write_text(
        render_review_sheet(replaced, records, judged), encoding="utf-8"
    )
    (output_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "summary.md").write_text(render_summary(config, summary, retrieval), encoding="utf-8")
    return {"config": config, "summary": summary, "retrieval": retrieval}


def _read_usage(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_live_retriever(preset: str, device: str, with_precedents: bool) -> ContractReviewRagRetriever:
    from ml.rag.contract_review_retriever import SharedBgeM3ModelLoader
    from ml.rag.precedent_retriever import BgeM3PrecedentRetriever
    from ml.rag.statute_retriever import BgeM3StatuteRetriever, load_bge_model

    loader = SharedBgeM3ModelLoader(load_bge_model)
    options = RETRIEVAL_PRESETS[preset]
    return ContractReviewRagRetriever(
        statute_retriever=BgeM3StatuteRetriever(device=device, model_loader=loader),
        precedent_retriever=BgeM3PrecedentRetriever(model_loader=loader) if with_precedents else None,
        statute_top_k=options["statute_top_k"],
        evidence_top_k=options["evidence_top_k"],
        min_similarity=options["min_similarity"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["retrieve", "all"], default="all",
                        help="retrieve: 검색만 실행해 evidence.json 저장(무료)")
    parser.add_argument("--mode", choices=["fake", "live"], default="fake")
    parser.add_argument("--retrieval", choices=sorted(RETRIEVAL_PRESETS), default="dense_k3")
    parser.add_argument("--evidence-cache", type=Path, default=None, help="저장된 검색 결과를 재사용")
    parser.add_argument("--with-precedents", action="store_true")
    parser.add_argument("--model", default=review_module.DEFAULT_GEMINI_MODEL)
    parser.add_argument("--judge-model", default=None, help="지정하지 않으면 판정 LLM 채점을 생략")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--max-calls", type=int, default=60)
    parser.add_argument("--max-cost-usd", type=float, default=0.5)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

    cases = load_cases()
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir or DEFAULT_OUTPUT_ROOT / run_id
    if args.evidence_cache:
        preset, evidence = load_evidence(args.evidence_cache)
    else:
        preset = args.retrieval
        evidence = retrieve_evidence(cases, build_live_retriever(preset, args.device, args.with_precedents))
    if args.stage == "retrieve":
        output_dir.mkdir(parents=True, exist_ok=True)
        save_evidence(output_dir / "evidence.json", preset, evidence)
        print(json.dumps(retrieval_quality(cases, evidence), ensure_ascii=False, indent=2))
        print(f"검색 결과 저장: {output_dir / 'evidence.json'}")
        return

    api_key = None
    if args.mode == "live":
        from precedents.precedent_config import load_env_file

        load_env_file()
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    result = run_generation_eval(
        cases=cases, evidence=evidence, retrieval_preset=preset, model=args.model,
        output_dir=output_dir, mode=args.mode, judge_model=args.judge_model, api_key=api_key,
        confirm_live=args.confirm_live, max_calls=args.max_calls, max_cost_usd=args.max_cost_usd,
    )
    print(f"완료: {output_dir}")
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
