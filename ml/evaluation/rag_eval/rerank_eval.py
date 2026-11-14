# rerank_eval.py
"""
Description: dense 검색 후보를 크로스 인코더 리랭커로 재정렬했을 때 법령 검색 정답 50문항의
Recall@3 등이 오르는지 비교하고, 특약 요청·검토 조항 질의는 재정렬 전후 상위 3개를 나란히 저장한다.
LLM을 호출하지 않으며 검색기와 서비스 코드는 읽기만 한다.
Author: ooheunsu
Date: 2026-11-12
Before:
    - 법령 ChromaDB, BGE-M3 모델 캐시, 정답 JSONL(관련도 0~3), 리랭커 모델이 로컬에 준비된 상태.
After:
    - local_data/rag_eval/rerank/<실행ID>/에 config.json, results.json, summary.md,
      qualitative.md가 생성됨.
"""

from __future__ import annotations

import argparse
import gc
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from ml.evaluation.rag_eval.retrieval_eval import build_labels, load_gold
from ml.evaluation.rag_eval.retrieval_metrics import (
    DEFAULT_KS,
    evaluate_rankings,
    paired_bootstrap_difference,
)

DEFAULT_OUTPUT_ROOT = Path("local_data/rag_eval/rerank")
DEFAULT_CASES = Path("ml/evaluation/prompt_harness/cases_v02.json")
CANDIDATE_DEPTHS = (10, 20, 30, 50)
MAX_PASSAGE_CHARS = 1500  # 리랭커 입력 길이 한도(약 512토큰)에 맞춘 안전 절단


def passage_text(result: Any) -> str:
    """리랭커에 넣을 조문 텍스트를 법령명·조문 표기와 함께 만든다."""

    head = " ".join(part for part in (result.law_name, result.article_label, result.article_title) if part)
    return f"{head}\n{result.text}"[:MAX_PASSAGE_CHARS]


def clause_queries(cases_path: Path) -> dict[str, str]:
    """특약 요청과 검토 조항 질의(주입 시험 제외)를 이름 붙여 모은다."""

    data = json.loads(cases_path.read_text(encoding="utf-8"))
    queries: dict[str, str] = {}
    for kind in ("clause_cases", "review_cases"):
        for case in data[kind]:
            for index, item in enumerate(case["items"], start=1):
                query = item["query"]
                if "INJECTED" in query:
                    continue
                queries[f"{case['id']}#{index}"] = query.split(": ", 1)[-1]
    return queries


