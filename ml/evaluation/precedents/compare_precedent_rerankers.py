# compare_precedent_rerankers.py
"""
Description: 동일한 BGE-M3 판례 후보를 BGE와 GTE 전용 리랭커로 재정렬하고
블라인드 사람 평가용 풀과 모델별 순위 및 지연시간을 저장한다.
Author: choeminju
Date: 2026-10-18
Before:
    - 질문 CSV, BGE-M3 판례 벡터DB와 로컬 리랭커 모델이 준비된 상태.
After:
    - local_data 아래에 Dense 및 리랭커 순위, 리뷰 풀과 비교 지표가 생성된다.
"""

from __future__ import annotations

import argparse
import csv
import gc
import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml.evaluation.precedents.evaluate_query_guidance_variants import (  # noqa: E402
    DEFAULT_QUERIES_CSV,
    load_queries,
)
from ml.rag.precedent_retriever import (  # noqa: E402
    BGE_M3_MODEL_NAME,
    DEFAULT_CHUNKING_STRATEGY,
    BgeM3PrecedentRetriever,
    PrecedentSearchResult,
)


DEFAULT_MODEL_ROOT = PROJECT_ROOT / "local_data" / "models" / "rerankers"
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT
    / "local_data"
    / "precedents"
    / "evaluation"
    / "reranker_runs"
)
DEFAULT_CANDIDATE_CASES = 20
DEFAULT_OUTPUT_TOP_K = 10
DEFAULT_REVIEW_TOP_K = 5
DEFAULT_MAX_LENGTH = 512
DEFAULT_BATCH_SIZE = 16
DENSE_METHOD = "dense"
RERANKER_SPECS = {
    "bge-reranker-v2-m3": {
        "path": DEFAULT_MODEL_ROOT / "bge-reranker-v2-m3",
        "trust_remote_code": False,
    },
    "gte-multilingual-reranker-base": {
        "path": DEFAULT_MODEL_ROOT / "gte-multilingual-reranker-base",
        "trust_remote_code": True,
    },
}

RANKING_FIELDS = [
    "query_id",
    "scenario_id",
    "variant",
    "query",
    "method",
    "rank",
    "candidate_key",
    "precedent_id",
    "case_number",
    "case_name",
    "court_name",
    "decision_date",
    "ranking_score",
    "best_chunk_id",
    "best_chunk_type",
    "best_chunk_text",
]
REVIEW_FIELDS = [
    "query_id",
    "scenario_id",
    "variant",
    "query",
    "candidate_key",
    "precedent_id",
    "case_number",
    "case_name",
    "court_name",
    "decision_date",
    "evidence_texts",
    "human_score",
    "review_note",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare encoder rerankers on fixed BGE-M3 precedent candidates.",
    )
    parser.add_argument("--queries-csv", type=Path, default=DEFAULT_QUERIES_CSV)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--candidate-cases", type=int, default=DEFAULT_CANDIDATE_CASES)
    parser.add_argument("--output-top-k", type=int, default=DEFAULT_OUTPUT_TOP_K)
    parser.add_argument("--review-top-k", type=int, default=DEFAULT_REVIEW_TOP_K)
    parser.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument(
        "--variants",
        default="A,B,C",
        help="쉼표로 구분한 질문 문체 버전이다.",
    )
    parser.add_argument(
        "--scores-csv",
        type=Path,
        default=None,
        help="human_score가 채워진 review_pool.csv를 집계한다.",
    )
    parser.add_argument(
        "--rankings-csv",
        type=Path,
        default=None,
        help="--scores-csv와 함께 집계할 rankings.csv 경로다.",
    )
    return parser.parse_args()


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def make_output_dir(path: Path | None) -> Path:
    if path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = DEFAULT_OUTPUT_ROOT / f"precedent_rerankers_v01_{timestamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_run_args(args: argparse.Namespace) -> None:
    positive_values = {
        "--candidate-cases": args.candidate_cases,
        "--output-top-k": args.output_top_k,
        "--review-top-k": args.review_top_k,
        "--max-length": args.max_length,
        "--batch-size": args.batch_size,
    }
    invalid = [name for name, value in positive_values.items() if value < 1]
    if invalid:
        raise ValueError(f"1 이상이어야 하는 옵션입니다: {', '.join(invalid)}")
    if args.output_top_k > args.candidate_cases:
        raise ValueError("--output-top-k는 --candidate-cases보다 클 수 없습니다.")
    if args.review_top_k > args.output_top_k:
        raise ValueError("--review-top-k는 --output-top-k보다 클 수 없습니다.")


def candidate_key(query_id: str, precedent_id: str) -> str:
    return f"{query_id}:{precedent_id}"


