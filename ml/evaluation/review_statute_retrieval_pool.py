"""Blind AI review and targeted cross-review sampling for statute retrieval."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BLIND_POOL = (
    PROJECT_ROOT
    / "data/statutes/evaluation/reviews/"
    "pilot_v01_pilot-v01-initial_blind.jsonl"
)
DEFAULT_AI_REVIEW = (
    PROJECT_ROOT
    / "data/statutes/evaluation/reviews/"
    "pilot_v01_pilot-v01-initial_ai_review.jsonl"
)
DEFAULT_MANIFEST = (
    PROJECT_ROOT
    / "data/statutes/evaluation/reviews/"
    "pilot_v01_pilot-v01-initial_ai_review_manifest.json"
)
DEFAULT_HUMAN_REVIEW = (
    PROJECT_ROOT
    / "data/statutes/evaluation/reviews/"
    "pilot_v01_pilot-v01-initial_human_review.jsonl"
)
DEFAULT_COMPARISON = (
    PROJECT_ROOT
    / "data/statutes/evaluation/reviews/"
    "pilot_v01_pilot-v01-initial_review_comparison.jsonl"
)
DEFAULT_TEAM_SELECTION = (
    PROJECT_ROOT
    / "data/statutes/evaluation/reviews/"
    "pilot_v01_pilot-v01-initial_team_selection.jsonl"
)
DEFAULT_MODEL = "gpt-5.4"
PROMPT_VERSION = "statute-relevance-v0.1"


class CandidateAssessment(BaseModel):
    candidate_id: str
    relevance: int = Field(ge=0, le=3)
    confidence: Literal["high", "medium", "low"]
    reason: str
    evidence_excerpt: str


class QuestionAssessment(BaseModel):
    query_id: str
    assessments: list[CandidateAssessment]


SYSTEM_INSTRUCTIONS = """\
당신은 법령 검색 평가셋의 독립 관련도 검수자다. 최종 법률 답변의 문체나
친절함이 아니라, 검색된 각 법령 청크가 사용자 질문에 답하는 데 얼마나
필요한지만 판정한다. 제공된 질문과 청크 본문만 사용하고 외부 지식으로
빈 내용을 보충하지 않는다.

관련도 기준:
- 3 핵심 근거: 청크가 질문의 핵심 요건, 기간, 효과 또는 절차에 직접 답한다.
- 2 부분·보조 근거: 완전한 답은 아니지만 정확한 답에 필요한 요건, 예외,
  효과 또는 절차 일부를 제공한다.
- 1 주제만 관련: 같은 법률 영역이나 용어를 다루지만 이 질문의 답에
  실질적으로 필요하지 않다.
- 0 무관: 질문의 답과 관계없거나, 답 근거로 쓰면 혼동을 일으킨다.

판정 규칙:
1. 문서에 명시된 내용만 근거로 삼는다.
2. 법령명이나 단어가 겹친다는 이유만으로 2 이상을 주지 않는다.
3. 2와 3은 '이 청크가 없으면 답의 핵심이 빠지는가'로 구분한다.
4. 해석이 필요하거나 청크가 중간에서 잘려 판단하기 어려우면 confidence를
   낮추고 reason에 그 이유를 쓴다.
5. evidence_excerpt는 판정을 뒷받침하는 짧은 원문 구절만 적는다. 근거가
   없으면 빈 문자열로 둔다.
6. 후보마다 빠짐없이 하나의 판정을 반환하고 candidate_id를 입력 그대로
   유지한다. 긴 chunk_id는 출력하지 않는다.
