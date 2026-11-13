# compare_precedent_rerankers_v2.py
"""
Description: 고정된 판례 골든셋 질문과 Dense 후보를 세 로컬 리랭커로 재정렬하고
품질, 로드 시간, warm 지연시간과 최대 메모리를 동일 조건에서 비교한다.
Author: choeminju
Date: 2026-11-12
Before:
    - 20문항 골든셋 부분집합, 골드 라벨, BGE-M3 판례 DB와 로컬 리랭커가 준비된 상태.
After:
    - local_data에 고정 후보와 모델별 상세 결과가, 지정한 요약 경로에 비교 결과가 생성된다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import resource
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml.evaluation.precedents.compare_precedent_rerankers import (  # noqa: E402
    deduplicate_case_results,
)
from ml.rag.precedent_retriever import (  # noqa: E402
    BGE_M3_MODEL_NAME,
    DEFAULT_CHUNKING_STRATEGY,
    LOCAL_DATA_ROOT,
    BgeM3PrecedentRetriever,
)


DEFAULT_QUERIES_CSV = (
    Path(__file__).resolve().parent
    / "datasets"
    / "reranker_goldset_subset_v01.csv"
)
DEFAULT_LABELS_CSV = (
    LOCAL_DATA_ROOT
    / "precedents"
    / "evaluation"
    / "goldset_v01"
    / "precedent_retrieval_gold_labels_v01.csv"
)
DEFAULT_OUTPUT_ROOT = (
    LOCAL_DATA_ROOT / "precedents" / "evaluation" / "reranker_runs_v2"
)
DEFAULT_MODEL_ROOT = LOCAL_DATA_ROOT / "models" / "rerankers"
MODEL_SPECS = {
    "kure-reranker-nano": {
        "repo_id": "nlpai-lab/KURE-Reranker-nano",
        "revision": "8514b9e113f3b27c441ed9c2b50f81d80388b2fd",
        "path": DEFAULT_MODEL_ROOT / "KURE-Reranker-nano",
        "trust_remote_code": False,
    },
    "bge-reranker-v2-m3": {
        "repo_id": "BAAI/bge-reranker-v2-m3",
        "revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
        "path": DEFAULT_MODEL_ROOT / "bge-reranker-v2-m3",
        "trust_remote_code": False,
    },
    "qwen3-reranker-0.6b": {
        "repo_id": "Qwen/Qwen3-Reranker-0.6B",
        "revision": "e61197ed45024b0ed8a2d74b80b4d909f1255473",
        "path": DEFAULT_MODEL_ROOT / "Qwen3-Reranker-0.6B",
        "trust_remote_code": False,
    },
}
RELEVANT_THRESHOLD = 2
METRIC_NAMES = (
    "ndcg@5",
    "precision@3",
    "mrr@5",
    "mean_top3_score",
    "recall@5",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized_case_key(case_number: str | None, precedent_id: str) -> str:
    normalized = "".join((case_number or "").split()).casefold()
    return normalized or f"precedent:{precedent_id}"


def load_queries(path: Path) -> list[dict[str, str]]:
    rows = read_csv(path)
    required = {"query_id", "dispute_type", "query_specificity", "query"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"질문 CSV 필수 컬럼이 없습니다: {sorted(required)}")
    query_ids = [row["query_id"].strip() for row in rows]
    if any(not query_id for query_id in query_ids) or len(query_ids) != len(set(query_ids)):
        raise ValueError("질문 ID가 비어 있거나 중복되었습니다.")
    return [
        {
            "query_id": row["query_id"].strip(),
            "dispute_type": row["dispute_type"].strip(),
            "query_specificity": row["query_specificity"].strip(),
            "query": row["query"].strip(),
        }
        for row in rows
    ]


def load_gold_labels(path: Path) -> dict[str, dict[str, int]]:
    labels: dict[str, dict[str, int]] = defaultdict(dict)
    for row in read_csv(path):
        query_id = row.get("query_id", "").strip()
        precedent_id = row.get("precedent_id", "").strip()
        if not query_id or not precedent_id:
            continue
        key = normalized_case_key(row.get("case_no"), precedent_id)
        try:
            relevance = int(row.get("relevance", "0").strip() or "0")
        except ValueError:
            relevance = 0
        labels[query_id][key] = max(labels[query_id].get(key, 0), relevance)
    return dict(labels)


def dcg(relevances: list[int]) -> float:
    return sum(
        ((2**relevance) - 1) / math.log2(index + 2)
        for index, relevance in enumerate(relevances)
    )


def compute_metrics(
    ranked_cases: list[dict[str, Any]],
    gold_relevance: dict[str, int],
) -> dict[str, float | int]:
    relevances = [gold_relevance.get(row["case_key"], 0) for row in ranked_cases]
    relevant_gold = [score for score in gold_relevance.values() if score >= RELEVANT_THRESHOLD]
    ideal = sorted(relevant_gold, reverse=True)[:5]
    ideal_dcg = dcg(ideal)
    first_relevant = next(
        (
            rank
            for rank, score in enumerate(relevances[:5], start=1)
            if score >= RELEVANT_THRESHOLD
        ),
        None,
    )
    found_top5 = sum(score >= RELEVANT_THRESHOLD for score in relevances[:5])
    top3 = relevances[:3]
    return {
        "ndcg@5": round(dcg(relevances[:5]) / ideal_dcg, 6) if ideal_dcg else 0.0,
        "precision@3": round(
            sum(score >= RELEVANT_THRESHOLD for score in top3) / max(len(top3), 1),
            6,
        ),
        "mrr@5": round(1 / first_relevant, 6) if first_relevant else 0.0,
        "mean_top3_score": round(sum(top3) / max(len(top3), 1), 6),
        "recall@5": round(found_top5 / max(len(relevant_gold), 1), 6),
        "gold_case_count": len(relevant_gold),
        "non_gold_top5_count": sum(score == 0 for score in relevances[:5]),
    }


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(math.ceil(quantile * len(ordered)) - 1, len(ordered) - 1)
    return round(ordered[max(index, 0)], 4)


def peak_rss_mb() -> float:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    bytes_used = raw if sys.platform == "darwin" else raw * 1024
    return round(bytes_used / (1024 * 1024), 2)


def summarize_query_metrics(
    method: str,
    rows: list[dict[str, Any]],
    timings_ms: list[float] | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "method": method,
        "query_count": len(rows),
        **{
            metric: mean([float(row[metric]) for row in rows])
            for metric in METRIC_NAMES
        },
        "non_gold_top5_count": sum(int(row["non_gold_top5_count"]) for row in rows),
    }
    if timings_ms is not None:
        summary.update(
            {
                "warm_rerank_total_seconds": round(sum(timings_ms) / 1000, 4),
                "mean_milliseconds_per_query": mean(timings_ms),
                "p50_milliseconds_per_query": percentile(timings_ms, 0.5),
                "p95_milliseconds_per_query": percentile(timings_ms, 0.95),
            }
        )
    return summary


def serialize_candidate(result: Any, chunks_per_case: int) -> dict[str, Any]:
    chunks = sorted(result.matched_chunks, key=lambda chunk: chunk.distance)[:chunks_per_case]
    return {
        "case_key": normalized_case_key(result.case_no, result.precedent_id),
        "precedent_id": result.precedent_id,
        "case_number": result.case_no or "",
        "case_name": result.case_name or "",
        "court_name": result.court_name or "",
        "decision_date": result.decision_date or "",
        "dense_score": result.similarity,
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "chunk_type": chunk.chunk_type or "",
                "distance": chunk.distance,
                "text": chunk.text,
            }
            for chunk in chunks
        ],
    }


def prepare_candidates(args: argparse.Namespace) -> None:
    queries = load_queries(args.queries_csv)
    labels = load_gold_labels(args.labels_csv)
    started = time.perf_counter()
    retriever = BgeM3PrecedentRetriever(device=args.device)
    raw_results = retriever.search_many(
        [row["query"] for row in queries],
        top_k=args.candidate_cases * 2,
    )
    retrieval_seconds = time.perf_counter() - started
    candidate_queries = []
    dense_metric_rows = []
    for query, results in zip(queries, raw_results, strict=True):
        deduplicated = deduplicate_case_results(results, args.candidate_cases)
        if len(deduplicated) < args.candidate_cases:
            raise ValueError(
                f"{query['query_id']} 후보가 부족합니다: "
                f"{len(deduplicated)} < {args.candidate_cases}"
            )
        candidates = [
            serialize_candidate(result, args.chunks_per_case)
            for result in deduplicated
        ]
        metrics = compute_metrics(candidates, labels.get(query["query_id"], {}))
        dense_metric_rows.append({**query, **metrics})
        candidate_queries.append({**query, "candidates": candidates})

    args.output_dir.mkdir(parents=True, exist_ok=True)
    pool_path = args.output_dir / "candidate_pool.json"
    write_json(
        pool_path,
        {
            "schema_version": "precedent_reranker_candidate_pool.v2",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "queries_csv_sha256": file_sha256(args.queries_csv),
            "labels_csv_sha256": file_sha256(args.labels_csv),
            "query_count": len(queries),
            "candidate_cases": args.candidate_cases,
            "chunks_per_case": args.chunks_per_case,
            "dense_embedding_model": BGE_M3_MODEL_NAME,
            "chunking_strategy": DEFAULT_CHUNKING_STRATEGY,
            "retrieval_seconds_including_load": round(retrieval_seconds, 4),
            "queries": candidate_queries,
        },
    )
    dense_summary = summarize_query_metrics("dense", dense_metric_rows)
    dense_summary["retrieval_seconds_including_load"] = round(retrieval_seconds, 4)
    write_json(args.output_dir / "dense_summary.json", dense_summary)
    write_csv(
        args.output_dir / "dense_query_metrics.csv",
        dense_metric_rows,
        [
            "query_id",
            "dispute_type",
            "query_specificity",
            "query",
            *METRIC_NAMES,
            "gold_case_count",
            "non_gold_top5_count",
        ],
    )
    print(f"고정 후보 저장: {pool_path}")


def scalar_score(value: Any) -> float:
    if hasattr(value, "item"):
        try:
            return float(value.item())
        except ValueError:
            pass
    if isinstance(value, (list, tuple)) and len(value) == 1:
        return float(value[0])
    return float(value)


def evaluate_model(args: argparse.Namespace) -> None:
    spec = MODEL_SPECS[args.model]
    model_path = Path(spec["path"])
    if not model_path.is_dir():
        raise FileNotFoundError(f"로컬 모델이 없습니다: {model_path}")
    candidate_pool = json.loads(args.candidate_pool.read_text(encoding="utf-8"))
    labels = load_gold_labels(args.labels_csv)

    from sentence_transformers import CrossEncoder

    load_started = time.perf_counter()
    model = CrossEncoder(
        str(model_path),
        max_length=args.max_length,
        device=args.device,
        trust_remote_code=spec["trust_remote_code"],
        local_files_only=True,
    )
    load_seconds = time.perf_counter() - load_started

    first_query = candidate_pool["queries"][0]
    first_chunk = first_query["candidates"][0]["chunks"][0]["text"]
    warmup_started = time.perf_counter()
    model.predict([(first_query["query"], first_chunk)], show_progress_bar=False)
    warmup_seconds = time.perf_counter() - warmup_started

    ranking_rows = []
    metric_rows = []
    timings_ms = []
    for query_row in candidate_pool["queries"]:
        pairs = []
        pair_keys = []
        for candidate_index, candidate in enumerate(query_row["candidates"]):
            for chunk_index, chunk in enumerate(candidate["chunks"]):
                pairs.append((query_row["query"], chunk["text"]))
                pair_keys.append((candidate_index, chunk_index))
        started = time.perf_counter()
        raw_scores = model.predict(
            pairs,
            batch_size=args.batch_size,
            show_progress_bar=False,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        timings_ms.append(elapsed_ms)
        scores = {
            key: scalar_score(score)
            for key, score in zip(pair_keys, raw_scores, strict=True)
        }
        ranked = []
        for candidate_index, candidate in enumerate(query_row["candidates"]):
            best_chunk_index = max(
                range(len(candidate["chunks"])),
                key=lambda index: scores[(candidate_index, index)],
            )
            ranked.append(
                {
                    **candidate,
                    "reranker_score": scores[(candidate_index, best_chunk_index)],
                    "best_chunk": candidate["chunks"][best_chunk_index],
                }
            )
        ranked.sort(key=lambda row: row["reranker_score"], reverse=True)
        metrics = compute_metrics(ranked, labels.get(query_row["query_id"], {}))
        metric_rows.append(
            {
                "query_id": query_row["query_id"],
                "dispute_type": query_row["dispute_type"],
                "query_specificity": query_row["query_specificity"],
                "milliseconds": round(elapsed_ms, 4),
                **metrics,
            }
        )
        for rank, candidate in enumerate(ranked, start=1):
            ranking_rows.append(
                {
                    "query_id": query_row["query_id"],
                    "method": args.model,
                    "rank": rank,
                    "precedent_id": candidate["precedent_id"],
                    "case_number": candidate["case_number"],
                    "case_name": candidate["case_name"],
                    "reranker_score": round(candidate["reranker_score"], 8),
                    "dense_score": round(candidate["dense_score"], 8),
                    "best_chunk_id": candidate["best_chunk"]["chunk_id"],
                    "best_chunk_type": candidate["best_chunk"]["chunk_type"],
                }
            )

    output_dir = args.output_dir / args.model
    summary = summarize_query_metrics(args.model, metric_rows, timings_ms)
    summary.update(
        {
            "schema_version": "precedent_reranker_result.v2",
            "repo_id": spec["repo_id"],
            "revision": spec["revision"],
            "model_path": str(model_path.resolve()),
            "model_safetensors_sha256": file_sha256(model_path / "model.safetensors"),
            "candidate_pool_sha256": file_sha256(args.candidate_pool),
            "labels_csv_sha256": file_sha256(args.labels_csv),
            "max_length": args.max_length,
            "batch_size": args.batch_size,
            "device": args.device,
            "load_seconds": round(load_seconds, 4),
            "warmup_seconds": round(warmup_seconds, 4),
            "peak_rss_mb": peak_rss_mb(),
            "llm_api_calls": 0,
        }
    )
    write_json(output_dir / "summary.json", summary)
    write_csv(
        output_dir / "query_metrics.csv",
        metric_rows,
        [
            "query_id",
            "dispute_type",
            "query_specificity",
            "milliseconds",
            *METRIC_NAMES,
            "gold_case_count",
            "non_gold_top5_count",
        ],
    )
    write_csv(
        output_dir / "rankings.csv",
        ranking_rows,
        list(ranking_rows[0]),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def aggregate_results(args: argparse.Namespace) -> None:
    summaries = [
        json.loads((args.run_dir / "dense_summary.json").read_text(encoding="utf-8"))
    ]
    for model_key in MODEL_SPECS:
        path = args.run_dir / model_key / "summary.json"
        if path.is_file():
            summaries.append(json.loads(path.read_text(encoding="utf-8")))
    if len(summaries) < 2:
        raise ValueError("집계할 리랭커 결과가 없습니다.")

    fields = [
        "method",
        "query_count",
        *METRIC_NAMES,
        "non_gold_top5_count",
        "load_seconds",
        "warmup_seconds",
        "warm_rerank_total_seconds",
        "mean_milliseconds_per_query",
        "p50_milliseconds_per_query",
        "p95_milliseconds_per_query",
        "peak_rss_mb",
    ]
    rows = [{field: summary.get(field, "") for field in fields} for summary in summaries]
    output_dir = args.summary_dir or args.run_dir
    write_csv(output_dir / "method_summary.csv", rows, fields)
    public_manifest = {
        "schema_version": "precedent_reranker_comparison.v2",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": "reranker_goldset_subset_v01",
        "candidate_pool_sha256": file_sha256(args.run_dir / "candidate_pool.json"),
        "candidate_cases": 20,
        "chunks_per_case": 3,
        "reranker_max_length": 512,
        "llm_api_calls": 0,
        "summaries": [
            {key: value for key, value in summary.items() if key != "model_path"}
            for summary in summaries
        ],
    }
    write_json(output_dir / "manifest.json", public_manifest)
    print(f"비교 요약 저장: {output_dir}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="판례 리랭커 v2 비교 평가")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="동일 Dense 후보 풀을 생성한다.")
    prepare.add_argument("--queries-csv", type=Path, default=DEFAULT_QUERIES_CSV)
    prepare.add_argument("--labels-csv", type=Path, default=DEFAULT_LABELS_CSV)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--candidate-cases", type=int, default=20)
    prepare.add_argument("--chunks-per-case", type=int, default=3)
    prepare.add_argument("--device", choices=("auto", "cpu", "cuda"), default="cpu")
    prepare.set_defaults(handler=prepare_candidates)

    evaluate = subparsers.add_parser("evaluate", help="리랭커 하나를 평가한다.")
    evaluate.add_argument("--model", choices=tuple(MODEL_SPECS), required=True)
    evaluate.add_argument("--candidate-pool", type=Path, required=True)
    evaluate.add_argument("--labels-csv", type=Path, default=DEFAULT_LABELS_CSV)
    evaluate.add_argument("--output-dir", type=Path, required=True)
    evaluate.add_argument("--max-length", type=int, default=512)
    evaluate.add_argument("--batch-size", type=int, default=8)
    evaluate.add_argument("--device", default="cpu")
    evaluate.set_defaults(handler=evaluate_model)

    aggregate = subparsers.add_parser("aggregate", help="모델별 결과를 집계한다.")
    aggregate.add_argument("--run-dir", type=Path, required=True)
    aggregate.add_argument("--summary-dir", type=Path, default=None)
    aggregate.set_defaults(handler=aggregate_results)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
