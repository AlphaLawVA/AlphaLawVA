# prepare_statute_ai_first_human_review.py
"""
Description: 블라인드 AI 관련도 판정을 현재 시드 라벨과 비교하고 사람 검수가
필요한 충돌, 저확신, 신규 양성 후보와 품질 통제 표본을 추린다.
Author: ooheunsu
Date: 2026-09-04
Before:
    - 50문항 블라인드 후보 풀, AI 전수 판정, 현재 평가 데이터셋이 존재.

After:
    - 사람 검수 대상 JSONL·CSV와 선별 통계 JSON이 생성.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ml.data_collection.statutes.law_api_common import PROJECT_ROOT
from ml.evaluation.review_statute_retrieval_pool import read_jsonl, write_jsonl


EVALUATION_ROOT = PROJECT_ROOT / "data/statutes/evaluation"
DEFAULT_DATASET = (
    EVALUATION_ROOT / "datasets/statute_retrieval_v01_50_draft.jsonl"
)
DEFAULT_BLIND_POOL = (
    EVALUATION_ROOT
    / "reviews/statute_retrieval_v01_50_draft_"
    "statute-50-v01-initial_blind.jsonl"
)
DEFAULT_AI_REVIEW = (
    EVALUATION_ROOT
    / "reviews/statute_retrieval_v01_50_draft_"
    "statute-50-v01-initial_ai_review.jsonl"
)
DEFAULT_OUTPUT = (
    EVALUATION_ROOT
    / "reviews/statute_retrieval_v01_50_draft_"
    "statute-50-v01-initial_human_targets.jsonl"
)
DEFAULT_CSV = DEFAULT_OUTPUT.with_suffix(".csv")
DEFAULT_SUMMARY = DEFAULT_OUTPUT.with_name(
    "statute_retrieval_v01_50_draft_"
    "statute-50-v01-initial_human_targets_summary.json"
)
DEFAULT_CONTROL_RATE = 0.10
DEFAULT_SEED = "statute-50-v01-ai-first-human-review"


def load_seed_judgments(
    dataset_rows: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    judgments = {}
    for case in dataset_rows:
        for judgment in case.get("judgments", []):
            key = (case["query_id"], judgment["chunk_id"])
            if key in judgments:
                raise ValueError(f"중복 시드 판정입니다: {key}")
            judgments[key] = judgment
    return judgments


def load_ai_assessments(
    ai_rows: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    assessments = {}
    for row in ai_rows:
        for assessment in row["assessments"]:
            key = (row["query_id"], assessment["candidate_id"])
            if key in assessments:
                raise ValueError(f"중복 AI 판정입니다: {key}")
            assessments[key] = assessment
    return assessments


def _sample_key(row: dict[str, Any], seed: str) -> str:
    value = f"{seed}\0{row['query_id']}\0{row['candidate_id']}"
    return hashlib.sha256(value.encode()).hexdigest()


def build_candidate_rows(
    dataset_rows: list[dict[str, Any]],
    blind_rows: list[dict[str, Any]],
    ai_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seeds = load_seed_judgments(dataset_rows)
    ai = load_ai_assessments(ai_rows)
    rows = []
    for case in blind_rows:
        for candidate in case["candidates"]:
            ai_key = (case["query_id"], candidate["candidate_id"])
            if ai_key not in ai:
                raise ValueError(f"AI 판정이 없습니다: {ai_key}")
            assessment = ai[ai_key]
            if assessment.get("chunk_id") != candidate["chunk_id"]:
                raise ValueError(f"AI 판정 chunk_id가 다릅니다: {ai_key}")
            seed = seeds.get((case["query_id"], candidate["chunk_id"]))
            rows.append(
                {
                    "query_id": case["query_id"],
                    "question": case["question"],
                    "purpose": case["purpose"],
                    "category": case["category"],
                    "critical": bool(case["critical"]),
                    **{
                        key: candidate[key]
                        for key in (
                            "candidate_id",
                            "chunk_id",
                            "law_name",
                            "article_label",
                            "article_title",
                            "retrieval_text",
                        )
                    },
                    "seed_relevance": None if seed is None else seed["relevance"],
                    "seed_reason": "" if seed is None else seed.get("reason", ""),
                    "ai_relevance": assessment["relevance"],
                    "ai_confidence": assessment["confidence"],
                    "ai_reason": assessment["reason"],
                    "ai_evidence_excerpt": assessment["evidence_excerpt"],
                }
            )
    if len(rows) != len(ai):
        raise ValueError("블라인드 후보와 AI 판정 개수가 일치하지 않습니다.")
    return rows


def select_human_targets(
    rows: list[dict[str, Any]],
    control_rate: float = DEFAULT_CONTROL_RATE,
    seed: str = DEFAULT_SEED,
) -> list[dict[str, Any]]:
    if not 0 <= control_rate <= 1:
        raise ValueError("control_rate는 0과 1 사이여야 합니다.")
    reasons: dict[tuple[str, str], set[str]] = {}

    def add(row: dict[str, Any], reason: str) -> None:
        key = (row["query_id"], row["candidate_id"])
        reasons.setdefault(key, set()).add(reason)

    for row in rows:
        seed_score = row["seed_relevance"]
        ai_score = row["ai_relevance"]
        if row["ai_confidence"] == "low":
            add(row, "ai_low_confidence")
        if seed_score is None and ai_score >= 2:
            add(row, "unseeded_ai_positive")
        if seed_score is not None and seed_score != ai_score:
            add(row, "seed_ai_score_disagreement")
            if (seed_score >= 2) != (ai_score >= 2):
                add(row, "seed_ai_threshold_disagreement")

    unselected = [
        row
        for row in rows
        if (row["query_id"], row["candidate_id"]) not in reasons
    ]
    negative_controls = [
        row
        for row in unselected
        if row["ai_relevance"] < 2 and row["ai_confidence"] == "high"
    ]
    sample_count = round(len(negative_controls) * control_rate)
    for row in sorted(
        negative_controls,
        key=lambda item: _sample_key(item, seed),
    )[:sample_count]:
        add(row, "high_confidence_negative_control")

    for query_id in sorted({row["query_id"] for row in rows if row["critical"]}):
        query_rows = [row for row in rows if row["query_id"] == query_id]
        for positive in (True, False):
            candidates = [
                row
                for row in query_rows
                if (row["ai_relevance"] >= 2) == positive
                and (row["query_id"], row["candidate_id"]) not in reasons
            ]
            if candidates:
                add(
                    min(candidates, key=lambda item: _sample_key(item, seed)),
                    "critical_query_control",
                )

    by_key = {
        (row["query_id"], row["candidate_id"]): row for row in rows
    }
    selected = []
    for key in sorted(reasons):
        row = dict(by_key[key])
        row["selection_reasons"] = sorted(reasons[key])
        row["human_relevance"] = None
        row["human_reason"] = ""
        selected.append(row)
    return selected


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "query_id",
        "question",
        "critical",
        "candidate_id",
        "chunk_id",
        "law_name",
        "article_label",
        "article_title",
        "retrieval_text",
        "human_relevance",
        "human_reason",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            output = {key: row.get(key) for key in fields}
            writer.writerow(output)


def build_summary(
    all_rows: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    control_rate: float,
) -> dict[str, Any]:
    control_reasons = {
        "high_confidence_negative_control",
        "critical_query_control",
    }
    required_count = sum(
        any(reason not in control_reasons for reason in row["selection_reasons"])
        for row in selected
    )
    reason_counts = Counter(
        reason for row in selected for reason in row["selection_reasons"]
    )
    return {
        "schema_version": "0.1",
        "candidate_count": len(all_rows),
        "selected_count": len(selected),
        "required_review_count": required_count,
        "control_only_count": len(selected) - required_count,
        "excluded_count": len(all_rows) - len(selected),
        "control_rate": control_rate,
        "ai_relevance_distribution": dict(
            sorted(Counter(row["ai_relevance"] for row in all_rows).items())
        ),
        "ai_confidence_distribution": dict(
            sorted(Counter(row["ai_confidence"] for row in all_rows).items())
        ),
        "selection_reason_counts": dict(sorted(reason_counts.items())),
        "selected_query_count": len({row["query_id"] for row in selected}),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--blind-pool", type=Path, default=DEFAULT_BLIND_POOL)
    parser.add_argument("--ai-review", type=Path, default=DEFAULT_AI_REVIEW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--control-rate", type=float, default=DEFAULT_CONTROL_RATE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = build_candidate_rows(
        read_jsonl(args.dataset.resolve()),
        read_jsonl(args.blind_pool.resolve()),
        read_jsonl(args.ai_review.resolve()),
    )
    selected = select_human_targets(rows, args.control_rate)
    write_jsonl(args.output.resolve(), selected)
    write_csv(args.csv.resolve(), selected)
    summary = build_summary(rows, selected, args.control_rate)
    args.summary.resolve().write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "사람 검수 대상 생성 완료: "
        f"전체={len(rows)}, 검수={len(selected)}, 제외={len(rows) - len(selected)}"
    )


if __name__ == "__main__":
    main()
