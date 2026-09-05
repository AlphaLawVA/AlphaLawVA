# review_statute_retrieval_calibrated.py
"""
Description: 승인된 11문항의 1점·2점 경계 사례에서 도출한 기준으로 법령
후보를 독립 재판정하고 문항별 체크포인트와 사용량을 기록한다.
Author: ooheunsu
Date: 2026-09-04
Before:
    - 블라인드 후보 풀, 1차 AI 판정, 현재 평가 데이터셋이 존재.

After:
    - 경계·양성 후보의 교정 AI 판정 JSONL과 실행 명세가 생성.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ml.data_collection.statutes.law_api_common import PROJECT_ROOT
from ml.evaluation.review_statute_retrieval_pool import (
    QuestionAssessment,
    file_sha256,
    load_openai_client,
    read_jsonl,
    validate_assessment,
    write_jsonl,
)


EVALUATION_ROOT = PROJECT_ROOT / "data/statutes/evaluation"
DEFAULT_DATASET = (
    EVALUATION_ROOT / "datasets/statute_retrieval_v01_50_draft.jsonl"
)
DEFAULT_BLIND_POOL = (
    EVALUATION_ROOT
    / "reviews/statute_retrieval_v01_50_draft_"
    "statute-50-v01-initial_blind.jsonl"
)
DEFAULT_FIRST_REVIEW = (
    EVALUATION_ROOT
    / "reviews/statute_retrieval_v01_50_draft_"
    "statute-50-v01-initial_ai_review.jsonl"
)
DEFAULT_HUMAN_TARGETS = (
    EVALUATION_ROOT
    / "reviews/statute_retrieval_v01_50_draft_"
    "statute-50-v01-initial_human_targets.jsonl"
)
DEFAULT_MODEL = "gpt-5.4"
PROMPT_VERSION = "statute-relevance-pilot-calibrated-v0.2"
DEFAULT_ATTEMPTS = 3


CALIBRATED_SYSTEM_INSTRUCTIONS = """\
당신은 법령 검색 평가셋의 독립 관련도 검수자다. 질문과 제공된 법령 청크
본문만으로 각 후보의 관련도 0~3점을 판정한다. 외부 지식으로 청크에 없는
내용을 보충하지 않는다. 이전 AI 점수나 검색 모델·순위는 제공되지 않는다.

관련도 기준:
- 3 핵심 근거: 질문의 핵심 요건, 기간, 효과 또는 절차에 직접 답한다.
- 2 필수 보충: 완전한 답은 아니지만 정확한 법적 설명에 필요한 요건,
  예외, 효과, 적용 범위 또는 절차 일부를 실제로 제공한다.
- 1 참고 관련: 같은 주제나 용어를 다루지만 답에 없어도 중요한 법적
  내용이 빠지지 않는다.
- 0 무관: 질문 해결에 쓰이지 않거나 근거로 쓰면 혼동을 일으킨다.

승인된 11문항에서 확정한 1점·2점 경계 교정:
1. 질문의 직접 결론이 없어도 필요한 요건 하나, 배당요구 같은 후속 절차,
   갱신·해지의 일반 구조를 제공하면 2점이 될 수 있다.
2. 적용 대상이 더 좁은 특별법이라도 질문이 묻는 제한·요건을 직접
   규정하고 답에서 적용 범위를 구별해 설명할 가치가 있으면 2점이다.
3. 다른 절차·특별법을 언급한다는 이유만으로 2점을 주지는 않는다.
   질문에 필요한 독립적인 법적 내용이 실제로 있어야 한다.
4. 위반 후 반환청구처럼 주변 효과만 있고 질문이 묻는 제한·요건·절차를
   제공하지 않으면 1점이다.
5. 2점과 3점의 차이는 보조 근거와 직접 핵심 근거의 차이다. 2점을
   과도하게 1점으로 낮추지 말고, 3점을 넓게 부여하지 않는다.