def canonical_case_key(result: PrecedentSearchResult) -> str:
    case_number = "".join((result.case_no or "").split()).casefold()
    return case_number or f"precedent:{result.precedent_id}"


def deduplicate_case_results(
    results: list[PrecedentSearchResult],
    limit: int,
) -> list[PrecedentSearchResult]:
    unique_results = []
    seen_cases = set()
    for result in results:
        key = canonical_case_key(result)
        if key in seen_cases:
            continue
        seen_cases.add(key)
        unique_results.append(result)
        if len(unique_results) >= limit:
            break
    return unique_results


def result_metadata(result: PrecedentSearchResult) -> dict[str, str]:
    return {
        "precedent_id": result.precedent_id,
        "case_number": result.case_no or "",
        "case_name": result.case_name or "",
        "court_name": result.court_name or "",
        "decision_date": result.decision_date or "",
    }


def dense_ranking_rows(
    query: dict[str, str],
    results: list[PrecedentSearchResult],
    top_k: int,
) -> list[dict[str, Any]]:
    rows = []
    for rank, result in enumerate(results[:top_k], start=1):
        best_chunk = min(result.matched_chunks, key=lambda chunk: chunk.distance)
        rows.append(
            {
                **query,
                "method": DENSE_METHOD,
                "rank": rank,
                "candidate_key": candidate_key(query["query_id"], result.precedent_id),
                **result_metadata(result),
                "ranking_score": round(result.similarity, 8),
                "best_chunk_id": best_chunk.chunk_id,
                "best_chunk_type": best_chunk.chunk_type or "",
                "best_chunk_text": compact_text(best_chunk.text),
            }
        )
    return rows


def compact_text(value: str) -> str:
    return " ".join(value.split())


def load_cross_encoder(
    model_path: Path,
    *,
    max_length: int,
    device: str,
    trust_remote_code: bool,
) -> Any:
    from sentence_transformers import CrossEncoder

    if not model_path.is_dir():
        raise FileNotFoundError(f"리랭커 모델이 없습니다: {model_path}")
    selected_device = None if device == "auto" else device
    return CrossEncoder(
        str(model_path),
        max_length=max_length,
        device=selected_device,
        trust_remote_code=trust_remote_code,
        local_files_only=True,
    )


