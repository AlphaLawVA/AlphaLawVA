# finalize_statute_human_review.py
"""
Description: 사람의 최종 검수 결정을 법령 검색 평가셋 초안에 반영하고
승인된 11문항 골드셋과 최종 검색 지표를 생성한다.
Author: ooheunsu
Date: 2026-09-04
Before:
    - 법령 근거 재판정 초안과 사람 최종 검수 결정 및 검색 순위가 준비된 상태.
After:
    - 승인된 골드셋, 승인 기록, 모델별 최종 검색 지표가 생성.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ml.evaluation.evaluate_statute_label_sensitivity import (
    evaluate_model,
    model_order,
    percent,
)
from ml.evaluation.prepare_statute_gold_adjudication import (
    PROJECT_ROOT,
    read_jsonl,
    write_jsonl,
)


EVALUATION_ROOT = PROJECT_ROOT / "data/statutes/evaluation"
DEFAULT_DRAFT = EVALUATION_ROOT / "datasets/pilot_v01_evidence_rechecked.jsonl"
DEFAULT_DECISIONS = (
    EVALUATION_ROOT / "approvals/pilot_v01_final_human_decisions.json"
)
DEFAULT_APPROVED = EVALUATION_ROOT / "datasets/pilot_v01_approved.jsonl"
DEFAULT_MANIFEST = (
    EVALUATION_ROOT / "approvals/pilot_v01_final_approval_manifest.json"
)
DEFAULT_RANKINGS = EVALUATION_ROOT / "runs/pilot-v01-initial/rankings.json"
DEFAULT_RUN_MANIFEST = (
    EVALUATION_ROOT / "runs/pilot-v01-initial/run_manifest.json"
)
DEFAULT_METRICS = EVALUATION_ROOT / "runs/pilot-v01-initial/metrics_approved.json"
DEFAULT_REPORT = EVALUATION_ROOT / "runs/pilot-v01-initial/metrics_approved.md"
POSITIVE_THRESHOLD = 2
EXPECTED_REVIEW_SCOPE = {
    "question_count": 11,
    "positive_judgment_count": 38,
    "changed_binary_label_count": 42,
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_human_decisions(
    draft_cases: list[dict[str, Any]],
    decisions: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if decisions.get("review_status") != "completed":
        raise ValueError("사람 최종 검수가 완료 상태가 아닙니다.")
    if decisions.get("review_scope") != EXPECTED_REVIEW_SCOPE:
        raise ValueError("사람 최종 검수 범위가 예상 수치와 다릅니다.")

    output = deepcopy(draft_cases)
    case_lookup = {case["query_id"]: case for case in output}
    if len(case_lookup) != len(output):
        raise ValueError("중복 query_id가 있습니다.")

    question_changes = []
    for resolution in decisions.get("question_resolutions", []):
        query_id = resolution["query_id"]
        if query_id not in case_lookup:
            raise ValueError(f"알 수 없는 질문 결정: {query_id}")
        before = case_lookup[query_id]["question"]
        case_lookup[query_id]["question"] = resolution["final_question"]
        question_changes.append(
            {
                "query_id": query_id,
                "before": before,
                "after": resolution["final_question"],
                "reason": resolution["reason"],
            }
        )

    judgment_lookup = {
        (case["query_id"], judgment["chunk_id"]): judgment
        for case in output
        for judgment in case["judgments"]
    }
    judgment_resolutions = []
    changed_scores = []
    seen = set()
    for resolution in decisions.get("judgment_resolutions", []):
        key = (resolution["query_id"], resolution["chunk_id"])
        if key in seen:
            raise ValueError(f"중복 사람 최종 판정: {key}")
        seen.add(key)
        if key not in judgment_lookup:
            raise ValueError(f"알 수 없는 질문-청크 결정: {key}")
        score = resolution["final_relevance"]
        if not isinstance(score, int) or not 0 <= score <= 3:
            raise ValueError(f"허용되지 않는 최종 관련도: {key} {score}")
        judgment = judgment_lookup[key]
        before = judgment["relevance"]
        judgment["relevance"] = score
        judgment["reason"] = f"사람 최종 검수: {resolution['reason']}"
        record = {
            "query_id": key[0],
            "chunk_id": key[1],
            "before": before,
            "after": score,
            "reason": resolution["reason"],
        }
        judgment_resolutions.append(record)
        if before != score:
            changed_scores.append(record)

    reviewed_by = f"{decisions['reviewer']}; AI evidence recheck"
    for case in output:
        positives = [
            row for row in case["judgments"] if row["relevance"] >= POSITIVE_THRESHOLD
        ]
        if not positives:
            raise ValueError(f"정답 청크가 없는 질문: {case['query_id']}")
        if not any(row["relevance"] == 3 for row in positives):
            raise ValueError(f"핵심 근거 3점이 없는 질문: {case['query_id']}")
        case["review"] = {
            **case["review"],
            "status": "approved",
            "reviewed_by": reviewed_by,
            "updated_at": decisions["reviewed_at"],
        }

    relevance_counts = Counter(
        judgment["relevance"]
        for case in output
        for judgment in case["judgments"]
    )
    summary = {
        "question_count": len(output),
        "judgment_count": sum(len(case["judgments"]) for case in output),
        "positive_count": sum(
            judgment["relevance"] >= POSITIVE_THRESHOLD
            for case in output
            for judgment in case["judgments"]
        ),
        "relevance_counts": {
            str(score): relevance_counts[score] for score in range(4)
        },
        "question_changes": question_changes,
        "judgment_resolutions": judgment_resolutions,
        "changed_scores": changed_scores,
    }
    return output, summary


def evaluate_approved(
    approved_cases: list[dict[str, Any]],
    rankings: dict[str, dict[str, list[dict[str, Any]]]],
) -> dict[str, Any]:
    labels = {
        case["query_id"]: {
            judgment["chunk_id"]: judgment["relevance"]
            for judgment in case["judgments"]
        }
        for case in approved_cases
    }
    case_lookup = {case["query_id"]: case for case in approved_cases}
    models = {
        model: evaluate_model(model_rankings, labels, case_lookup)
        for model, model_rankings in rankings.items()
    }
    return {"models": models, "model_order": model_order(models)}


def render_metrics(result: dict[str, Any], display_names: dict[str, str]) -> str:
    lines = [
        "# 법령 검색 11문항 사람 승인 라벨 평가",
        "",
        "관련도 2 이상을 정답으로 사용한 사람 최종 승인 결과다.",
        "",
        "| 순위 | 모델 | Recall@5 | Recall@10 | MRR@10 | nDCG@10 | Precision@10 | Hit@10 | 치명 문항 불완전 검색 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, model in enumerate(result["model_order"], start=1):
        model_result = result["models"][model]
        macro = model_result["macro"]
        incomplete = len(model_result["critical"]["incomplete_recall_at_10"])
        lines.append(
            f"| {rank} | {display_names.get(model, model)} | "
            f"{percent(macro['recall_at_5'])} | "
            f"{percent(macro['recall_at_10'])} | "
            f"{macro['mrr_at_10']:.4f} | {macro['ndcg_at_10']:.4f} | "
            f"{percent(macro['precision_at_10'])} | "
            f"{percent(macro['hit_at_10'])} | {incomplete} |"
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="사람 검수 결정을 반영해 11문항 골드셋을 승인합니다."
    )
    parser.add_argument("--draft", type=Path, default=DEFAULT_DRAFT)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--approved", type=Path, default=DEFAULT_APPROVED)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--rankings", type=Path, default=DEFAULT_RANKINGS)
    parser.add_argument("--run-manifest", type=Path, default=DEFAULT_RUN_MANIFEST)
    parser.add_argument("--metrics", type=Path, default=DEFAULT_METRICS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    decisions = read_json(args.decisions.resolve())
    approved, summary = apply_human_decisions(
        read_jsonl(args.draft.resolve()), decisions
    )
    write_jsonl(args.approved.resolve(), approved)

    result = evaluate_approved(approved, read_json(args.rankings.resolve()))
    metrics = {
        "schema_version": "0.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.approved.resolve().relative_to(PROJECT_ROOT)),
        "dataset_sha256": file_sha256(args.approved.resolve()),
        "query_count": len(approved),
        "positive_threshold": POSITIVE_THRESHOLD,
        "aggregation": "macro_average_over_queries",
        **result,
    }
    args.metrics.resolve().write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    run_manifest = read_json(args.run_manifest.resolve())
    display_names = {
        model["key"]: model["display_name"] for model in run_manifest["models"]
    }
    args.report.resolve().write_text(
        render_metrics(metrics, display_names), encoding="utf-8"
    )

    approval_manifest = {
        "schema_version": "0.1",
        "status": "approved",
        "approved_at": decisions["reviewed_at"],
        "reviewer": decisions["reviewer"],
        "source_draft": str(args.draft.resolve().relative_to(PROJECT_ROOT)),
        "source_draft_sha256": file_sha256(args.draft.resolve()),
        "decisions": str(args.decisions.resolve().relative_to(PROJECT_ROOT)),
        "decisions_sha256": file_sha256(args.decisions.resolve()),
        "approved_dataset": str(args.approved.resolve().relative_to(PROJECT_ROOT)),
        "approved_dataset_sha256": file_sha256(args.approved.resolve()),
        "summary": summary,
    }
    args.manifest.resolve().write_text(
        json.dumps(approval_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.report.resolve().read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
