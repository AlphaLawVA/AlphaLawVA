# finalize_statute_50_calibrated_review.py
"""
Description: 50문항 법령 검색 평가셋에 사람·AI 판정과 근거 재판정을
우선순위대로 병합하고 승인 데이터셋과 공식 검색 지표를 생성한다.
Author: ooheunsu
Date: 2026-09-05
Before:
    - 50문항 초안, 검색 후보, 두 AI 판정, 사람 40건 판정이 준비된 상태.

After:
    - 승인된 50문항 평가셋과 근거 판정 이력 및 모델별 공식 지표가 생성.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
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


ROOT = PROJECT_ROOT / "data/statutes/evaluation"
REVIEWS = ROOT / "reviews"
RUN = ROOT / "runs/statute-50-v01-initial"
DEFAULT_DRAFT = ROOT / "datasets/statute_retrieval_v01_50_draft.jsonl"
DEFAULT_BLIND = REVIEWS / (
    "statute_retrieval_v01_50_draft_statute-50-v01-initial_blind.jsonl"
)
DEFAULT_FIRST = REVIEWS / (
    "statute_retrieval_v01_50_draft_statute-50-v01-initial_ai_review.jsonl"
)
DEFAULT_MERGED = REVIEWS / (
    "statute_retrieval_v01_50_draft_"
    "statute-50-v01-calibrated_merged.jsonl"
)
DEFAULT_HUMAN = ROOT / (
    "approvals/statute_retrieval_v01_50_calibrated_human_decisions.json"
)
DEFAULT_ADJUDICATION = ROOT / (
    "approvals/statute_retrieval_v01_50_evidence_adjudication.json"
)
DEFAULT_APPROVED = ROOT / (
    "datasets/statute_retrieval_v01_50_approved.jsonl"
)
DEFAULT_MANIFEST = ROOT / (
    "approvals/statute_retrieval_v01_50_approval_manifest.json"
)
DEFAULT_RANKINGS = RUN / "rankings.json"
DEFAULT_RUN_MANIFEST = RUN / "run_manifest.json"
DEFAULT_METRICS = RUN / "metrics_approved.json"
DEFAULT_REPORT = RUN / "metrics_approved.md"
POSITIVE_THRESHOLD = 2


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repository_path(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT).as_posix()


def normalize_source_path(value: str) -> str:
    path = Path(value)
    try:
        return repository_path(path)
    except ValueError:
        return value


def _first_lookup(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict]:
    return {
        (row["query_id"], item["candidate_id"]): item
        for row in rows
        for item in row["assessments"]
    }


def _human_lookup(document: dict[str, Any]) -> dict[tuple[str, str], dict]:
    if document.get("review_status") != "completed":
        raise ValueError("사람 검수가 완료 상태가 아닙니다.")
    result = {}
    for row in document["decisions"]:
        key = (row["query_id"], row["candidate_id"])
        score = row["final_relevance"]
        if key in result or not isinstance(score, int) or not 0 <= score <= 3:
            raise ValueError(f"잘못된 사람 판정: {key}={score}")
        result[key] = row
    return result


def _calibrated_lookup(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict]:
    return {(row["query_id"], row["candidate_id"]): row for row in rows}


def build_datasets(
    draft: list[dict[str, Any]],
    blind: list[dict[str, Any]],
    first_rows: list[dict[str, Any]],
    merged_rows: list[dict[str, Any]],
    human_document: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    cases = {row["query_id"]: row for row in draft}
    first = _first_lookup(first_rows)
    calibrated = _calibrated_lookup(merged_rows)
    human = _human_lookup(human_document)
    hybrid = deepcopy(draft)
    hybrid_lookup = {row["query_id"]: row for row in hybrid}
    conservative = deepcopy(draft)
    conservative_lookup = {row["query_id"]: row for row in conservative}
    seen_human = set()
    source_counts: Counter[str] = Counter()

    for blind_case in blind:
        query_id = blind_case["query_id"]
        if query_id not in cases:
            raise ValueError(f"초안에 없는 질문: {query_id}")
        if int(query_id.rsplit("q", 1)[1]) <= 11:
            source_counts["approved_pilot"] += len(cases[query_id]["judgments"])
            continue

        judgments_by_chunk = {
            row["chunk_id"]: {
                **row,
                "label_source": "seed_evidence",
            }
            for row in cases[query_id]["judgments"]
        }
        candidate_to_chunk = {}
        for candidate in blind_case["candidates"]:
            key = (query_id, candidate["candidate_id"])
            candidate_to_chunk[key] = candidate["chunk_id"]
            if key not in first:
                raise ValueError(f"1차 AI 판정 누락: {key}")
            if key in human:
                decision = human[key]
                score = decision["final_relevance"]
                source = "human_review"
                reason = decision.get("reason") or "사람 최종 점수"
                seen_human.add(key)
            elif key in calibrated:
                decision = calibrated[key]
                score = int(decision["provisional_relevance"])
                source = "calibrated_ai"
                reason = decision["calibrated_reason"]
            else:
                decision = first[key]
                score = int(decision["relevance"])
                source = "first_ai_high_confidence"
                reason = decision["reason"]
            judgments_by_chunk[candidate["chunk_id"]] = {
                "chunk_id": candidate["chunk_id"],
                "relevance": score,
                "reason": reason,
                "label_source": source,
            }
        judgments = list(judgments_by_chunk.values())
        if len({row["chunk_id"] for row in judgments}) != len(judgments):
            raise ValueError(f"후보 중복 청크: {query_id}")
        source_counts.update(row["label_source"] for row in judgments)
        hybrid_lookup[query_id]["judgments"] = sorted(
            judgments, key=lambda row: row["chunk_id"]
        )
        hybrid_lookup[query_id]["review"] = {
            **hybrid_lookup[query_id]["review"],
            "status": "provisional",
            "reviewed_by": "human 40; calibrated AI; first AI",
            "updated_at": datetime.now(UTC).date().isoformat(),
        }

        conservative_judgments = {
            row["chunk_id"]: dict(row)
            for row in conservative_lookup[query_id]["judgments"]
        }
        for key, decision in human.items():
            if key[0] != query_id:
                continue
            chunk_id = candidate_to_chunk.get(key)
            if chunk_id is None:
                raise ValueError(f"사람 판정 후보를 찾을 수 없습니다: {key}")
            conservative_judgments[chunk_id] = {
                "chunk_id": chunk_id,
                "relevance": decision["final_relevance"],
                "reason": decision.get("reason") or "사람 최종 점수",
                "label_source": "human_review",
            }
        conservative_lookup[query_id]["judgments"] = sorted(
            conservative_judgments.values(), key=lambda row: row["chunk_id"]
        )
        conservative_lookup[query_id]["review"] = {
            **conservative_lookup[query_id]["review"],
            "status": "provisional",
            "reviewed_by": "approved seed evidence; human 40",
            "updated_at": datetime.now(UTC).date().isoformat(),
        }

    if seen_human != set(human):
        raise ValueError(f"반영되지 않은 사람 판정: {sorted(set(human) - seen_human)}")
    for dataset_name, dataset in (
        ("human_anchored_hybrid", hybrid),
        ("seed_human_conservative", conservative),
    ):
        if len(dataset) != 50:
            raise ValueError(f"{dataset_name} 문항 수 오류: {len(dataset)}")
        for case in dataset:
            if not any(
                row["relevance"] >= POSITIVE_THRESHOLD
                for row in case["judgments"]
            ):
                raise ValueError(f"정답 청크가 없는 질문: {dataset_name} {case['query_id']}")
    return hybrid, conservative, {"label_source_counts": dict(source_counts)}


def evaluate_dataset(
    cases: list[dict[str, Any]], rankings: dict[str, Any]
) -> dict[str, Any]:
    labels = {
        case["query_id"]: {
            row["chunk_id"]: row["relevance"] for row in case["judgments"]
        }
        for case in cases
    }
    case_lookup = {case["query_id"]: case for case in cases}
    models = {
        model: evaluate_model(model_rankings, labels, case_lookup)
        for model, model_rankings in rankings.items()
    }
    return {"models": models, "model_order": model_order(models)}


def apply_evidence_adjudication(
    cases: list[dict[str, Any]],
    adjudication: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if adjudication.get("status") != "approved":
        raise ValueError("근거 재판정이 승인 상태가 아닙니다.")

    output = deepcopy(cases)
    judgments = {
        (case["query_id"], row["chunk_id"]): row
        for case in output
        for row in case["judgments"]
    }
    seen = set()
    basis_counts: Counter[str] = Counter()
    changed = []
    for decision in adjudication["decisions"]:
        key = (decision["query_id"], decision["chunk_id"])
        if key in seen or key not in judgments:
            raise ValueError(f"잘못된 근거 재판정: {key}")
        seen.add(key)
        score = decision["final_relevance"]
        if not isinstance(score, int) or not 0 <= score <= 3:
            raise ValueError(f"허용되지 않는 최종 관련도: {key}={score}")
        judgment = judgments[key]
        before = judgment["relevance"]
        basis = decision["selected_basis"]
        judgment.update(
            relevance=score,
            reason=f"최종 근거 재판정: {decision['reason']}",
            label_source=f"evidence_adjudication_{basis}",
        )
        basis_counts[basis] += 1
        if before != score:
            changed.append(
                {
                    "query_id": key[0],
                    "chunk_id": key[1],
                    "before": before,
                    "after": score,
                    "selected_basis": basis,
                }
            )

    reviewed_by = (
        "Q1-Q11 final human approval; Q12-Q50 dual AI review and "
        "focused human review; evidence adjudication approved by ooheunsu"
    )
    for case in output:
        if not any(row["relevance"] >= POSITIVE_THRESHOLD for row in case["judgments"]):
            raise ValueError(f"정답 청크가 없는 질문: {case['query_id']}")
        case["review"] = {
            **case["review"],
            "status": "approved",
            "reviewed_by": reviewed_by,
            "updated_at": adjudication["reviewed_at"],
        }

    return output, {
        "decision_count": len(seen),
        "selected_basis_counts": dict(basis_counts),
        "changed_scores": changed,
    }


def control_summary(
    human_document: dict[str, Any], merged_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    calibrated = _calibrated_lookup(merged_rows)
    controls = [
        row
        for row in human_document["decisions"]
        if row["review_tier"] == "품질 표본"
    ]
    exact = sum(
        row["final_relevance"]
        == calibrated[(row["query_id"], row["candidate_id"])][
            "provisional_relevance"
        ]
        for row in controls
    )
    binary = sum(
        (row["final_relevance"] >= POSITIVE_THRESHOLD)
        == (
            calibrated[(row["query_id"], row["candidate_id"])][
                "provisional_relevance"
            ]
            >= POSITIVE_THRESHOLD
        )
        for row in controls
    )
    return {
        "count": len(controls),
        "exact_agreement_count": exact,
        "exact_agreement_rate": exact / len(controls),
        "binary_agreement_count": binary,
        "binary_agreement_rate": binary / len(controls),
    }


def render_report(metrics: dict[str, Any], names: dict[str, str]) -> str:
    lines = [
        "# 법령 검색 50문항 승인 평가",
        "",
        "사람 집중 검수와 AI 보조 판정을 병합하고, 충돌 5건을 법령 근거로",
        "최종 재판정했다. 관련도 2 이상을 정답으로 사용한다.",
        "",
        "| 순위 | 모델 | Recall@5 | Recall@10 | MRR@10 | nDCG@10 | Hit@10 | 치명 문항 불완전 검색 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, model in enumerate(metrics["model_order"], start=1):
        result = metrics["models"][model]
        macro = result["macro"]
        incomplete = len(result["critical"]["incomplete_recall_at_10"])
        lines.append(
            f"| {rank} | {names.get(model, model)} | "
            f"{percent(macro['recall_at_5'])} | "
            f"{percent(macro['recall_at_10'])} | "
            f"{macro['mrr_at_10']:.4f} | {macro['ndcg_at_10']:.4f} | "
            f"{percent(macro['hit_at_10'])} | {incomplete} |"
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", type=Path, default=DEFAULT_DRAFT)
    parser.add_argument("--blind", type=Path, default=DEFAULT_BLIND)
    parser.add_argument("--first", type=Path, default=DEFAULT_FIRST)
    parser.add_argument("--merged", type=Path, default=DEFAULT_MERGED)
    parser.add_argument("--human", type=Path, default=DEFAULT_HUMAN)
    parser.add_argument("--adjudication", type=Path, default=DEFAULT_ADJUDICATION)
    parser.add_argument("--approved", type=Path, default=DEFAULT_APPROVED)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--rankings", type=Path, default=DEFAULT_RANKINGS)
    parser.add_argument("--run-manifest", type=Path, default=DEFAULT_RUN_MANIFEST)
    parser.add_argument("--metrics", type=Path, default=DEFAULT_METRICS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    merged = read_jsonl(args.merged.resolve())
    human = read_json(args.human.resolve())
    hybrid, _, summary = build_datasets(
        read_jsonl(args.draft.resolve()),
        read_jsonl(args.blind.resolve()),
        read_jsonl(args.first.resolve()),
        merged,
        human,
    )
    adjudication = read_json(args.adjudication.resolve())
    approved, adjudication_summary = apply_evidence_adjudication(
        hybrid,
        adjudication,
    )
    write_jsonl(args.approved.resolve(), approved)
    rankings = read_json(args.rankings.resolve())
    result = evaluate_dataset(approved, rankings)
    metrics = {
        "schema_version": "0.1",
        "created_at": datetime.now(UTC).isoformat(),
        "dataset": repository_path(args.approved),
        "dataset_sha256": file_sha256(args.approved.resolve()),
        "positive_threshold": POSITIVE_THRESHOLD,
        "query_count": 50,
        "aggregation": "macro_average_over_queries",
        **result,
        "control_sample": control_summary(human, merged),
    }
    args.metrics.resolve().write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    run_manifest = read_json(args.run_manifest.resolve())
    names = {row["key"]: row["display_name"] for row in run_manifest["models"]}
    args.report.resolve().write_text(
        render_report(metrics, names), encoding="utf-8"
    )
    manifest = {
        "schema_version": "0.1",
        "status": "approved_internal_evaluation",
        "created_at": metrics["created_at"],
        "source_workbook": normalize_source_path(human["source_workbook"]),
        "human_decision_count": len(human["decisions"]),
        "adjudication": repository_path(args.adjudication),
        "adjudication_sha256": file_sha256(args.adjudication.resolve()),
        "approved_dataset": repository_path(args.approved),
        "approved_sha256": file_sha256(args.approved.resolve()),
        "metrics": repository_path(args.metrics),
        "control_sample": metrics["control_sample"],
        "model_order": metrics["model_order"],
        "selected_embedding_model": "bge_m3",
        "selection_status": "selected_for_statute_retrieval_v0.1",
        "adjudication_summary": adjudication_summary,
        **summary,
    }
    args.manifest.resolve().write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.report.resolve().read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