def rerank(
    method: str,
    spec: dict[str, Any],
    queries: list[dict[str, str]],
    dense_results: list[list[PrecedentSearchResult]],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    load_started = time.perf_counter()
    model = load_cross_encoder(
        spec["path"],
        max_length=args.max_length,
        device=args.device,
        trust_remote_code=spec["trust_remote_code"],
    )
    load_seconds = time.perf_counter() - load_started

    pairs: list[tuple[str, str]] = []
    pair_keys: list[tuple[int, int, int]] = []
    for query_index, (query, results) in enumerate(
        zip(queries, dense_results, strict=True)
    ):
        for result_index, result in enumerate(results):
            for chunk_index, chunk in enumerate(result.matched_chunks):
                pairs.append((query["query"], chunk.text))
                pair_keys.append((query_index, result_index, chunk_index))

    rerank_started = time.perf_counter()
    scores = model.predict(
        pairs,
        batch_size=args.batch_size,
        show_progress_bar=True,
    )
    rerank_seconds = time.perf_counter() - rerank_started
    score_by_key = {
        key: float(score)
        for key, score in zip(pair_keys, scores, strict=True)
    }

    rows: list[dict[str, Any]] = []
    for query_index, (query, results) in enumerate(
        zip(queries, dense_results, strict=True)
    ):
        scored_cases = []
        for result_index, result in enumerate(results):
            chunk_scores = [
                (score_by_key[(query_index, result_index, chunk_index)], chunk)
                for chunk_index, chunk in enumerate(result.matched_chunks)
            ]
            best_score, best_chunk = max(chunk_scores, key=lambda item: item[0])
            scored_cases.append((best_score, result, best_chunk))
        scored_cases.sort(key=lambda item: item[0], reverse=True)
        for rank, (score, result, best_chunk) in enumerate(
            scored_cases[: args.output_top_k],
            start=1,
        ):
            rows.append(
                {
                    **query,
                    "method": method,
                    "rank": rank,
                    "candidate_key": candidate_key(
                        query["query_id"], result.precedent_id
                    ),
                    **result_metadata(result),
                    "ranking_score": round(score, 8),
                    "best_chunk_id": best_chunk.chunk_id,
                    "best_chunk_type": best_chunk.chunk_type or "",
                    "best_chunk_text": compact_text(best_chunk.text),
                }
            )

    del model
    gc.collect()
    return rows, {
        "model_path": str(spec["path"].resolve()),
        "trust_remote_code": spec["trust_remote_code"],
        "pair_count": len(pairs),
        "load_seconds": round(load_seconds, 4),
        "rerank_seconds": round(rerank_seconds, 4),
        "milliseconds_per_pair": round(rerank_seconds * 1000 / len(pairs), 4),
    }


def build_review_pool(
    rankings: list[dict[str, Any]],
    review_top_k: int,
) -> list[dict[str, Any]]:
    selected = [row for row in rankings if int(row["rank"]) <= review_top_k]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        grouped[row["candidate_key"]].append(row)

    review_rows = []
    for key, rows in sorted(grouped.items()):
        first = rows[0]
        evidence = []
        seen_chunks = set()
        for row in rows:
            chunk_key = (row["best_chunk_id"], row["best_chunk_text"])
            if chunk_key in seen_chunks:
                continue
            seen_chunks.add(chunk_key)
            evidence.append(
                f"[{row['best_chunk_type'] or '청크'}] {row['best_chunk_text']}"
            )
        review_rows.append(
            {
                "query_id": first["query_id"],
                "scenario_id": first["scenario_id"],
                "variant": first["variant"],
                "query": first["query"],
                "candidate_key": key,
                "precedent_id": first["precedent_id"],
                "case_number": first["case_number"],
                "case_name": first["case_name"],
                "court_name": first["court_name"],
                "decision_date": first["decision_date"],
                "evidence_texts": " || ".join(evidence),
                "human_score": "",
                "review_note": "",
            }
        )
    return review_rows


def run_comparison(args: argparse.Namespace) -> None:
    validate_run_args(args)
    selected_variants = {
        variant.strip() for variant in args.variants.split(",") if variant.strip()
    }
    if not selected_variants or not selected_variants.issubset({"A", "B", "C"}):
        raise ValueError("--variants는 A, B, C 중 하나 이상이어야 합니다.")
    queries = [
        row
        for row in load_queries(args.queries_csv)
        if row["variant"] in selected_variants
    ]
    output_dir = make_output_dir(args.output_dir)

    retrieval_started = time.perf_counter()
    retriever = BgeM3PrecedentRetriever(device=args.device)
    raw_dense_results = retriever.search_many(
        [query["query"] for query in queries],
        top_k=args.candidate_cases * 2,
    )
    dense_results = [
        deduplicate_case_results(results, args.candidate_cases)
        for results in raw_dense_results
    ]
    retrieval_seconds = time.perf_counter() - retrieval_started

    rankings: list[dict[str, Any]] = []
    for query, results in zip(queries, dense_results, strict=True):
        rankings.extend(dense_ranking_rows(query, results, args.output_top_k))

    timings = {}
    for method, spec in RERANKER_SPECS.items():
        print(f"리랭커 실행: {method}", flush=True)
        method_rows, timing = rerank(method, spec, queries, dense_results, args)
        rankings.extend(method_rows)
        timings[method] = timing

    rankings.sort(key=lambda row: (row["query_id"], row["method"], row["rank"]))
    review_rows = build_review_pool(rankings, args.review_top_k)
    rankings_path = output_dir / "rankings.csv"
    review_path = output_dir / "review_pool.csv"
    write_csv(rankings_path, rankings, RANKING_FIELDS)
    write_csv(review_path, review_rows, REVIEW_FIELDS)

    manifest = {
        "schema_version": "precedent_reranker_comparison.v1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "queries_csv": str(args.queries_csv.resolve()),
        "query_count": len(queries),
        "scenario_count": len({row["scenario_id"] for row in queries}),
        "variants": sorted(selected_variants),
        "candidate_cases": args.candidate_cases,
        "retrieval_pool_cases_before_case_number_dedup": args.candidate_cases * 2,
        "output_top_k": args.output_top_k,
        "review_top_k": args.review_top_k,
        "max_length": args.max_length,
        "batch_size": args.batch_size,
        "device": args.device,
        "dense_embedding_model": BGE_M3_MODEL_NAME,
        "chunking_strategy": DEFAULT_CHUNKING_STRATEGY,
        "retrieval_seconds_including_model_load": round(retrieval_seconds, 4),
        "reranker_timings": timings,
        "llm_calls": 0,
        "rankings_csv": str(rankings_path.resolve()),
        "review_pool_csv": str(review_path.resolve()),
        "review_candidate_count": len(review_rows),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"비교 완료: 질문 {len(queries)}개, 리뷰 후보 {len(review_rows)}개")
    print(f"순위 파일: {rankings_path}")
    print(f"리뷰 파일: {review_path}")


def parse_human_scores(path: Path) -> tuple[dict[str, int], dict[str, list[int]]]:
    scores = {}
    scores_by_query: dict[str, list[int]] = defaultdict(list)
    for row in read_csv(path):
        raw_score = row.get("human_score", "").strip()
        try:
            score = int(raw_score)
        except ValueError as error:
            raise ValueError(f"평가 점수가 없습니다: {row.get('candidate_key')}") from error
        if score not in {1, 2, 3}:
            raise ValueError(f"평가 점수는 1, 2, 3 중 하나여야 합니다: {score}")
        scores[row["candidate_key"]] = score
        scores_by_query[row["query_id"]].append(score)
    return scores, scores_by_query


def dcg(scores: list[int]) -> float:
    return sum(
        (2 ** (score - 1) - 1) / math.log2(index + 2)
        for index, score in enumerate(scores)
    )


def ranking_metrics(
    scores: list[int],
    ideal_candidates: list[int] | None = None,
    k: int = 5,
) -> dict[str, float]:
    top_scores = scores[:k]
    ideal_scores = sorted(ideal_candidates or scores, reverse=True)[:k]
    ideal_dcg = dcg(ideal_scores)
    first_high = next(
        (index for index, score in enumerate(top_scores, start=1) if score == 3),
        None,
    )
    return {
        f"ndcg@{k}": 0.0 if ideal_dcg == 0 else dcg(top_scores) / ideal_dcg,
        "precision@3": sum(score >= 2 for score in scores[:3]) / min(3, len(scores)),
        f"mrr@{k}": 0.0 if first_high is None else 1 / first_high,
        "mean_top3_score": sum(scores[:3]) / min(3, len(scores)),
    }


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarize_scores(scores_csv: Path, rankings_csv: Path) -> None:
    human_scores, scores_by_query = parse_human_scores(scores_csv)
    ranking_rows = read_csv(rankings_csv)
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in ranking_rows:
        grouped[(row["method"], row["query_id"])].append(row)

    query_metrics = []
    for (method, query_id), rows in sorted(grouped.items()):
        rows.sort(key=lambda row: int(row["rank"]))
        missing = [row["candidate_key"] for row in rows[:5] if row["candidate_key"] not in human_scores]
        if missing:
            raise ValueError(f"상위 5개 평가 점수가 없습니다: {missing}")
        scores = [human_scores[row["candidate_key"]] for row in rows[:5]]
        metrics = ranking_metrics(scores, scores_by_query[query_id])
        query_metrics.append(
            {
                "method": method,
                "query_id": query_id,
                "scenario_id": rows[0]["scenario_id"],
                "variant": rows[0]["variant"],
                **{key: round(value, 6) for key, value in metrics.items()},
            }
        )

    scenario_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in query_metrics:
        scenario_groups[(row["method"], row["scenario_id"])].append(row)
    scenario_metrics = []
    metric_names = ["ndcg@5", "precision@3", "mrr@5", "mean_top3_score"]
    for (method, scenario_id), rows in sorted(scenario_groups.items()):
        scenario_metrics.append(
            {
                "method": method,
                "scenario_id": scenario_id,
                **{
                    metric: round(mean([row[metric] for row in rows]), 6)
                    for metric in metric_names
                },
            }
        )

    method_metrics: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scenario_metrics:
        method_metrics[row["method"]].append(row)
    summary_rows = []
    for method, rows in sorted(method_metrics.items()):
        summary_rows.append(
            {
                "method": method,
                "scenario_count": len(rows),
                **{
                    metric: round(mean([row[metric] for row in rows]), 6)
                    for metric in metric_names
                },
            }
        )

    output_dir = scores_csv.parent
    write_csv(
        output_dir / "query_metrics.csv",
        query_metrics,
        ["method", "query_id", "scenario_id", "variant", *metric_names],
    )
    write_csv(
        output_dir / "scenario_metrics.csv",
        scenario_metrics,
        ["method", "scenario_id", *metric_names],
    )
    write_csv(
        output_dir / "method_summary.csv",
        summary_rows,
        ["method", "scenario_count", *metric_names],
    )
    print(json.dumps(summary_rows, ensure_ascii=False, indent=2))


def main() -> None:
    args = parse_args()
    if args.scores_csv is not None:
        rankings_path = args.rankings_csv or args.scores_csv.parent / "rankings.csv"
        summarize_scores(args.scores_csv, rankings_path)
        return
    if args.rankings_csv is not None:
        raise ValueError("--rankings-csv는 --scores-csv와 함께 사용해야 합니다.")
    run_comparison(args)


if __name__ == "__main__":
    main()
