# latency_eval.py
"""
Description: 같은 특약 요청과 같은 근거를 후보 모델마다 여러 번 호출해 응답 시간(중앙값, 느린 쪽, 평균)과 토큰 처리량을 잰다.
서비스 생성기를 그대로 쓰되 모델 호출만 시간을 재며, 실호출은 확인 플래그와 호출 수·비용 한도가 필요하다.
Author: ooheunsu
Date: 2026-11-07
Before:
    - 모델 비교가 품질·비용만 다루고 응답 속도는 재지 않은 상태.
After:
    - local_data/rag_eval/latency/<실행ID>/에 모델별 호출 시간과 요약(summary.md, results.json)이 저장됨.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from api.services import gemini_special_clause_generator as clause_module
from ml.evaluation.rag_eval import judge as judge_module
from ml.evaluation.rag_eval.generation_eval import (
    PRICING_USD_PER_MILLION,
    GenerationEvalError,
    _require_keys,
    load_evidence,
    make_transport,
)
from ml.evaluation.rag_eval.providers import provider_for
from ml.evaluation.prompt_harness.runner import load_cases
from ml.agent.contract_review_graph import ContractReviewRetrievalQuery

DEFAULT_OUTPUT_ROOT = Path("local_data/rag_eval/latency")
DEFAULT_CASE_ID = "c13_guarantee_refusal_cancel"
DEFAULT_REPEATS = 10
# 호출 1회 평균 토큰 가정(live-202 실측 기반). 사전 비용 추정에만 쓴다.
ESTIMATED_TOKENS = (2100, 450)


def percentile(values: Sequence[float], q: float) -> float:
    """가장 가까운 순위 방식의 백분위수. 표본이 적을 때 보간 없이 실제 관측값을 돌려준다."""

    if not values:
        raise ValueError("값이 없습니다.")
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize_latency(durations: Sequence[float], output_tokens: Sequence[int]) -> dict[str, float]:
    total_out = sum(output_tokens)
    total_time = sum(durations)
    return {
        "calls": len(durations),
        "p50_s": round(percentile(durations, 50), 2),
        "p95_s": round(percentile(durations, 95), 2),
        "mean_s": round(total_time / len(durations), 2),
        "min_s": round(min(durations), 2),
        "max_s": round(max(durations), 2),
        "mean_output_tokens": round(total_out / len(durations), 1),
        "tokens_per_second": round(total_out / total_time, 1) if total_time else 0.0,
    }


def estimate_cost(models: Sequence[str], repeats: int) -> dict[str, Any]:
    total = 0.0
    for model in models:
        price = PRICING_USD_PER_MILLION.get(model)
        if price is None:
            return {"calls": len(models) * repeats, "estimated_cost_usd": None}
        total += repeats * (ESTIMATED_TOKENS[0] * price[0] + ESTIMATED_TOKENS[1] * price[1]) / 1_000_000
    return {"calls": len(models) * repeats, "estimated_cost_usd": round(total, 4)}


def measure_model(
    *,
    model: str,
    query: ContractReviewRetrievalQuery,
    evidence: list[Any],
    repeats: int,
    post_json: Callable[[dict[str, Any]], dict[str, Any]] | None,
    api_key: str = "offline-key",
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """한 모델을 repeats번 호출해 호출 시간과 출력 토큰을 모은다. post_json이 없으면 Gemini에 직접 보낸다."""

    generator = clause_module.GeminiSpecialClauseGenerator(
        api_key=api_key, model=model, usage_manifest_path=None
    )
    thinking = judge_module.judge_thinking_config(model)
    generator._thinking_config = lambda t=thinking: dict(t)
    transport = post_json or generator._post_json
    durations: list[float] = []
    output_tokens: list[int] = []
    errors: list[str] = []
    captured: dict[str, int] = {}

    def timed(payload: dict[str, Any]) -> dict[str, Any]:
        started = clock()
        try:
            response = transport(payload)
        finally:
            durations.append(clock() - started)
        captured["out"] = int(
            (response.get("usageMetadata") or {}).get("candidatesTokenCount") or 0
        )
        return response

    generator._post_json = timed
    for _ in range(repeats):
        captured.clear()
        try:
            generator.generate([query], evidence)
        except Exception as exc:  # 한 번의 실패가 전체 측정을 멈추지 않게 기록만 한다.
            errors.append(f"{type(exc).__name__}: {str(exc)[:80]}")
        output_tokens.append(captured.get("out", 0))
    ok = [d for d, t in zip(durations, output_tokens) if t > 0] or durations
    ok_tokens = [t for t in output_tokens if t > 0] or [0] * len(ok)
    summary = summarize_latency(ok, ok_tokens) if ok else {}
    summary["errors"] = len(errors)
    return {"model": model, "summary": summary, "durations_s": [round(d, 3) for d in durations], "errors": errors}


def render_summary(run_id: str, results: Sequence[Mapping[str, Any]], case_id: str) -> str:
    lines = [
        f"# 응답 속도 측정 {run_id}",
        "",
        f"- 입력: 특약 요청 1건(`{case_id}`) + 같은 검색 근거, 모델당 같은 호출을 반복(순차 실행)",
        "- 측정값은 네트워크 상태에 따라 달라지므로 같은 환경에서 비교한 상대값으로 본다.",
        "",
        "| 모델 | 호출 | 중앙값(s) | 느린 쪽 p95(s) | 평균(s) | 최소~최대(s) | 평균 출력 토큰 | 토큰/초 | 오류 |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|",
    ]
    for r in results:
        s = r["summary"]
        if not s or "p50_s" not in s:
            lines.append(f"| {r['model']} | 0 | - | - | - | - | - | - | {s.get('errors', 0) if s else '-'} |")
            continue
        lines.append(
            f"| {r['model']} | {s['calls']} | {s['p50_s']} | {s['p95_s']} | {s['mean_s']} | "
            f"{s['min_s']}~{s['max_s']} | {s['mean_output_tokens']} | {s['tokens_per_second']} | {s['errors']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--evidence-cache", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=None)
    parser.add_argument("--case-id", default=DEFAULT_CASE_ID)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--max-calls", type=int, default=40)
    parser.add_argument("--max-cost-usd", type=float, default=0.2)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

    estimate = estimate_cost(args.models, args.repeats)
    if not args.confirm_live:
        raise GenerationEvalError("실제 모델 호출은 --confirm-live 가 있어야 실행합니다(유료).")
    if estimate["calls"] > args.max_calls:
        raise GenerationEvalError(f"예상 호출 {estimate['calls']}회가 한도 {args.max_calls}회를 넘습니다.")
    if estimate["estimated_cost_usd"] is None:
        raise GenerationEvalError("단가를 모르는 모델이 있습니다.")
    if estimate["estimated_cost_usd"] > args.max_cost_usd:
        raise GenerationEvalError(
            f"예상 비용 ${estimate['estimated_cost_usd']}가 한도 ${args.max_cost_usd}를 넘습니다."
        )

    from precedents.precedent_config import load_env_file

    load_env_file()
    keys = {
        "gemini": os.environ.get("GEMINI_API_KEY", "").strip(),
        "openai": os.environ.get("OPENAI_API_KEY", "").strip(),
        "anthropic": os.environ.get("ANTHROPIC_API_KEY", "").strip(),
    }
    _require_keys(args.models, keys["gemini"], keys["openai"], keys["anthropic"])

    cases = {case.id: case for case in load_cases(args.cases)}
    case = cases[args.case_id]
    _, evidence_by_case = load_evidence(args.evidence_cache)
    item = case.items[0]
    evidence = evidence_by_case[case.id][item.query_item.item_id]
    query = item.query_item

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir or DEFAULT_OUTPUT_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for model in args.models:
        post_json = None
        if provider_for(model) != "gemini":
            post_json = make_transport(model, keys["gemini"], keys["openai"], keys["anthropic"])._post_json
        result = measure_model(
            model=model, query=query, evidence=evidence, repeats=args.repeats,
            post_json=post_json, api_key=keys["gemini"] or "offline-key",
        )
        results.append(result)
        (output_dir / "results.json").write_text(
            json.dumps({"case_id": args.case_id, "estimate": estimate, "results": results}, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )  # 모델마다 저장해 중간에 끊겨도 앞선 결과가 남는다.
    (output_dir / "summary.md").write_text(render_summary(run_id, results, args.case_id), encoding="utf-8")
    print(render_summary(run_id, results, args.case_id))
    print(f"완료: {output_dir}")


if __name__ == "__main__":
    main()
