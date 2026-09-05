# evaluate_statute_label_sensitivity.py
"""
Description: 동일한 법령 검색 순위에 다수결 및 법령 독립 재검토 라벨을
적용해 모델별 검색 지표와 라벨 민감도를 계산한다.
Author: ooheunsu
Date: 2026-09-02
Before:
    - 잠정 골드, 독립 재검토 결과, 모델별 Top-10 순위가 준비된 상태.

After:
    - 두 라벨 정책의 모델별 지표와 비교 보고서가 평가 실행 폴더에 생성.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_ROOT = PROJECT_ROOT / "data/statutes/evaluation"
DEFAULT_GOLD = EVALUATION_ROOT / "datasets/pilot_v01_provisional.jsonl"
DEFAULT_ADJUDICATION = (
    EVALUATION_ROOT
    / "reviews/pilot_v01_pilot-v01-initial_adjudication_complete.jsonl"
)
DEFAULT_RANKINGS = EVALUATION_ROOT / "runs/pilot-v01-initial/rankings.json"
DEFAULT_RUN_MANIFEST = (
    EVALUATION_ROOT / "runs/pilot-v01-initial/run_manifest.json"
)
DEFAULT_OUTPUT = (
    EVALUATION_ROOT
    / "runs/pilot-v01-initial/metrics_label_sensitivity.json"
)
DEFAULT_REPORT = (
    EVALUATION_ROOT
    / "runs/pilot-v01-initial/metrics_label_sensitivity.md"
)
POSITIVE_THRESHOLD = 2


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_label_policies(
    gold_cases: list[dict[str, Any]],
    adjudication_rows: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, int]]]:
    majority = {
        case["query_id"]: {
            judgment["chunk_id"]: int(judgment["relevance"])
            for judgment in case["judgments"]
        }
        for case in gold_cases
    }
    independent = {
        query_id: dict(judgments) for query_id, judgments in majority.items()
    }
    for row in adjudication_rows:
        query_id = row["query_id"]
        if query_id not in independent:
            raise ValueError(f"재검토 결과에 알 수 없는 질문이 있습니다: {query_id}")
        for decision in row["decisions"]:
            independent[query_id][decision["chunk_id"]] = int(
                decision["independent_relevance"]
            )
    return {"majority": majority, "independent": independent}


def dcg(relevances: list[int]) -> float:
    return sum(
        (2**relevance - 1) / math.log2(rank + 1)
        for rank, relevance in enumerate(relevances, start=1)
    )


def query_metrics(
    ranked_chunk_ids: list[str],
    judgments: dict[str, int],
) -> dict[str, float]:
    relevant = {
        chunk_id
        for chunk_id, relevance in judgments.items()
        if relevance >= POSITIVE_THRESHOLD
    }
    if not relevant:
        raise ValueError("관련도 2 이상인 정답 청크가 없습니다.")
    top_5 = ranked_chunk_ids[:5]
    top_10 = ranked_chunk_ids[:10]
    found_5 = len(relevant.intersection(top_5))
    found_10 = len(relevant.intersection(top_10))
    first_relevant_rank = next(
        (
            rank
            for rank, chunk_id in enumerate(top_10, start=1)
            if chunk_id in relevant
        ),
        None,
    )
    retrieved_relevances = [judgments.get(chunk_id, 0) for chunk_id in top_10]
    ideal_relevances = sorted(judgments.values(), reverse=True)[:10]
    ideal_dcg = dcg(ideal_relevances)
    return {
        "recall_at_5": found_5 / len(relevant),
        "recall_at_10": found_10 / len(relevant),
        "mrr_at_10": 0.0 if first_relevant_rank is None else 1 / first_relevant_rank,
        "ndcg_at_10": 0.0 if ideal_dcg == 0 else dcg(retrieved_relevances) / ideal_dcg,
        "precision_at_10": found_10 / len(top_10),
        "hit_at_10": float(found_10 > 0),
    }


def evaluate_model(
    model_rankings: dict[str, list[dict[str, Any]]],
    labels: dict[str, dict[str, int]],
    case_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if set(model_rankings) != set(labels):
        raise ValueError("검색 순위와 골드 라벨의 질문 집합이 다릅니다.")
    per_query = []
    for query_id in sorted(labels):
        rows = model_rankings[query_id]
        chunk_ids = [row["chunk_id"] for row in rows]
        if len(chunk_ids) < 10:
            raise ValueError(f"Top-10 검색 결과가 부족합니다: {query_id}")
        metrics = query_metrics(chunk_ids, labels[query_id])
        per_query.append(
            {
                "query_id": query_id,
                "critical": bool(case_lookup[query_id]["critical"]),
                **metrics,
            }
        )
    metric_names = (
        "recall_at_5",
        "recall_at_10",
        "mrr_at_10",
        "ndcg_at_10",
        "precision_at_10",
        "hit_at_10",
    )
    macro = {
        name: sum(row[name] for row in per_query) / len(per_query)
        for name in metric_names
    }
    critical_rows = [row for row in per_query if row["critical"]]
    return {
        "macro": macro,
        "critical": {
            "query_count": len(critical_rows),
            "no_hit_at_10": [
                row["query_id"] for row in critical_rows if row["hit_at_10"] == 0
            ],
            "incomplete_recall_at_10": [
                row["query_id"]
                for row in critical_rows
                if row["recall_at_10"] < 1
            ],
        },
        "per_query": per_query,
    }


def model_order(results: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(
        results,
        key=lambda model: (
            len(results[model]["critical"]["incomplete_recall_at_10"]),
            -results[model]["macro"]["recall_at_10"],
            -results[model]["macro"]["ndcg_at_10"],
            -results[model]["macro"]["mrr_at_10"],
            model,
        ),
    )


def evaluate(
    gold_cases: list[dict[str, Any]],
    adjudication_rows: list[dict[str, Any]],
    rankings: dict[str, dict[str, list[dict[str, Any]]]],
) -> dict[str, Any]:
    case_lookup = {case["query_id"]: case for case in gold_cases}
    policies = build_label_policies(gold_cases, adjudication_rows)
    output = {}
    for policy_name, labels in policies.items():
        model_results = {
            model: evaluate_model(model_rankings, labels, case_lookup)
            for model, model_rankings in rankings.items()
        }
        output[policy_name] = {
            "positive_label_counts": {
                query_id: sum(
                    relevance >= POSITIVE_THRESHOLD
                    for relevance in query_labels.values()
                )
                for query_id, query_labels in labels.items()
            },
            "models": model_results,
            "model_order": model_order(model_results),
        }
    majority_order = output["majority"]["model_order"]
    independent_order = output["independent"]["model_order"]
    return {
        "policies": output,
        "comparison": {
            "model_order_changed": majority_order != independent_order,
            "majority_order": majority_order,
            "independent_order": independent_order,
        },
    }


def percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_report(
    result: dict[str, Any],
    display_names: dict[str, str],
) -> str:
    lines = [
        "# 법령 검색 모델 라벨 민감도 평가",
        "",
        "동일한 Top-10 검색 결과를 다수결 잠정 라벨과 법령 독립 재검토 "
        "라벨로 각각 평가했다.",
        "",
    ]
    for policy_name, title in (
        ("majority", "다수결 잠정 라벨"),
        ("independent", "법령 독립 재검토 라벨"),
    ):
        policy = result["policies"][policy_name]
        lines.extend(
            [
                f"## {title}",
                "",
                "| 순위 | 모델 | Recall@5 | Recall@10 | MRR@10 | "
                "nDCG@10 | Precision@10 | Hit@10 | 치명 문항 불완전 검색 |",
                "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for rank, model in enumerate(policy["model_order"], start=1):
            metrics = policy["models"][model]
            macro = metrics["macro"]
            critical_count = len(
                metrics["critical"]["incomplete_recall_at_10"]
            )
            lines.append(
                f"| {rank} | {display_names.get(model, model)} | "
                f"{percent(macro['recall_at_5'])} | "
                f"{percent(macro['recall_at_10'])} | "
                f"{macro['mrr_at_10']:.4f} | "
                f"{macro['ndcg_at_10']:.4f} | "
                f"{percent(macro['precision_at_10'])} | "
                f"{percent(macro['hit_at_10'])} | {critical_count} |"
            )
        lines.append("")
    comparison = result["comparison"]
    lines.extend(
        [
            "## 비교",
            "",
            f"- 모델 순위 변경: {'있음' if comparison['model_order_changed'] else '없음'}",
            "- 다수결 순위: "
            + " > ".join(display_names.get(key, key) for key in comparison["majority_order"]),
            "- 독립 재검토 순위: "
            + " > ".join(
                display_names.get(key, key) for key in comparison["independent_order"]
            ),
            "",
            "관련도 2 이상을 정답으로 처리했다. nDCG@10은 "
            "`2^relevance - 1` 이득을 사용한다.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="다수결 및 독립 재검토 라벨의 검색 지표를 비교합니다."
    )
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument(
        "--adjudication", type=Path, default=DEFAULT_ADJUDICATION
    )
    parser.add_argument("--rankings", type=Path, default=DEFAULT_RANKINGS)
    parser.add_argument(
        "--run-manifest", type=Path, default=DEFAULT_RUN_MANIFEST
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = {
        "gold": args.gold.resolve(),
        "adjudication": args.adjudication.resolve(),
        "rankings": args.rankings.resolve(),
        "run_manifest": args.run_manifest.resolve(),
    }
    gold_cases = read_jsonl(paths["gold"])
    adjudication_rows = read_jsonl(paths["adjudication"])
    rankings = read_json(paths["rankings"])
    run_manifest = read_json(paths["run_manifest"])
    result = evaluate(gold_cases, adjudication_rows, rankings)
    result = {
        "schema_version": "0.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "query_count": len(gold_cases),
        "top_k": 10,
        "metric_definition": {
            "positive_threshold": POSITIVE_THRESHOLD,
            "aggregation": "macro_average_over_queries",
            "ndcg_gain": "2^relevance - 1",
            "model_order": [
                "critical incomplete Recall@10 count ascending",
                "Recall@10 descending",
                "nDCG@10 descending",
                "MRR@10 descending",
            ],
        },
        "inputs": {
            name: {
                "path": str(path.relative_to(PROJECT_ROOT)),
                "sha256": file_sha256(path),
            }
            for name, path in paths.items()
        },
        **result,
    }
    output_path = args.output.resolve()
    report_path = args.report.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    display_names = {
        model["key"]: model["display_name"]
        for model in run_manifest["models"]
    }
    report_path.write_text(
        render_report(result, display_names),
        encoding="utf-8",
    )
    print(report_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
