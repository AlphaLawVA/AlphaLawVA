# apply_statute_expansion_evidence_recheck.py
"""
Description: 신규 법령 검색 평가셋의 AI 근거 재검토 판정을 적용한다.
Author: ooheunsu
Date: 2026-09-04
Before:
    - Q12~Q50 시드 질문과 법령 근거 및 AI 재검토 명세가 준비된 상태.

After:
    - 점수 조정과 누락 근거가 반영된 재검토 데이터셋이 생성.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_ROOT = PROJECT_ROOT / "data" / "statutes" / "evaluation"
DEFAULT_SOURCE = EVALUATION_ROOT / "datasets" / "expansion_v01_seed.jsonl"
DEFAULT_REVIEW = EVALUATION_ROOT / "expansion_seed_ai_recheck_v01.json"
DEFAULT_OUTPUT = (
    EVALUATION_ROOT / "datasets" / "expansion_v01_evidence_rechecked.jsonl"
)
CHUNK_ROOT = PROJECT_ROOT / "data" / "statutes" / "chunks"
POSITIVE_THRESHOLD = 2


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_chunk_ids(root: Path) -> set[str]:
    chunk_ids: set[str] = set()
    for path in root.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        chunk_ids.update(chunk["chunk_id"] for chunk in payload["chunks"])
    return chunk_ids


def apply_recheck(
    cases: list[dict[str, Any]],
    review: dict[str, Any],
    chunk_ids: set[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    source_scores = {
        (case["query_id"], judgment["chunk_id"]): judgment["relevance"]
        for case in cases
        for judgment in case["judgments"]
    }
    overrides = {
        (item["query_id"], item["chunk_id"]): item
        for item in review["score_overrides"]
    }
    additions: dict[str, list[dict[str, Any]]] = {}
    for item in review["missing_seed_additions"]:
        additions.setdefault(item["query_id"], []).append(item)

    if len(source_scores) != review["method"]["reviewed_candidate_count"]:
        raise ValueError("재검토 대상 수가 명세와 다릅니다.")
    if set(overrides) - set(source_scores):
        raise ValueError("원본 시드에 없는 점수 조정 항목이 있습니다.")
    for key, item in overrides.items():
        if source_scores[key] != item["from"]:
            raise ValueError(f"기존 점수가 재검토 명세와 다릅니다: {key}")
        if item["chunk_id"] not in chunk_ids:
            raise ValueError(f"존재하지 않는 청크입니다: {item['chunk_id']}")

    output = []
    changed_to_negative = 0
    for case in cases:
        revised = dict(case)
        judgments = []
        seen = set()
        for judgment in case["judgments"]:
            key = (case["query_id"], judgment["chunk_id"])
            seen.add(judgment["chunk_id"])
            item = overrides.get(key)
            updated = dict(judgment)
            if item:
                updated["relevance"] = item["to"]
                updated["reason"] = f"AI 법령 근거 재검토: {item['reason']}"
                if item["from"] >= POSITIVE_THRESHOLD > item["to"]:
                    changed_to_negative += 1
            else:
                updated["reason"] = (
                    "AI 법령 근거 재검토 유지: 법령 원문과 질문을 다시 비교한 결과 "
                    + judgment["reason"]
                )
            judgments.append(updated)

        for item in additions.get(case["query_id"], []):
            if item["chunk_id"] in seen:
                raise ValueError(f"중복 추가 청크입니다: {item['chunk_id']}")
            if item["chunk_id"] not in chunk_ids:
                raise ValueError(f"존재하지 않는 추가 청크입니다: {item['chunk_id']}")
            judgments.append(
                {
                    "chunk_id": item["chunk_id"],
                    "relevance": item["relevance"],
                    "reason": f"AI 누락 근거 검사 추가: {item['reason']}",
                }
            )

        revised["judgments"] = judgments
        revised["review"] = {
            **case["review"],
            "status": "draft",
            "reviewed_by": "Codex AI evidence recheck; human approval pending",
            "updated_at": review["reviewed_at"],
        }
        output.append(revised)

    counts = Counter(
        judgment["relevance"]
        for case in output
        for judgment in case["judgments"]
    )
    summary = {
        "reviewed_candidate_count": len(source_scores),
        "unchanged_count": len(source_scores) - len(overrides),
        "changed_count": len(overrides),
        "changed_to_below_positive_threshold_count": changed_to_negative,
        "missing_seed_addition_count": sum(map(len, additions.values())),
        "rechecked_judgment_count": sum(len(case["judgments"]) for case in output),
        "rechecked_positive_count": sum(
            judgment["relevance"] >= POSITIVE_THRESHOLD
            for case in output
            for judgment in case["judgments"]
        ),
        "relevance_0_count": counts[0],
        "relevance_1_count": counts[1],
        "relevance_2_count": counts[2],
        "relevance_3_count": counts[3],
    }
    for key, value in review["expected_result"].items():
        if summary[key] != value:
            raise ValueError(
                f"재검토 결과가 예상과 다릅니다: {key}={summary[key]} != {value}"
            )
    return output, summary


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def main() -> None:
    review = json.loads(DEFAULT_REVIEW.read_text(encoding="utf-8"))
    output, summary = apply_recheck(
        read_jsonl(DEFAULT_SOURCE),
        review,
        load_chunk_ids(CHUNK_ROOT),
    )
    write_jsonl(DEFAULT_OUTPUT, output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
