# evaluate_parent_child_statute_retrieval.py
"""
Description: 항 단위 자식 검색 결과를 부모 조문별로 중복 제거하고 기존
50문항 조문 라벨로 BGE-M3와 KURE-v1 검색 성능을 평가한다.
Author: ooheunsu
Date: 2026-09-05
Before:
    - 두 모델의 부모-자식 ChromaDB와 승인된 50문항 평가셋이 존재.
After:
    - 부모 조문 Top-10 순위, 모델별 지표, 재현성 manifest가 생성.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ml.data_collection.statutes.law_api_common import PROJECT_ROOT
from ml.embedding.build_parent_child_statute_chroma import MODEL_SPECS
from ml.evaluation.evaluate_statute_label_sensitivity import (
    POSITIVE_THRESHOLD,
    evaluate_model,
    model_order,
    percent,
)
from ml.evaluation.run_statute_retrieval_pool import (
    RetrievalModel,
    load_cases,
    open_collection,
    sentence_embeddings,
)


DEFAULT_DATASET = (
    PROJECT_ROOT
    / "data/statutes/evaluation/datasets/statute_retrieval_v01_50_approved.jsonl"
)
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data/statutes/evaluation/runs"
DEFAULT_MODEL_CACHE = PROJECT_ROOT / "data/statutes/models"
DEFAULT_BASELINE_METRICS = (
    DEFAULT_OUTPUT_ROOT / "statute-50-v01-initial/metrics_approved.json"
)
DEFAULT_PARENT_TOP_K = 10
DEFAULT_CHILD_CANDIDATE_K = 100


def retrieval_config(model_key: str) -> RetrievalModel:
    spec = MODEL_SPECS[model_key]
    return RetrievalModel(
        key=model_key,
        display_name=spec.display_name,
        provider="sentence_transformers",
        model_name=spec.model_name,
        revision=spec.revision,
        dimension=spec.dimension,
        db_dir=spec.default_db_dir,
        collection_name=spec.collection_name,
    )


def validate_parent_child_collections(
    configs: list[RetrievalModel],
) -> dict[str, Any]:
    collections = {}
    source_hashes = set()
    for config in configs:
        collection = open_collection(config)
        metadata = collection.metadata or {}
        spec = MODEL_SPECS[config.key]
        expected = {
            "model_name": config.model_name,
            "model_revision": config.revision,
            "embedding_dimension": config.dimension,
            "chunking_strategy": spec.chunking_strategy,
        }
        for key, value in expected.items():
            if metadata.get(key) != value:
                raise ValueError(
                    f"{config.display_name} 컬렉션 {key} 불일치: "
                    f"{metadata.get(key)!r} != {value!r}"
                )
        source_hash = metadata.get("source_sha256")
        if not isinstance(source_hash, str) or not source_hash:
            raise ValueError(f"{config.display_name} 원본 해시가 없습니다.")
        source_hashes.add(source_hash)
        collections[config.key] = collection
    if len(source_hashes) != 1:
        raise ValueError("두 모델의 부모-자식 청크 원본이 서로 다릅니다.")
    return collections


def collapse_child_results_to_parents(
    result: dict[str, Any],
    query_ids: list[str],
    parent_top_k: int,
) -> dict[str, list[dict[str, Any]]]:
    rankings = {}
    for index, query_id in enumerate(query_ids):
        parents = []
        seen_parent_ids = set()
        values = zip(
            result["ids"][index],
            result["documents"][index],
            result["metadatas"][index],
            result["distances"][index],
            strict=True,
        )
        for child_rank, (child_id, document, metadata, distance) in enumerate(
            values, start=1
        ):
            metadata = metadata or {}
            parent_id = metadata.get("parent_article_id")
            if not isinstance(parent_id, str) or not parent_id:
                raise ValueError(f"자식 청크에 부모 조문 ID가 없습니다: {child_id}")
            if parent_id in seen_parent_ids:
                continue
            seen_parent_ids.add(parent_id)
            parents.append(
                {
                    "rank": len(parents) + 1,
                    "chunk_id": parent_id,
                    "child_chunk_id": child_id,
                    "child_rank": child_rank,
                    "distance": float(distance),
                    "document": document,
                    "metadata": metadata,
                }
            )
            if len(parents) == parent_top_k:
                break
        if len(parents) < parent_top_k:
            raise ValueError(
                f"부모 Top-{parent_top_k}를 만들 자식 후보가 부족합니다: "
                f"{query_id}={len(parents)}"
            )
        rankings[query_id] = parents
    return rankings


def query_parent_rankings(
    collection: Any,
    embeddings: list[list[float]],
    query_ids: list[str],
    child_candidate_k: int,
    parent_top_k: int,
) -> dict[str, list[dict[str, Any]]]:
    result = collection.query(
        query_embeddings=embeddings,
        n_results=child_candidate_k,
        include=["documents", "metadatas", "distances"],
    )
    return collapse_child_results_to_parents(result, query_ids, parent_top_k)


def evaluate_rankings(
    cases: list[dict[str, Any]],
    rankings: dict[str, dict[str, list[dict[str, Any]]]],
) -> dict[str, Any]:
    labels = {
        case["query_id"]: {
            judgment["chunk_id"]: int(judgment["relevance"])
            for judgment in case["judgments"]
        }
        for case in cases
    }
    case_lookup = {case["query_id"]: case for case in cases}
    models = {
        model: evaluate_model(model_rankings, labels, case_lookup)
        for model, model_rankings in rankings.items()
    }
    return {"models": models, "model_order": model_order(models)}


def compare_with_baseline(
    parent_child: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, Any]:
    comparisons = {}
    candidates = {}
    for model, parent_child_result in parent_child["models"].items():
        baseline_result = baseline["models"].get(model)
        if baseline_result is None:
            raise ValueError(f"기준선 지표에 모델이 없습니다: {model}")
        comparisons[model] = {
            metric: (
                parent_child_result["macro"][metric]
                - baseline_result["macro"][metric]
            )
            for metric in (
                "recall_at_5",
                "recall_at_10",
                "mrr_at_10",
                "ndcg_at_10",
                "hit_at_10",
            )
        }
        candidates[f"{model}:article_baseline"] = baseline_result
        candidates[f"{model}:parent_child"] = parent_child_result
    return {
        "metric_deltas": comparisons,
        "candidate_order": model_order(candidates),
    }


def render_report(metrics: dict[str, Any], names: dict[str, str]) -> str:
    lines = [
        "# 법령 부모-자식 검색 50문항 평가",
        "",
        "항 단위 자식을 검색한 뒤 부모 조문별 최고 점수로 중복 제거하여",
        "기존 조문 단위 승인 라벨로 평가했다.",
        "",
        "| 순위 | 모델 | Recall@5 | Recall@10 | MRR@10 | nDCG@10 | "
        "Hit@10 | 치명 문항 불완전 검색 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, model in enumerate(metrics["model_order"], start=1):
        result = metrics["models"][model]
        macro = result["macro"]
        incomplete = len(result["critical"]["incomplete_recall_at_10"])
        lines.append(
            f"| {rank} | {names[model]} | {percent(macro['recall_at_5'])} | "
            f"{percent(macro['recall_at_10'])} | {macro['mrr_at_10']:.4f} | "
            f"{macro['ndcg_at_10']:.4f} | {percent(macro['hit_at_10'])} | "
            f"{incomplete} |"
        )
    lines.extend(
        (
            "",
            f"관련도 {POSITIVE_THRESHOLD} 이상을 정답 조문으로 처리했다.",
        )
    )
    comparison = metrics["baseline_comparison"]
    lines.extend(
        (
            "",
            "## 현재 조문 단위 기준선 대비 변화",
            "",
            "| 모델 | Recall@5 | Recall@10 | MRR@10 | nDCG@10 | Hit@10 |",
            "|---|---:|---:|---:|---:|---:|",
        )
    )
    for model in metrics["models"]:
        delta = comparison["metric_deltas"][model]
        lines.append(
            f"| {names[model]} | {delta['recall_at_5']:+.4f} | "
            f"{delta['recall_at_10']:+.4f} | {delta['mrr_at_10']:+.4f} | "
            f"{delta['ndcg_at_10']:+.4f} | {delta['hit_at_10']:+.4f} |"
        )
    lines.extend(
        (
            "",
            "전체 후보 순위: " + " > ".join(comparison["candidate_order"]),
        )
    )
    return "\n".join(lines) + "\n"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="부모-자식 법령 검색 평가")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--baseline-metrics", type=Path, default=DEFAULT_BASELINE_METRICS
    )
    parser.add_argument("--run-id", default="statute-50-parent-child-v01")
    parser.add_argument(
        "--models", nargs="+", choices=tuple(MODEL_SPECS), default=list(MODEL_SPECS)
    )
    parser.add_argument("--model-cache", type=Path, default=DEFAULT_MODEL_CACHE)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--parent-top-k", type=int, default=DEFAULT_PARENT_TOP_K)
    parser.add_argument(
        "--child-candidate-k", type=int, default=DEFAULT_CHILD_CANDIDATE_K
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.parent_top_k < 10:
        raise ValueError("공식 지표 계산을 위해 parent-top-k는 10 이상이어야 합니다.")
    if args.child_candidate_k < args.parent_top_k:
        raise ValueError("child-candidate-k는 parent-top-k 이상이어야 합니다.")

    dataset = args.dataset.resolve()
    cases = load_cases(dataset)
    configs = [retrieval_config(key) for key in args.models]
    collections = validate_parent_child_collections(configs)
    questions = [case["question"] for case in cases]
    query_ids = [case["query_id"] for case in cases]
    rankings = {}
    for config in configs:
        print(f"질의 임베딩 및 부모 조문 검색: {config.display_name}")
        embeddings = sentence_embeddings(
            config,
            questions,
            args.model_cache.resolve(),
            args.device,
        )
        rankings[config.key] = query_parent_rankings(
            collections[config.key],
            embeddings,
            query_ids,
            args.child_candidate_k,
            args.parent_top_k,
        )

    evaluated = evaluate_rankings(cases, rankings)
    baseline_path = args.baseline_metrics.resolve()
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_comparison = compare_with_baseline(evaluated, baseline)
    created_at = datetime.now(UTC).isoformat()
    metrics = {
        "schema_version": "0.1",
        "created_at": created_at,
        "dataset": str(dataset.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "query_count": len(cases),
        "parent_top_k": args.parent_top_k,
        "child_candidate_k": args.child_candidate_k,
        "positive_threshold": POSITIVE_THRESHOLD,
        "baseline_metrics": str(baseline_path.relative_to(PROJECT_ROOT)).replace(
            "\\", "/"
        ),
        "baseline_metrics_sha256": hashlib.sha256(
            baseline_path.read_bytes()
        ).hexdigest(),
        "baseline_comparison": baseline_comparison,
        **evaluated,
    }
    run_dir = args.output_root.resolve() / args.run_id
    names = {config.key: config.display_name for config in configs}
    write_json(run_dir / "rankings.json", rankings)
    write_json(run_dir / "metrics.json", metrics)
    write_json(
        run_dir / "run_manifest.json",
        {
            "schema_version": "0.1",
            "created_at": created_at,
            "dataset_sha256": metrics["dataset_sha256"],
            "models": [
                {
                    "key": config.key,
                    "display_name": config.display_name,
                    "model_name": config.model_name,
                    "model_revision": config.revision,
                    "db_dir": str(config.db_dir),
                    "collection_name": config.collection_name,
                    "source_sha256": collections[config.key].metadata[
                        "source_sha256"
                    ],
                }
                for config in configs
            ],
            "parent_top_k": args.parent_top_k,
            "child_candidate_k": args.child_candidate_k,
        },
    )
    report = render_report(metrics, names)
    (run_dir / "metrics.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"평가 결과: {run_dir}")


if __name__ == "__main__":
    main()
