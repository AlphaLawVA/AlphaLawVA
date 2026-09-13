# recheck_statute_positive_labels.py
"""
Description: 최종 근거 재검토에서 제외된 양성 법령 청크를 독립 판정하고
기존 재검토 결과와 병합해 완전한 독립 라벨을 생성한다.
Author: ooheunsu
Date: 2026-09-02
Before:
    - 잠정 골드와 1차 근거 재검토 결과 및 법령 청크가 준비된 상태.

After:
    - 양성 전수 재검토 결과, 통합 독립 판정 및 수정 골드셋이 생성.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ml.evaluation.prepare_statute_gold_adjudication import (
    PROJECT_ROOT,
    REVIEW_ROOT,
    load_chunks,
    read_jsonl,
    run_adjudication,
    write_jsonl,
)


EVALUATION_ROOT = PROJECT_ROOT / "data/statutes/evaluation"
DEFAULT_GOLD = EVALUATION_ROOT / "datasets/pilot_v01_provisional.jsonl"
DEFAULT_EXISTING = (
    REVIEW_ROOT / "pilot_v01_pilot-v01-initial_adjudication_v02.jsonl"
)
DEFAULT_TARGETS = (
    REVIEW_ROOT / "pilot_v01_pilot-v01-initial_positive_recheck_targets.jsonl"
)
DEFAULT_RECHECK = (
    REVIEW_ROOT / "pilot_v01_pilot-v01-initial_positive_recheck.jsonl"
)
DEFAULT_RECHECK_MANIFEST = (
    REVIEW_ROOT / "pilot_v01_pilot-v01-initial_positive_recheck_manifest.json"
)
DEFAULT_COMBINED = (
    REVIEW_ROOT / "pilot_v01_pilot-v01-initial_adjudication_complete.jsonl"
)
DEFAULT_REVISED_GOLD = (
    EVALUATION_ROOT / "datasets/pilot_v01_evidence_rechecked.jsonl"
)
DEFAULT_SUMMARY = (
    REVIEW_ROOT / "pilot_v01_pilot-v01-initial_positive_recheck_summary.json"
)
DEFAULT_CHUNK_ROOT = PROJECT_ROOT / "data/statutes/chunks"
DEFAULT_MODEL = "gpt-5.5"
POSITIVE_THRESHOLD = 2


def decision_lookup(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict]:
    result = {}
    for row in rows:
        for decision in row["decisions"]:
            key = (row["query_id"], decision["chunk_id"])
            if key in result:
                raise ValueError(f"중복 근거 재검토 판정: {key}")
            result[key] = decision
    return result


def build_positive_targets(
    cases: list[dict[str, Any]],
    existing_rows: list[dict[str, Any]],
    chunks: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    existing = decision_lookup(existing_rows)
    targets = []
    for case in cases:
        candidates = []
        for judgment in case["judgments"]:
            key = (case["query_id"], judgment["chunk_id"])
            if judgment["relevance"] < POSITIVE_THRESHOLD or key in existing:
                continue
            chunk = chunks[judgment["chunk_id"]]
            metadata = chunk["metadata"]
            candidates.append(
                {
                    "candidate_id": f"P{len(candidates) + 1:02d}",
                    "chunk_id": judgment["chunk_id"],
                    "law_name": metadata["law_name"],
                    "article_label": metadata["article_label"],
                    "article_title": metadata["article_title"],
                    "retrieval_text": chunk["retrieval_text"],
                    "target_type": "positive_agreement_recheck",
                    "target_reasons": ["positive_not_evidence_rechecked"],
                    "allowed_relevance": [0, 1, 2, 3],
                }
            )
        if candidates:
            targets.append(
                {
                    "query_id": case["query_id"],
                    "question": case["question"],
                    "purpose": case["purpose"],
                    "required_concepts": case["required_concepts"],
                    "candidates": candidates,
                }
            )
    return targets


def combine_decisions(
    existing_rows: list[dict[str, Any]],
    recheck_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    combined: dict[str, dict[str, Any]] = {}
    seen = set()
    for source, rows in (("initial", existing_rows), ("positive_recheck", recheck_rows)):
        for row in rows:
            target = combined.setdefault(
                row["query_id"],
                {"query_id": row["query_id"], "decisions": [], "sources": []},
            )
            target["sources"].append(
                {"type": source, "review_metadata": row.get("review_metadata", {})}
            )
            for decision in row["decisions"]:
                key = (row["query_id"], decision["chunk_id"])
                if key in seen:
                    raise ValueError(f"통합 중 중복 판정: {key}")
                seen.add(key)
                target["decisions"].append(decision)
    for row in combined.values():
        row["decisions"].sort(key=lambda item: item["chunk_id"])
    return [combined[query_id] for query_id in sorted(combined)]


def apply_independent_labels(
    cases: list[dict[str, Any]],
    combined_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    decisions = decision_lookup(combined_rows)
    output = []
    for case in cases:
        revised = dict(case)
        judgments = []
        for judgment in case["judgments"]:
            updated = dict(judgment)
            decision = decisions.get((case["query_id"], judgment["chunk_id"]))
            if decision is not None:
                updated["relevance"] = decision["independent_relevance"]
                updated["reason"] = f"독립 법령 근거 재검토: {decision['reason']}"
            judgments.append(updated)
        revised["judgments"] = judgments
        revised["review"] = {
            **case["review"],
            "status": "draft",
            "reviewed_by": "AI evidence recheck; human approval pending",
            "updated_at": datetime.now(timezone.utc).date().isoformat(),
        }
        output.append(revised)
    return output


def build_summary(
    original_cases: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    recheck_rows: list[dict[str, Any]],
    combined_rows: list[dict[str, Any]],
    revised_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    target_count = sum(len(row["candidates"]) for row in targets)
    decisions = decision_lookup(recheck_rows)
    original = {
        (case["query_id"], judgment["chunk_id"]): judgment["relevance"]
        for case in original_cases
        for judgment in case["judgments"]
    }
    changed_to_negative = [
        {
            "query_id": query_id,
            "chunk_id": chunk_id,
            "before": original[(query_id, chunk_id)],
            "after": decision["independent_relevance"],
            "reason": decision["reason"],
        }
        for (query_id, chunk_id), decision in sorted(decisions.items())
        if original[(query_id, chunk_id)] >= POSITIVE_THRESHOLD
        and decision["independent_relevance"] < POSITIVE_THRESHOLD
    ]
    combined = decision_lookup(combined_rows)
    remaining = [
        {"query_id": case["query_id"], "chunk_id": judgment["chunk_id"]}
        for case in original_cases
        for judgment in case["judgments"]
        if judgment["relevance"] >= POSITIVE_THRESHOLD
        and (case["query_id"], judgment["chunk_id"]) not in combined
    ]
    relevance_counts: Counter[int] = Counter(
        judgment["relevance"]
        for case in revised_cases
        for judgment in case["judgments"]
    )
    return {
        "target_count": target_count,
        "completed_count": len(decisions),
        "changed_from_positive_to_negative_count": len(changed_to_negative),
        "changed_from_positive_to_negative": changed_to_negative,
        "remaining_positive_without_evidence_recheck": remaining,
        "combined_decision_count": len(combined),
        "revised_relevance_counts": {
            str(score): relevance_counts[score] for score in range(4)
        },
        "revised_positive_counts_by_query": {
            case["query_id"]: sum(
                judgment["relevance"] >= POSITIVE_THRESHOLD
                for judgment in case["judgments"]
            )
            for case in revised_cases
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="미재검토 양성 법령 청크를 근거 중심으로 재판정합니다."
    )
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--existing", type=Path, default=DEFAULT_EXISTING)
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--recheck", type=Path, default=DEFAULT_RECHECK)
    parser.add_argument(
        "--recheck-manifest", type=Path, default=DEFAULT_RECHECK_MANIFEST
    )
    parser.add_argument("--combined", type=Path, default=DEFAULT_COMBINED)
    parser.add_argument("--revised-gold", type=Path, default=DEFAULT_REVISED_GOLD)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--chunk-root", type=Path, default=DEFAULT_CHUNK_ROOT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prepare-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cases = read_jsonl(args.gold.resolve())
    existing_rows = read_jsonl(args.existing.resolve())
    targets = build_positive_targets(
        cases,
        existing_rows,
        load_chunks(args.chunk_root.resolve()),
    )
    write_jsonl(args.targets.resolve(), targets)
    target_count = sum(len(row["candidates"]) for row in targets)
    print(f"양성 근거 재검토 준비: 질문={len(targets)}, 후보={target_count}")
    if args.prepare_only:
        return
    recheck_rows = run_adjudication(
        targets,
        args.targets.resolve(),
        args.recheck.resolve(),
        args.recheck_manifest.resolve(),
        args.model,
    )
    combined_rows = combine_decisions(existing_rows, recheck_rows)
    write_jsonl(args.combined.resolve(), combined_rows)
    revised_cases = apply_independent_labels(cases, combined_rows)
    write_jsonl(args.revised_gold.resolve(), revised_cases)
    summary = build_summary(
        cases, targets, recheck_rows, combined_rows, revised_cases
    )
    args.summary.resolve().write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
