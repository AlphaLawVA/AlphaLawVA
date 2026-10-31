# compare_bge_bm25_hybrid.py
"""
Description: 동일한 승인 법령 평가셋으로 BGE-M3 dense 검색과 dense·BM25
하이브리드 검색을 비교하고, 지표·실행 조건·질문별 순위를 기록한다.
Author: choeminju
Date: 2026-10-29
Before:
    - 승인 법령 평가셋, BGE-M3 ChromaDB, 로컬 BGE-M3 모델 캐시가 존재.
After:
    - 두 검색 방식의 Top-K 지표와 순위 비교 결과가 JSON으로 남음.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ml.data_collection.statutes.law_api_common import PROJECT_ROOT
from ml.evaluation.evaluate_statute_label_sensitivity import evaluate_model
from ml.rag.statute_retriever import BgeM3StatuteRetriever, read_manifest


DEFAULT_DATASET = (
    PROJECT_ROOT
    / "data/statutes/evaluation/datasets/statute_retrieval_v01_50_approved.jsonl"
)
DEFAULT_DB_DIR = PROJECT_ROOT / "data/statutes/vectorstores/bge_m3"
DEFAULT_MODEL_CACHE = PROJECT_ROOT / "data/statutes/models"
DEFAULT_OUTPUT = (
    PROJECT_ROOT / "data/statutes/evaluation/runs/bge-bm25-hybrid-v01/result.json"
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError("평가 문항이 없습니다.")
    return rows


def rankings_from_batches(
    cases: list[dict[str, Any]],
    batches: list[list[Any]],
) -> dict[str, list[dict[str, str | int | float]]]:
    if len(cases) != len(batches):
        raise ValueError("평가 문항과 검색 결과 수가 일치하지 않습니다.")
    return {
        case["query_id"]: [
            {
                "rank": result.rank,
                "chunk_id": result.chunk_id,
                "similarity": result.similarity,
            }
            for result in batch
        ]
        for case, batch in zip(cases, batches, strict=True)
    }


def evaluate(
    cases: list[dict[str, Any]],
    dense_batches: list[list[Any]],
    hybrid_batches: list[list[Any]],
) -> dict[str, Any]:
    labels = {
        case["query_id"]: {
            judgment["chunk_id"]: int(judgment["relevance"])
            for judgment in case["judgments"]
        }
        for case in cases
    }
    case_lookup = {case["query_id"]: case for case in cases}
    dense = evaluate_model(rankings_from_batches(cases, dense_batches), labels, case_lookup)
    hybrid = evaluate_model(
        rankings_from_batches(cases, hybrid_batches),
        labels,
        case_lookup,
    )
    metrics = (
        "recall_at_5",
        "recall_at_10",
        "mrr_at_10",
        "ndcg_at_10",
        "precision_at_10",
        "hit_at_10",
    )
    return {
        "dense": dense,
        "hybrid": hybrid,
        "hybrid_minus_dense": {
            metric: hybrid["macro"][metric] - dense["macro"][metric]
            for metric in metrics
        },
        "rankings": {
            "dense": rankings_from_batches(cases, dense_batches),
            "hybrid": rankings_from_batches(cases, hybrid_batches),
        },
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="BGE-M3 dense와 BM25 하이브리드 법령 검색을 비교합니다."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--model-cache", type=Path, default=DEFAULT_MODEL_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.top_k < 10:
        raise ValueError("골드 지표 비교를 위해 --top-k는 10 이상이어야 합니다.")
    dataset_path = args.dataset.resolve()
    db_dir = args.db_dir.resolve()
    model_cache = args.model_cache.resolve()
    cases = read_jsonl(dataset_path)
    manifest = read_manifest(db_dir / "build_manifest.json")
    source_hashes = {case["dataset_snapshot"]["source_sha256"] for case in cases}
    if len(source_hashes) != 1 or manifest.get("source_sha256") not in source_hashes:
        raise ValueError("평가셋과 법령 DB의 source_sha256이 일치하지 않습니다.")

    retriever = BgeM3StatuteRetriever(
        db_dir=db_dir,
        model_cache=model_cache,
        device=args.device,
    )
    questions = [case["question"] for case in cases]
    started = time.perf_counter()
    dense_batches = retriever.search_many(questions, top_k=args.top_k)
    dense_elapsed_seconds = time.perf_counter() - started
    started = time.perf_counter()
    hybrid_batches = retriever.search_many_hybrid(questions, top_k=args.top_k)
    hybrid_elapsed_seconds = time.perf_counter() - started

    result = {
        "schema_version": "statute_hybrid_comparison.v0.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "path": str(dataset_path),
            "sha256": sha256(dataset_path),
            "query_count": len(cases),
            "source_sha256": manifest["source_sha256"],
        },
        "retrieval": {
            "dense": "BGE-M3 cosine ChromaDB",
            "hybrid": "BGE-M3 dense + BM25, equal-weight RRF",
            "top_k": args.top_k,
            "device": args.device,
            "bm25_k1": 1.5,
            "bm25_b": 0.75,
            "rrf_k": 60,
            "dense_elapsed_seconds": dense_elapsed_seconds,
            "hybrid_elapsed_seconds": hybrid_elapsed_seconds,
        },
        "comparison": evaluate(cases, dense_batches, hybrid_batches),
    }
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for method in ("dense", "hybrid"):
        macro = result["comparison"][method]["macro"]
        missing = len(result["comparison"][method]["critical"]["no_hit_at_10"])
        print(
            f"{method}: Recall@10={macro['recall_at_10']:.4f}, "
            f"nDCG@10={macro['ndcg_at_10']:.4f}, MRR@10={macro['mrr_at_10']:.4f}, "
            f"critical_no_hit={missing}"
        )
    print(f"result: {output_path}")


if __name__ == "__main__":
    main()