판정 규칙:
1. 후보마다 빠짐없이 판정하고 candidate_id를 입력 그대로 유지한다.
2. confidence는 high, medium, low 중 하나다.
3. reason에는 질문에 필요한 어떤 법적 내용이 있거나 없는지 적는다.
4. evidence_excerpt는 짧은 원문 구절만 적고 근거가 없으면 빈 문자열이다.
5. 긴 chunk_id는 출력하지 않는다.
"""


def _query_number(query_id: str) -> int:
    try:
        return int(query_id.rsplit("q", 1)[1])
    except (IndexError, ValueError) as error:
        raise ValueError(f"query_id 번호를 읽을 수 없습니다: {query_id}") from error


def select_review_cases(
    dataset_rows: list[dict[str, Any]],
    blind_rows: list[dict[str, Any]],
    first_review_rows: list[dict[str, Any]],
    query_min: int,
    query_max: int,
) -> list[dict[str, Any]]:
    dataset = {row["query_id"]: row for row in dataset_rows}
    first = {
        (row["query_id"], item["candidate_id"]): item
        for row in first_review_rows
        for item in row["assessments"]
    }
    selected = []
    for case in blind_rows:
        number = _query_number(case["query_id"])
        if not query_min <= number <= query_max:
            continue
        if case["query_id"] not in dataset:
            raise ValueError(f"평가 문항이 없습니다: {case['query_id']}")
        seed_scores = {
            item["chunk_id"]: item["relevance"]
            for item in dataset[case["query_id"]].get("judgments", [])
        }
        candidates = []
        for candidate in case["candidates"]:
            key = (case["query_id"], candidate["candidate_id"])
            if key not in first:
                raise ValueError(f"1차 AI 판정이 없습니다: {key}")
            assessment = first[key]
            seed_score = seed_scores.get(candidate["chunk_id"])
            should_review = (
                assessment["relevance"] >= 1
                or assessment["confidence"] != "high"
                or (
                    seed_score is not None
                    and seed_score != assessment["relevance"]
                )
            )
            if should_review:
                candidates.append(dict(candidate))
        if candidates:
            selected.append(
                {
                    **{key: case[key] for key in (
                        "query_id",
                        "question",
                        "purpose",
                        "category",
                        "critical",
                    )},
                    "required_concepts": dataset[case["query_id"]][
                        "required_concepts"
                    ],
                    "candidates": candidates,
                }
            )
    return selected


def select_target_cases(
    dataset_rows: list[dict[str, Any]],
    target_rows: list[dict[str, Any]],
    query_min: int,
    query_max: int,
) -> list[dict[str, Any]]:
    dataset = {row["query_id"]: row for row in dataset_rows}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in target_rows:
        number = _query_number(row["query_id"])
        if query_min <= number <= query_max:
            grouped.setdefault(row["query_id"], []).append(row)

    selected = []
    candidate_fields = (
        "candidate_id",
        "chunk_id",
        "law_name",
        "article_label",
        "article_title",
        "retrieval_text",
    )
    for query_id in sorted(grouped, key=_query_number):
        if query_id not in dataset:
            raise ValueError(f"평가 문항이 없습니다: {query_id}")
        rows = grouped[query_id]
        candidate_ids = [row["candidate_id"] for row in rows]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError(f"중복 후보가 있습니다: {query_id}")
        case = dataset[query_id]
        selected.append(
            {
                **{
                    key: case[key]
                    for key in (
                        "query_id",
                        "question",
                        "purpose",
                        "category",
                        "critical",
                    )
                },
                "required_concepts": case["required_concepts"],
                "candidates": [
                    {key: row[key] for key in candidate_fields}
                    for row in rows
                ],
            }
        )
    return selected


def build_review_input(case: dict[str, Any]) -> str:
    payload = {
        "query_id": case["query_id"],
        "question": case["question"],
        "purpose": case["purpose"],
        "required_concepts": case["required_concepts"],
        "candidates": [
            {
                key: candidate[key]
                for key in (
                    "candidate_id",
                    "law_name",
                    "article_label",
                    "article_title",
                    "retrieval_text",
                )
            }
            for candidate in case["candidates"]
        ],
    }
    return (
        "아래 질문의 모든 후보 청크를 교정 기준에 따라 서로 독립적으로 "
        "판정하라.\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def review_case(
    client: Any,
    case: dict[str, Any],
    model: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    for attempt in range(1, DEFAULT_ATTEMPTS + 1):
        response = client.responses.parse(
            model=model,
            reasoning={"effort": "medium"},
            instructions=CALIBRATED_SYSTEM_INSTRUCTIONS,
            input=build_review_input(case),
            text_format=QuestionAssessment,
            max_output_tokens=16_000,
            store=False,
        )
        parsed = response.output_parsed
        if parsed is None:
            error: ValueError = ValueError(
                f"구조화된 응답이 없습니다: {case['query_id']}"
            )
        else:
            try:
                validate_assessment(case, parsed)
            except ValueError as validation_error:
                error = validation_error
            else:
                result = parsed.model_dump()
                chunk_ids = {
                    candidate["candidate_id"]: candidate["chunk_id"]
                    for candidate in case["candidates"]
                }
                for item in result["assessments"]:
                    item["chunk_id"] = chunk_ids[item["candidate_id"]]
                usage = (
                    response.usage.model_dump()
                    if response.usage is not None
                    else {}
                )
                usage["response_id"] = response.id
                return result, usage
        if attempt == DEFAULT_ATTEMPTS:
            raise error
        print(
            "교정 AI 응답 구조 재시도: "
            f"{case['query_id']} ({attempt}/{DEFAULT_ATTEMPTS})"
        )
    raise AssertionError("도달할 수 없는 검증 재시도 상태입니다.")


def run_review(
    cases: list[dict[str, Any]],
    output_path: Path,
    manifest_path: Path,
    model: str,
) -> list[dict[str, Any]]:
    completed_rows = read_jsonl(output_path) if output_path.exists() else []
    completed = {row["query_id"]: row for row in completed_rows}
    expected_ids = {case["query_id"] for case in cases}
    if set(completed) - expected_ids:
        raise ValueError("체크포인트에 실행 범위 밖 문항이 있습니다.")
    client = load_openai_client()
    for index, case in enumerate(cases, start=1):
        query_id = case["query_id"]
        if query_id in completed:
            validate_assessment(
                case,
                QuestionAssessment.model_validate(completed[query_id]),
            )
            print(f"교정 AI 검수 재사용: {index}/{len(cases)} {query_id}")
            continue
        assessment, usage = review_case(client, case, model)
        assessment["review_metadata"] = {
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "reasoning_effort": "medium",
            "reviewed_at": datetime.now(UTC).isoformat(),
            "usage": usage,
        }
        completed[query_id] = assessment
        ordered = [
            completed[row["query_id"]]
            for row in cases
            if row["query_id"] in completed
        ]
        write_jsonl(output_path, ordered)
        print(f"교정 AI 검수 완료: {index}/{len(cases)} {query_id}")
    ordered = [completed[case["query_id"]] for case in cases]
    manifest = {
        "schema_version": "0.1",
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "question_count": len(cases),
        "candidate_count": sum(len(case["candidates"]) for case in cases),
        "output_sha256": file_sha256(output_path),
        "completed_at": datetime.now(UTC).isoformat(),
        "request_usage": [
            {
                "query_id": row["query_id"],
                **row["review_metadata"]["usage"],
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--blind-pool", type=Path, default=DEFAULT_BLIND_POOL)
    parser.add_argument("--first-review", type=Path, default=DEFAULT_FIRST_REVIEW)
    parser.add_argument(
        "--targets",
        type=Path,
        help=(
            "지정하면 1차 판정 기반 자동 선별 대신 이 JSONL의 후보만 "
            "블라인드 재검수한다."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--query-min", type=int, default=1)
    parser.add_argument("--query-max", type=int, default=50)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset_rows = read_jsonl(args.dataset.resolve())
    if args.targets is not None:
        cases = select_target_cases(
            dataset_rows,
            read_jsonl(args.targets.resolve()),
            args.query_min,
            args.query_max,
        )
    else:
        cases = select_review_cases(
            dataset_rows,
            read_jsonl(args.blind_pool.resolve()),
            read_jsonl(args.first_review.resolve()),
            args.query_min,
            args.query_max,
        )
    rows = run_review(
        cases,
        args.output.resolve(),
        args.manifest.resolve(),
        args.model,
    )
    print(
        "교정 AI 검수 완료: "
        f"문항={len(rows)}, 후보={sum(len(row['assessments']) for row in rows)}"
    )


if __name__ == "__main__":
    main()
