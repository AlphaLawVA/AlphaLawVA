# prepare_statute_calibrated_human_review.py
"""
Description: 1차·보정 AI와 시드 판정을 병합해 신규 39문항의 사람 검수
대상을 필수 충돌과 결정적 품질 표본으로 축소한다.
Author: ooheunsu
Date: 2026-09-05
Before:
    - Q12~Q50의 1차 AI 대상표와 보정 AI 판정이 존재.

After:
    - 146건 병합표, 축소 사람 검수표, 선정 요약이 생성.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from ml.data_collection.statutes.law_api_common import PROJECT_ROOT
from ml.evaluation.review_statute_retrieval_pool import read_jsonl, write_jsonl


EVALUATION_ROOT = PROJECT_ROOT / "data/statutes/evaluation"
REVIEW_ROOT = EVALUATION_ROOT / "reviews"
DEFAULT_TARGETS = (
    REVIEW_ROOT
    / "statute_retrieval_v01_50_draft_"
    "statute-50-v01-initial_human_targets.jsonl"
)
DEFAULT_CALIBRATED = (
    REVIEW_ROOT
    / "statute_retrieval_v01_50_draft_"
    "statute-50-v01-target-calibrated_ai_review.jsonl"
)
DEFAULT_MERGED = (
    REVIEW_ROOT
    / "statute_retrieval_v01_50_draft_"
    "statute-50-v01-calibrated_merged.jsonl"
)
DEFAULT_HUMAN_TARGETS = (
    REVIEW_ROOT
    / "statute_retrieval_v01_50_draft_"
    "statute-50-v01-calibrated_human_targets.jsonl"
)
DEFAULT_HUMAN_CSV = DEFAULT_HUMAN_TARGETS.with_suffix(".csv")
DEFAULT_SUMMARY = (
    REVIEW_ROOT
    / "statute_retrieval_v01_50_draft_"
    "statute-50-v01-calibrated_human_targets_summary.json"
)
CONTROL_RATE = 0.10


def _query_number(query_id: str) -> int:
    return int(query_id.rsplit("q", 1)[1])


def _is_positive(score: int | None) -> bool | None:
    return None if score is None else score >= 2


def _stable_order(row: dict[str, Any]) -> str:
    key = f"{row['query_id']}|{row['chunk_id']}"
    return hashlib.sha256(key.encode()).hexdigest()


def _index_calibrated(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (row["query_id"], item["chunk_id"]): item
        for row in rows
        for item in row["assessments"]
    }


def merge_reviews(
    target_rows: list[dict[str, Any]],
    calibrated_rows: list[dict[str, Any]],
    query_min: int = 12,
    query_max: int = 50,
) -> list[dict[str, Any]]:
    calibrated = _index_calibrated(calibrated_rows)
    merged = []
    for source in target_rows:
        number = _query_number(source["query_id"])
        if not query_min <= number <= query_max:
            continue
        key = (source["query_id"], source["chunk_id"])
        if key not in calibrated:
            raise ValueError(f"보정 AI 판정이 없습니다: {key}")
        second = calibrated[key]
        first_score = int(source["ai_relevance"])
        second_score = int(second["relevance"])
        seed_score = source.get("seed_relevance")
        reasons = []
        if _is_positive(first_score) != _is_positive(second_score):
            reasons.append("first_second_threshold_disagreement")
        elif (
            seed_score is not None
            and _is_positive(seed_score) != _is_positive(second_score)
        ):
            reasons.append("seed_threshold_conflict_with_ai_consensus")
        if (
            source["ai_confidence"] == "low"
            or second["confidence"] == "low"
        ):
            reasons.append("ai_low_confidence")
        merged.append(
            {
                **source,
                "calibrated_relevance": second_score,
                "calibrated_confidence": second["confidence"],
                "calibrated_reason": second["reason"],
                "calibrated_evidence_excerpt": second["evidence_excerpt"],
                "first_second_exact_agreement": first_score == second_score,
                "first_second_threshold_agreement": (
                    _is_positive(first_score) == _is_positive(second_score)
                ),
                "provisional_relevance": second_score,
                "human_review_reasons": reasons,
                "human_review_tier": "required" if reasons else "auto",
            }
        )
    if len(merged) != len(calibrated):
        raise ValueError(
            "대상표와 보정 AI 판정 수가 다릅니다: "
            f"targets={len(merged)}, calibrated={len(calibrated)}"
        )
    return merged


def _sample_group(
    rows: list[dict[str, Any]],
    count: int,
) -> list[dict[str, Any]]:
    return sorted(rows, key=_stable_order)[:count]


def select_human_review(
    merged_rows: list[dict[str, Any]],
    control_rate: float = CONTROL_RATE,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    required = [
        row for row in merged_rows if row["human_review_tier"] == "required"
    ]
    consensus = [
        row for row in merged_rows if row["human_review_tier"] == "auto"
    ]
    control_count = math.ceil(len(consensus) * control_rate)

    groups: dict[tuple[bool, bool], list[dict[str, Any]]] = {
        (positive, critical): []
        for positive in (False, True)
        for critical in (False, True)
    }
    for row in consensus:
        groups[(bool(_is_positive(row["provisional_relevance"])), row["critical"])].append(row)

    base = control_count // 4
    remainder = control_count % 4
    group_order = [(False, False), (True, False), (False, True), (True, True)]
    controls = []
    for index, key in enumerate(group_order):
        quota = base + (1 if index < remainder else 0)
        controls.extend(_sample_group(groups[key], min(quota, len(groups[key]))))
    if len(controls) < control_count:
        selected_keys = {
            (row["query_id"], row["chunk_id"]) for row in controls
        }
        remaining = [
            row
            for row in consensus
            if (row["query_id"], row["chunk_id"]) not in selected_keys
        ]
        controls.extend(
            _sample_group(remaining, control_count - len(controls))
        )

    control_keys = {(row["query_id"], row["chunk_id"]) for row in controls}
    output = []
    for row in merged_rows:
        updated = dict(row)
        key = (row["query_id"], row["chunk_id"])
        if key in control_keys:
            updated["human_review_tier"] = "control"
            updated["human_review_reasons"] = ["quality_control_sample"]
        if updated["human_review_tier"] != "auto":
            updated["human_relevance"] = None
            updated["human_reason"] = ""
            output.append(updated)

    tier_order = {"required": 0, "control": 1}
    output.sort(
        key=lambda row: (
            tier_order[row["human_review_tier"]],
            _query_number(row["query_id"]),
            row["candidate_id"],
        )
    )
    reason_counts = Counter(
        reason
        for row in output
        for reason in row["human_review_reasons"]
    )
    summary = {
        "schema_version": "0.1",
        "policy": {
            "positive_threshold": 2,
            "provisional_score": "calibrated_ai_relevance",
            "required_when": [
                "first_second_threshold_disagreement",
                "seed_threshold_conflict_with_ai_consensus",
                "ai_low_confidence",
            ],
            "control_rate": control_rate,
            "control_sampling": (
                "deterministic_sha256_stratified_by_positive_and_critical"
            ),
        },
        "source_candidate_count": len(merged_rows),
        "first_second_exact_disagreement_count": sum(
            not row["first_second_exact_agreement"] for row in merged_rows
        ),
        "first_second_threshold_disagreement_count": sum(
            not row["first_second_threshold_agreement"] for row in merged_rows
        ),
        "same_class_score_disagreement_count": sum(
            not row["first_second_exact_agreement"]
            and row["first_second_threshold_agreement"]
            for row in merged_rows
        ),
        "required_count": len(required),
        "control_count": len(controls),
        "human_review_count": len(output),
        "auto_provisional_count": len(merged_rows) - len(output),
        "reason_counts": dict(sorted(reason_counts.items())),
    }
    return output, summary


def write_blind_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "review_tier",
        "query_id",
        "question",
        "candidate_id",
        "law_name",
        "article_label",
        "article_title",
        "retrieval_text",
        "human_relevance",
        "human_reason",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "review_tier": row["human_review_tier"],
                    "query_id": row["query_id"],
                    "question": row["question"],
                    "candidate_id": row["candidate_id"],
                    "law_name": row["law_name"],
                    "article_label": row["article_label"],
                    "article_title": row["article_title"],
                    "retrieval_text": row["retrieval_text"],
                    "human_relevance": "",
                    "human_reason": "",
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--calibrated", type=Path, default=DEFAULT_CALIBRATED)
    parser.add_argument("--merged", type=Path, default=DEFAULT_MERGED)
    parser.add_argument("--human-targets", type=Path, default=DEFAULT_HUMAN_TARGETS)
    parser.add_argument("--human-csv", type=Path, default=DEFAULT_HUMAN_CSV)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--control-rate", type=float, default=CONTROL_RATE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    merged = merge_reviews(
        read_jsonl(args.targets.resolve()),
        read_jsonl(args.calibrated.resolve()),
    )
    human_targets, summary = select_human_review(
        merged,
        args.control_rate,
    )
    write_jsonl(args.merged.resolve(), merged)
    write_jsonl(args.human_targets.resolve(), human_targets)
    write_blind_csv(args.human_csv.resolve(), human_targets)
    args.summary.resolve().write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