"""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"JSONL {line_number}행이 올바르지 않습니다.") from error
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_blind_pool(rows: list[dict[str, Any]]) -> None:
    query_ids: set[str] = set()
    candidate_pairs: set[tuple[str, str]] = set()
    for row in rows:
        query_id = str(row.get("query_id", ""))
        if not query_id or query_id in query_ids:
            raise ValueError(f"query_id가 비었거나 중복입니다: {query_id}")
        query_ids.add(query_id)
        candidates = row.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError(f"후보가 없습니다: {query_id}")
        for candidate in candidates:
            pair = (query_id, str(candidate.get("chunk_id", "")))
            if not pair[1] or pair in candidate_pairs:
                raise ValueError(f"후보 chunk_id가 비었거나 중복입니다: {pair}")
            candidate_pairs.add(pair)
            if candidate.get("relevance") is not None:
                raise ValueError("AI 검수 입력에 기존 관련도 라벨이 포함되어 있습니다.")


def build_review_input(case: dict[str, Any]) -> str:
    candidates = []
    for candidate in case["candidates"]:
        candidates.append(
            {
                "candidate_id": candidate["candidate_id"],
                "chunk_id": candidate["chunk_id"],
                "law_name": candidate["law_name"],
                "article_label": candidate["article_label"],
                "article_title": candidate["article_title"],
                "retrieval_text": candidate["retrieval_text"],
            }
        )
    payload = {
        "query_id": case["query_id"],
        "question": case["question"],
        "purpose": case["purpose"],
        "category": case["category"],
        "critical": case["critical"],
        "candidates": candidates,
    }
    return (
        "아래 질문의 모든 후보 청크를 서로 독립적으로 판정하라.\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def validate_assessment(
    case: dict[str, Any], assessment: QuestionAssessment
) -> None:
    if assessment.query_id != case["query_id"]:
        raise ValueError(
            f"query_id 불일치: {assessment.query_id} != {case['query_id']}"
        )
    expected = {candidate["candidate_id"] for candidate in case["candidates"]}
    actual = {candidate.candidate_id for candidate in assessment.assessments}
    if len(actual) != len(assessment.assessments):
        raise ValueError(f"AI 응답에 중복 후보가 있습니다: {case['query_id']}")
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"AI 응답 후보 불일치: {case['query_id']}, "
            f"missing={missing}, extra={extra}"
        )


def load_openai_client() -> Any:
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError as error:
        raise RuntimeError(
            "python-dotenv가 필요합니다. "
            "pip install -r requirements-openai-embedding.txt 를 실행하세요."
        ) from error
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(".env에 OPENAI_API_KEY를 설정하세요.")
    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError(
            "OpenAI SDK가 필요합니다. "
            "pip install -r requirements-openai-embedding.txt 를 실행하세요."
        ) from error
    return OpenAI(max_retries=6, timeout=300.0)


def review_case(client: Any, case: dict[str, Any], model: str) -> tuple[dict, dict]:
    response = client.responses.parse(
        model=model,
        reasoning={"effort": "medium"},
        instructions=SYSTEM_INSTRUCTIONS,
        input=build_review_input(case),
        text_format=QuestionAssessment,
        max_output_tokens=16_000,
        store=False,
    )
    parsed = response.output_parsed
    if parsed is None:
        raise ValueError(f"구조화된 응답이 없습니다: {case['query_id']}")
    validate_assessment(case, parsed)
    usage = response.usage.model_dump() if response.usage is not None else {}
    usage["response_id"] = response.id
    result = parsed.model_dump()
    chunk_ids = {
        candidate["candidate_id"]: candidate["chunk_id"]
        for candidate in case["candidates"]
    }
    for candidate in result["assessments"]:
        candidate["chunk_id"] = chunk_ids[candidate["candidate_id"]]
    return result, usage


def run_ai_review(
    blind_pool_path: Path,
    output_path: Path,
    manifest_path: Path,
    model: str = DEFAULT_MODEL,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    cases = read_jsonl(blind_pool_path)
    validate_blind_pool(cases)
    completed_rows = read_jsonl(output_path) if output_path.exists() else []
    completed = {row["query_id"]: row for row in completed_rows}
    unknown = set(completed) - {case["query_id"] for case in cases}
    if unknown:
        raise ValueError(f"기존 AI 검수 파일에 알 수 없는 query_id가 있습니다: {unknown}")

    api_client = client or load_openai_client()
    for index, case in enumerate(cases, start=1):
        query_id = case["query_id"]
        if query_id in completed:
            validate_assessment(case, QuestionAssessment.model_validate(completed[query_id]))
            print(f"AI 검수 재사용: {index}/{len(cases)} {query_id}")
            continue
        assessment, usage = review_case(api_client, case, model)
        assessment["review_metadata"] = {
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "reasoning_effort": "medium",
            "reviewed_at": datetime.now(UTC).isoformat(),
            "usage": usage,
        }
        completed[query_id] = assessment
        ordered = [completed[row["query_id"]] for row in cases if row["query_id"] in completed]
        write_jsonl(output_path, ordered)
        print(f"AI 검수 완료: {index}/{len(cases)} {query_id}")

    ordered = [completed[case["query_id"]] for case in cases]
    manifest = {
        "schema_version": "0.1",
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "reasoning_effort": "medium",
        "blind_pool_path": str(blind_pool_path),
        "blind_pool_sha256": file_sha256(blind_pool_path),
        "question_count": len(cases),
        "candidate_count": sum(len(case["candidates"]) for case in cases),
        "completed_at": datetime.now(UTC).isoformat(),
        "request_usage": [
            {
                "query_id": row["query_id"],
                **row.get("review_metadata", {}).get("usage", {}),
            }
            for row in ordered
        ],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ordered


def flatten_ai_reviews(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict]:
    flattened = {}
    for row in rows:
        for assessment in row["assessments"]:
            key = (row["query_id"], assessment["candidate_id"])
            if key in flattened:
                raise ValueError(f"중복 AI 판정입니다: {key}")
            flattened[key] = assessment
    return flattened


def compare_reviews(
    blind_pool: list[dict[str, Any]],
    human_rows: list[dict[str, Any]],
    ai_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    human = {(row["query_id"], row["candidate_id"]): row for row in human_rows}
    ai = flatten_ai_reviews(ai_rows)
    comparisons = []
    for case in blind_pool:
        for candidate in case["candidates"]:
            key = (case["query_id"], candidate["candidate_id"])
            if key not in human or key not in ai:
                raise ValueError(f"사람 또는 AI 판정이 없습니다: {key}")
            human_score = int(human[key]["relevance"])
            ai_score = int(ai[key]["relevance"])
            comparisons.append(
                {
                    "query_id": case["query_id"],
                    "question": case["question"],
                    "purpose": case["purpose"],
                    "category": case["category"],
                    "critical": bool(case["critical"]),
                    **{key: candidate[key] for key in (
                        "candidate_id",
                        "chunk_id",
                        "law_name",
                        "article_label",
                        "article_title",
                        "retrieval_text",
                    )},
                    "human_relevance": human_score,
                    "human_reason": human[key].get("reason", ""),
                    "ai_relevance": ai_score,
                    "ai_confidence": ai[key]["confidence"],
                    "ai_reason": ai[key]["reason"],
                    "ai_evidence_excerpt": ai[key]["evidence_excerpt"],
                    "exact_agreement": human_score == ai_score,
                    "binary_agreement": (human_score >= 2) == (ai_score >= 2),
                }
            )
    if len(comparisons) != len(human) or len(comparisons) != len(ai):
        raise ValueError("사람, AI, 원본 후보의 개수가 일치하지 않습니다.")
    return comparisons


def _sample_key(row: dict[str, Any], seed: str) -> str:
    value = f"{seed}\0{row['query_id']}\0{row['candidate_id']}"
    return hashlib.sha256(value.encode()).hexdigest()


def select_team_review(
    comparisons: list[dict[str, Any]],
    agreement_rate: float = 0.10,
    seed: str = "pilot-v01-team-review",
) -> list[dict[str, Any]]:
    if not 0 <= agreement_rate <= 1:
        raise ValueError("agreement_rate는 0과 1 사이여야 합니다.")
    reasons: dict[tuple[str, str], set[str]] = {}

    def add(row: dict[str, Any], reason: str) -> None:
        key = (row["query_id"], row["candidate_id"])
        reasons.setdefault(key, set()).add(reason)

    for row in comparisons:
        if not row["binary_agreement"]:
            add(row, "positive_threshold_disagreement")
        if row["ai_confidence"] == "low":
            add(row, "ai_low_confidence")

    agreements = [row for row in comparisons if row["exact_agreement"]]
    sample_count = round(len(agreements) * agreement_rate)
    for row in sorted(agreements, key=lambda item: _sample_key(item, seed))[:sample_count]:
        add(row, "agreement_random_sample")

    critical_query_ids = sorted(
        {row["query_id"] for row in comparisons if row["critical"]}
    )
    for query_id in critical_query_ids:
        query_agreements = [
            row
            for row in agreements
            if row["query_id"] == query_id
        ]
        for positive in (True, False):
            candidates = [
                row
                for row in query_agreements
                if (row["human_relevance"] >= 2) == positive
            ]
            if candidates:
                add(
                    min(candidates, key=lambda item: _sample_key(item, seed)),
                    "critical_query_control_sample",
                )

    by_key = {
        (row["query_id"], row["candidate_id"]): row for row in comparisons
    }
    selected = []
    for key in sorted(reasons, key=lambda item: (item[0], item[1])):
        row = dict(by_key[key])
        row["selection_reasons"] = sorted(reasons[key])
        selected.append(row)
    return selected


def write_comparison_outputs(
    blind_pool_path: Path,
    human_path: Path,
    ai_path: Path,
    comparison_path: Path,
    team_selection_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blind_pool = read_jsonl(blind_pool_path)
    human_rows = read_jsonl(human_path)
    ai_rows = read_jsonl(ai_path)
    comparisons = compare_reviews(blind_pool, human_rows, ai_rows)
    selected = select_team_review(comparisons)
    write_jsonl(comparison_path, comparisons)
    write_jsonl(team_selection_path, selected)
    return comparisons, selected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blind-pool", type=Path, default=DEFAULT_BLIND_POOL)
    parser.add_argument("--output", type=Path, default=DEFAULT_AI_REVIEW)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--human-review", type=Path, default=DEFAULT_HUMAN_REVIEW)
    parser.add_argument("--comparison", type=Path, default=DEFAULT_COMPARISON)
    parser.add_argument("--team-selection", type=Path, default=DEFAULT_TEAM_SELECTION)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    blind_pool = args.blind_pool.resolve()
    ai_output = args.output.resolve()
    run_ai_review(
        blind_pool,
        ai_output,
        args.manifest.resolve(),
        args.model,
    )
    comparisons, selected = write_comparison_outputs(
        blind_pool,
        args.human_review.resolve(),
        ai_output,
        args.comparison.resolve(),
        args.team_selection.resolve(),
    )
    exact = sum(row["exact_agreement"] for row in comparisons)
    binary = sum(row["binary_agreement"] for row in comparisons)
    print(
        "검수 비교 완료: "
        f"전체={len(comparisons)}, 정확 일치={exact}, "
        f"정답 기준 일치={binary}, 팀 검수={len(selected)}"
    )


if __name__ == "__main__":
    main()
