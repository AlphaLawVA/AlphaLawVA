# prepare_statute_final_review.py
"""
Description: 법령 근거 재판정 초안에서 사람이 최종 승인할 질문, 정답 청크,
다수결 충돌 항목을 추출해 검수 패키지를 생성한다.
Author: ooheunsu
Date: 2026-09-03
Before:
    - 잠정 다수결 데이터셋과 법령 근거 재판정 데이터셋이 준비된 상태.
After:
    - 11문항 최종 승인에 필요한 축약 JSON 검수 패키지가 생성.
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
    load_chunks,
    read_jsonl,
)


EVALUATION_ROOT = PROJECT_ROOT / "data/statutes/evaluation"
DEFAULT_MAJORITY = EVALUATION_ROOT / "datasets/pilot_v01_provisional.jsonl"
DEFAULT_EVIDENCE = (
    EVALUATION_ROOT / "datasets/pilot_v01_evidence_rechecked.jsonl"
)
DEFAULT_CHUNK_ROOT = PROJECT_ROOT / "data/statutes/chunks"
DEFAULT_OUTPUT = (
    EVALUATION_ROOT / "reviews/pilot_v01_final_human_review_draft.json"
)
POSITIVE_THRESHOLD = 2


def judgment_lookup(cases: list[dict[str, Any]]) -> dict[tuple[str, str], dict]:
    lookup = {}
    for case in cases:
        for judgment in case["judgments"]:
            key = (case["query_id"], judgment["chunk_id"])
            if key in lookup:
                raise ValueError(f"중복 질문-청크 판정: {key}")
            lookup[key] = judgment
    return lookup


def chunk_details(chunk: dict[str, Any]) -> dict[str, Any]:
    metadata = chunk["metadata"]
    return {
        "law_name": metadata["law_name"],
        "article_label": metadata["article_label"],
        "article_title": metadata["article_title"],
        "retrieval_text": chunk["retrieval_text"],
    }


def build_review_package(
    majority_cases: list[dict[str, Any]],
    evidence_cases: list[dict[str, Any]],
    chunks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if [case["query_id"] for case in majority_cases] != [
        case["query_id"] for case in evidence_cases
    ]:
        raise ValueError("다수결과 법령 근거 재판정의 질문 순서가 다릅니다.")

    majority = judgment_lookup(majority_cases)
    evidence = judgment_lookup(evidence_cases)
    if set(majority) != set(evidence):
        raise ValueError("두 데이터셋의 질문-청크 집합이 다릅니다.")

    question_rows = []
    positive_rows = []
    changed_rows = []
    score_counts: Counter[int] = Counter()

    for case in evidence_cases:
        positives = [
            judgment
            for judgment in case["judgments"]
            if judgment["relevance"] >= POSITIVE_THRESHOLD
        ]
        cores = [row for row in positives if row["relevance"] == 3]
        if not positives:
            raise ValueError(f"정답 청크가 없는 질문: {case['query_id']}")

        question_rows.append(
            {
                "query_id": case["query_id"],
                "question": case["question"],
                "purpose": case["purpose"],
                "category": case["category"],
                "legal_domain": case["legal_domain"],
                "difficulty": case["difficulty"],
                "critical": case["critical"],
                "required_concepts": case["required_concepts"],
                "positive_count": len(positives),
                "core_count": len(cores),
                "needs_core_review": not cores,
            }
        )

        for judgment in case["judgments"]:
            score_counts[judgment["relevance"]] += 1
            key = (case["query_id"], judgment["chunk_id"])
            if judgment["chunk_id"] not in chunks:
                raise ValueError(f"코퍼스에 없는 청크: {judgment['chunk_id']}")
            details = chunk_details(chunks[judgment["chunk_id"]])
            if judgment["relevance"] >= POSITIVE_THRESHOLD:
                positive_rows.append(
                    {
                        "query_id": case["query_id"],
                        "question": case["question"],
                        "chunk_id": judgment["chunk_id"],
                        "evidence_relevance": judgment["relevance"],
                        "evidence_reason": judgment["reason"],
                        **details,
                    }
                )

            majority_score = majority[key]["relevance"]
            evidence_score = judgment["relevance"]
            if (majority_score >= POSITIVE_THRESHOLD) != (
                evidence_score >= POSITIVE_THRESHOLD
            ):
                changed_rows.append(
                    {
                        "query_id": case["query_id"],
                        "question": case["question"],
                        "chunk_id": judgment["chunk_id"],
                        "majority_relevance": majority_score,
                        "evidence_relevance": evidence_score,
                        "evidence_reason": judgment["reason"],
                        "priority": "high" if majority_score == 3 else "normal",
                        **details,
                    }
                )

    return {
        "schema_version": "0.1",
        "status": "human_approval_pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label_policy": {
            "draft_source": "AI evidence recheck",
            "positive_threshold": POSITIVE_THRESHOLD,
            "human_approval_required": True,
            "score_definitions": {
                "0": "질문 해결에 쓰이지 않거나 혼동을 일으키는 조문",
                "1": "같은 주제지만 필요한 법적 요건·효과·절차를 제공하지 않는 조문",
                "2": "정확한 답변에 필요한 일부·보조 근거",
                "3": "핵심 요건·효과·절차를 직접 설명하는 근거",
            },
        },
        "summary": {
            "question_count": len(question_rows),
            "judgment_count": len(evidence),
            "positive_count": len(positive_rows),
            "changed_binary_label_count": len(changed_rows),
            "score_counts": {str(score): score_counts[score] for score in range(4)},
            "question_without_core_count": sum(
                row["needs_core_review"] for row in question_rows
            ),
        },
        "questions": question_rows,
        "positive_judgments": positive_rows,
        "changed_binary_labels": changed_rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="11문항 법령 검색 평가셋의 사람 최종 검수 초안을 만듭니다."
    )
    parser.add_argument("--majority", type=Path, default=DEFAULT_MAJORITY)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--chunk-root", type=Path, default=DEFAULT_CHUNK_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    package = build_review_package(
        read_jsonl(args.majority.resolve()),
        read_jsonl(args.evidence.resolve()),
        load_chunks(args.chunk_root.resolve()),
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(package, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "최종 사람 검수 초안 생성: "
        f"질문={package['summary']['question_count']}, "
        f"정답={package['summary']['positive_count']}, "
        f"정답여부변경={package['summary']['changed_binary_label_count']}"
    )


if __name__ == "__main__":
    main()