def rerank_order(model: Any, query: str, candidates: Sequence[Any], batch_size: int) -> list[str]:
    """후보를 리랭커 점수 내림차순으로 정렬한 chunk_id 목록을 돌려준다."""

    scores = model.predict(
        [(query, passage_text(c)) for c in candidates], batch_size=batch_size, show_progress_bar=False
    )
    ranked = sorted(zip(candidates, scores), key=lambda pair: float(pair[1]), reverse=True)
    return [c.chunk_id for c, _ in ranked]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True, help="정답 JSONL 경로")
    parser.add_argument("--reranker-path", type=Path, required=True, help="로컬에 내려받은 리랭커 모델 폴더")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--depth", type=int, default=max(CANDIDATE_DEPTHS))
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()

    import psutil
    import torch
    from sentence_transformers import CrossEncoder

    from ml.rag.statute_retriever import BgeM3StatuteRetriever

    process = psutil.Process()
    cases = load_gold(args.dataset)
    labels = build_labels(cases)
    gold_queries = {case["query_id"]: case["question"] for case in cases}
    style_queries = clause_queries(args.cases)

    # 1단계: 검색 후보를 모은 뒤 검색 모델을 내려 메모리를 확보한다.
    retriever = BgeM3StatuteRetriever(device=args.device)
    ids = sorted(gold_queries)
    gold_candidates = dict(
        zip(ids, retriever.search_many([gold_queries[i] for i in ids], top_k=args.depth), strict=True)
    )
    style_ids = sorted(style_queries)
    style_candidates = dict(
        zip(style_ids, retriever.search_many([style_queries[i] for i in style_ids], top_k=30), strict=True)
    )
    del retriever
    gc.collect()

    # 2단계: 리랭커로 재정렬한다.
    # 모델이 bfloat16으로 저장돼 있으면 CPU에서 매우 느려질 수 있어 float32로 올린다.
    model_kwargs = {"torch_dtype": torch.float32} if args.device == "cpu" else {}
    model = CrossEncoder(str(args.reranker_path), device=args.device, max_length=512, model_kwargs=model_kwargs)
    print(f"모델 로드 완료: dtype={next(model.model.parameters()).dtype}", flush=True)
    peak_rss = process.memory_info().rss / 1e9

    rankings: dict[str, dict[str, list[str]]] = {
        "A_dense": {q: [c.chunk_id for c in gold_candidates[q]] for q in ids}
    }
    started = time.perf_counter()
    reranked_full: dict[str, list[str]] = {}
    for done, q in enumerate(ids, start=1):
        reranked_full[q] = rerank_order(model, gold_queries[q], gold_candidates[q], args.batch_size)
        if done % 5 == 0 or done == len(ids):
            elapsed = time.perf_counter() - started
            print(f"재정렬 {done}/{len(ids)} 질문, 경과 {elapsed:.0f}초, 남은 시간 약 {elapsed / done * (len(ids) - done):.0f}초", flush=True)
    rerank_seconds = time.perf_counter() - started
    pairs = sum(len(v) for v in gold_candidates.values())
    peak_rss = max(peak_rss, process.memory_info().rss / 1e9)

    for depth in CANDIDATE_DEPTHS:
        rankings[f"B_rerank_top{depth}"] = {}
        for q in ids:
            head = [c.chunk_id for c in gold_candidates[q][:depth]]
            order = [c for c in reranked_full[q] if c in set(head)]  # 후보 수를 depth로 잘라 재정렬 순서를 유지
            rankings[f"B_rerank_top{depth}"][q] = order + [
                c.chunk_id for c in gold_candidates[q][depth:]
            ]
    results = {name: evaluate_rankings(r, labels, DEFAULT_KS) for name, r in rankings.items()}

    bootstrap: dict[str, dict[str, dict[str, float]]] = {}
    base = results["A_dense"]["per_query"]
    for name, result in results.items():
        if name == "A_dense":
            continue
        bootstrap[name] = {}
        for k in DEFAULT_KS:
            for metric in ("recall", "ndcg"):
                bootstrap[name][f"{metric}@{k}"] = paired_bootstrap_difference(
                    {q: row[k][metric] for q, row in result["per_query"].items()},
                    {q: row[k][metric] for q, row in base.items()},
                )

    # 정성 비교: 특약 요청·검토 조항의 재정렬 전후 상위 3개
    lines = ["# 재정렬 전후 상위 3개 (정답 라벨 없음, 눈으로 확인)", ""]
    for name in style_ids:
        cands = style_candidates[name]
        after = rerank_order(model, style_queries[name], cands, args.batch_size)
        by_id = {c.chunk_id: c for c in cands}

        def label(chunk_id: str) -> str:
            c = by_id[chunk_id]
            return f"{c.law_name or ''} {c.article_label or ''} {c.article_title or ''}".strip()

        lines.append(f"## {name}: {style_queries[name]}")
        lines.append("- 전(dense 상위3): " + " / ".join(label(c.chunk_id) for c in cands[:3]))
        lines.append("- 후(리랭커 상위3): " + " / ".join(label(c) for c in after[:3]))
        lines.append("")

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.output_dir or DEFAULT_OUTPUT_ROOT / run_id
    out.mkdir(parents=True, exist_ok=True)
    config = {
        "run_id": run_id,
        "dataset": str(args.dataset),
        "reranker_path": str(args.reranker_path),
        "device": args.device,
        "candidate_depths": list(CANDIDATE_DEPTHS),
        "batch_size": args.batch_size,
        "gold_queries": len(ids),
        "rerank_pairs": pairs,
        "rerank_seconds": round(rerank_seconds, 1),
        "seconds_per_pair": round(rerank_seconds / pairs, 4),
        "peak_rss_gb": round(peak_rss, 2),
        "llm_calls": 0,
    }
    (out / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "results.json").write_text(
        json.dumps({"macro": {n: r["macro"] for n, r in results.items()}, "bootstrap": bootstrap},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "qualitative.md").write_text("\n".join(lines), encoding="utf-8")

    summary = ["# 리랭커 시험 요약", "", f"- 모델: {args.reranker_path.name} / 장치: {args.device}",
               f"- 재정렬 {pairs}쌍, {rerank_seconds:.0f}초 (쌍당 {rerank_seconds / pairs:.3f}초), 최대 메모리 {peak_rss:.1f}GB", "",
               "| 조건 | Recall@3 | Recall@5 | Recall@10 | nDCG@10 | MRR@10 |", "|---|---:|---:|---:|---:|---:|"]
    for name, result in results.items():
        m = result["macro"]
        summary.append(
            f"| {name} | {m[3]['recall']:.1%} | {m[5]['recall']:.1%} | {m[10]['recall']:.1%} | "
            f"{m[10]['ndcg']:.3f} | {m[10]['mrr']:.3f} |"
        )
    summary += ["", "## A 대비 차이 (Recall@3, 95% 신뢰구간)", "", "| 조건 | 평균 차이 | 신뢰구간 |", "|---|---:|---|"]
    for name, rows in bootstrap.items():
        r = rows["recall@3"]
        summary.append(f"| {name} | {r['mean_diff']:+.3f} | [{r['ci_low']:+.3f}, {r['ci_high']:+.3f}] |")
    (out / "summary.md").write_text("\n".join(summary), encoding="utf-8")
    print(f"완료: {out}")
    print("\n".join(summary))


if __name__ == "__main__":
    main()
