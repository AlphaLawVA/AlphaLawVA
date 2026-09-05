# prepare_statute_gold_adjudication.py
"""
Description: 사람·AI·팀원 법령 관련도 판정을 병합하고 불일치와 누락
후보를 법령 근거로 재검토해 잠정 골드셋을 준비한다.
Author: ooheunsu
Date: 2026-09-02
Before:
    - 블라인드 후보와 세 검수자 판정 및 법령 청크가 준비된 상태.

After:
    - 잠정 골드셋과 재검토 결과 및 지표 계산 준비 보고서가 생성.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_ROOT = PROJECT_ROOT / "data/statutes/evaluation"
REVIEW_ROOT = EVALUATION_ROOT / "reviews"
DEFAULT_CASES = EVALUATION_ROOT / "datasets/pilot_v01.jsonl"
DEFAULT_BLIND_POOL = REVIEW_ROOT / "pilot_v01_pilot-v01-initial_blind.jsonl"
DEFAULT_HUMAN = REVIEW_ROOT / "pilot_v01_pilot-v01-initial_human_review.jsonl"
DEFAULT_AI = REVIEW_ROOT / "pilot_v01_pilot-v01-initial_ai_review.jsonl"
DEFAULT_TEAM = REVIEW_ROOT / "pilot_v01_pilot-v01-initial_team_review.jsonl"
DEFAULT_SELECTION = REVIEW_ROOT / "pilot_v01_pilot-v01-initial_team_selection.jsonl"
DEFAULT_MERGED = REVIEW_ROOT / "pilot_v01_pilot-v01-initial_merged.jsonl"
DEFAULT_AUDIT = REVIEW_ROOT / "pilot_v01_pilot-v01-initial_missing_audit.jsonl"
DEFAULT_TARGETS = REVIEW_ROOT / "pilot_v01_pilot-v01-initial_adjudication_targets.jsonl"
DEFAULT_DECISIONS = REVIEW_ROOT / "pilot_v01_pilot-v01-initial_adjudication_v02.jsonl"
DEFAULT_MANIFEST = (
    REVIEW_ROOT / "pilot_v01_pilot-v01-initial_adjudication_manifest_v02.json"
)
DEFAULT_PROVISIONAL = EVALUATION_ROOT / "datasets/pilot_v01_provisional.jsonl"
DEFAULT_RANKINGS = EVALUATION_ROOT / "runs/pilot-v01-initial/rankings.json"
DEFAULT_READINESS = REVIEW_ROOT / "pilot_v01_pilot-v01-initial_readiness.json"
DEFAULT_CHUNK_ROOT = PROJECT_ROOT / "data/statutes/chunks"
DEFAULT_MODEL = "gpt-5.5"
PROMPT_VERSION = "statute-gold-adjudication-v0.2"
TOKEN_PATTERN = re.compile(r"[가-힣A-Za-z0-9]+")


class CandidateDecision(BaseModel):
    candidate_id: str
    independent_relevance: int = Field(ge=0, le=3)
    relevance: int = Field(ge=0, le=3)
    majority_class_assessment: Literal[
        "supported", "unsupported", "not_applicable"
    ]
    confidence: Literal["high", "medium", "low"]
    reason: str
    evidence_excerpt: str


class QuestionDecision(BaseModel):
    query_id: str
    decisions: list[CandidateDecision]


ADJUDICATION_INSTRUCTIONS = """\
당신은 법령 검색 평가셋의 근거 중심 최종 재검토자다. 기존 검수자의 점수는
제공되지 않으며 질문과 실제 법령 청크만 보고 판단한다. 최종 법률 답변을
작성하지 말고, 각 청크가 질문에 답하는 데 얼마나 필요한지만 평가한다.

- 3 핵심 근거: 질문의 핵심 요건, 기간, 효과 또는 절차에 직접 답한다.
- 2 부분·보조 근거: 완전한 답은 아니지만 정확한 답에 필요한 요건, 예외,
  효과 또는 절차 일부를 제공한다.
- 1 주제만 관련: 같은 주제이지만 답에 실질적으로 필요하지 않거나 다른
  조문을 단순히 참조할 뿐 답 내용을 제공하지 않는다.
- 0 무관: 질문의 답과 관계없거나 답 근거로 쓰면 혼동을 일으킨다.

법령명이나 단어가 겹친다는 이유만으로 2 이상을 주지 않는다. 청크 안에
질문에 필요한 내용이 실제로 있어야 한다. 2와 3은 이 청크가 없으면 답의
핵심이 빠지는지로 구분한다. evidence_excerpt에는 판정을 뒷받침하는 짧은
원문만 적고 근거가 없으면 빈 문자열로 둔다. 후보 번호를 빠짐없이 그대로
반환한다.

각 후보는 다음 순서로 판정한다.
1. independent_relevance: allowed_relevance를 무시하고 법령 원문만으로 0~3을
   판정한다.
2. allowed_relevance가 [0, 1] 또는 [2, 3]이면 relevance는 그 목록 안에서
   잠정 세부 점수를 고른다. independent_relevance가 목록 안이면
   majority_class_assessment를 supported, 밖이면 unsupported로 둔다.
3. allowed_relevance가 [0, 1, 2, 3]이면 relevance는 independent_relevance와
   같게 하고 majority_class_assessment는 not_applicable로 둔다.

allowed_relevance는 사람·AI·팀원 다수결로 잠정 확정한 정답 여부를 보존하기
위한 제약이다. 독립 판정과 충돌하더라도 숨기지 말고 unsupported로 표시한다.
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
                raise ValueError(f"{path} {line_number}행 JSON 오류") from error
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


def flatten_ai(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict]:
    result = {}
    for row in rows:
        for decision in row["assessments"]:
            key = (row["query_id"], decision["candidate_id"])
            if key in result:
                raise ValueError(f"중복 AI 판정: {key}")
            result[key] = decision
    return result


def exact_majority(scores: list[int]) -> int | None:
    counts = Counter(scores)
    score, count = counts.most_common(1)[0]
    return score if count >= 2 else None


def merge_reviews(
    blind_pool: list[dict[str, Any]],
    human_rows: list[dict[str, Any]],
    ai_rows: list[dict[str, Any]],
    team_rows: list[dict[str, Any]],
    selection_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    human = {(row["query_id"], row["candidate_id"]): row for row in human_rows}
    ai = flatten_ai(ai_rows)
    team = {(row["query_id"], row["candidate_id"]): row for row in team_rows}
    selection = {
        (row["query_id"], row["candidate_id"]): row for row in selection_rows
    }
    merged = []
    for case in blind_pool:
        for candidate in case["candidates"]:
            key = (case["query_id"], candidate["candidate_id"])
            if key not in human or key not in ai:
                raise ValueError(f"사람 또는 AI 판정 누락: {key}")
            votes = {
                "human": int(human[key]["relevance"]),
                "ai": int(ai[key]["relevance"]),
            }
            if key in team:
                votes["team"] = int(team[key]["relevance"])
            binary_votes = [score >= 2 for score in votes.values()]
            if len(binary_votes) == 2 and binary_votes[0] != binary_votes[1]:
                raise ValueError(f"팀 검수가 필요한 정답 기준 충돌: {key}")
            provisional_binary = sum(binary_votes) > len(binary_votes) / 2
            provisional_grade = exact_majority(list(votes.values()))
            reasons = []
            if provisional_grade is None:
                reasons.append("no_exact_grade_majority")
            selected = selection.get(key, {})
            is_control = bool(
                set(selected.get("selection_reasons", []))
                & {"agreement_random_sample", "critical_query_control_sample"}
            )
            if (
                key in team
                and (votes["human"] >= 2) == (votes["ai"] >= 2)
                and (votes["team"] >= 2) != (votes["human"] >= 2)
                and is_control
            ):
                reasons.append("control_sample_binary_conflict")
            merged.append(
                {
                    "query_id": case["query_id"],
                    "question": case["question"],
                    "purpose": case["purpose"],
                    "category": case["category"],
                    "critical": case["critical"],
                    **{
                        field: candidate[field]
                        for field in (
                            "candidate_id",
                            "chunk_id",
                            "law_name",
                            "article_label",
                            "article_title",
                            "retrieval_text",
                        )
                    },
                    "votes": votes,
                    "provisional_binary_relevant": provisional_binary,
                    "provisional_grade": provisional_grade,
                    "adjudication_reasons": reasons,
                    "human_reason": human[key].get("reason", ""),
                    "ai_reason": ai[key].get("reason", ""),
                    "team_reason": team.get(key, {}).get("reason", ""),
                    "team_confidence": team.get(key, {}).get(
                        "confidence", "not_reviewed"
                    ),
                }
            )
    expected = sum(len(case["candidates"]) for case in blind_pool)
    if len(merged) != expected:
        raise ValueError(f"병합 후보 개수 불일치: {len(merged)} != {expected}")
    return merged


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text) if len(token) > 1]


def load_chunks(chunk_root: Path) -> dict[str, dict[str, Any]]:
    chunks = {}
    for path in sorted(chunk_root.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        for chunk in document["chunks"]:
            chunk_id = chunk["chunk_id"]
            if chunk_id in chunks:
                raise ValueError(f"중복 법령 청크: {chunk_id}")
            chunks[chunk_id] = chunk
    return chunks


def bm25_scores(
    query: str,
    documents: list[tuple[str, Counter[str], int, str]],
    document_frequency: Counter[str],
    average_length: float,
) -> list[tuple[float, str]]:
    query_tokens = Counter(tokenize(query))
    total_documents = len(documents)
    k1 = 1.5
    b = 0.75
    scores = []
    for chunk_id, frequencies, length, text in documents:
        score = 0.0
        for token, query_frequency in query_tokens.items():
            frequency = frequencies.get(token, 0)
            if not frequency:
                continue
            df = document_frequency[token]
            idf = math.log(1 + (total_documents - df + 0.5) / (df + 0.5))
            denominator = frequency + k1 * (
                1 - b + b * length / average_length
            )
            score += query_frequency * idf * frequency * (k1 + 1) / denominator
        scores.append((score, chunk_id))
    return sorted(scores, key=lambda item: (-item[0], item[1]))


def audit_missing_candidates(
    cases: list[dict[str, Any]],
    blind_pool: list[dict[str, Any]],
    chunks: dict[str, dict[str, Any]],
    bm25_top_n: int = 5,
    concept_top_n: int = 3,
) -> list[dict[str, Any]]:
    pooled = {
        case["query_id"]: {candidate["chunk_id"] for candidate in case["candidates"]}
        for case in blind_pool
    }
    documents = []
    document_frequency: Counter[str] = Counter()
    for chunk_id, chunk in chunks.items():
        text = chunk["retrieval_text"]
        frequencies = Counter(tokenize(text))
        documents.append((chunk_id, frequencies, sum(frequencies.values()), text))
        document_frequency.update(frequencies.keys())
    average_length = sum(row[2] for row in documents) / len(documents)

    audit_rows = []
    for case in cases:
        query_id = case["query_id"]
        candidates: dict[str, dict[str, Any]] = {}

        def add(chunk_id: str, source: str, lexical_score: float = 0.0) -> None:
            if chunk_id in pooled[query_id]:
                return
            chunk = chunks.get(chunk_id)
            if chunk is None:
                raise ValueError(f"법령 청크를 찾을 수 없습니다: {chunk_id}")
            if chunk_id not in candidates:
                metadata = chunk["metadata"]
                candidates[chunk_id] = {
                    "chunk_id": chunk_id,
                    "law_name": metadata["law_name"],
                    "article_label": metadata["article_label"],
                    "article_title": metadata["article_title"],
                    "retrieval_text": chunk["retrieval_text"],
                    "audit_sources": [],
                    "lexical_score": lexical_score,
                }
            candidates[chunk_id]["audit_sources"].append(source)
            candidates[chunk_id]["lexical_score"] = max(
                candidates[chunk_id]["lexical_score"], lexical_score
            )

        for judgment in case["judgments"]:
            add(judgment["chunk_id"], "seed_answer_missing")

        query = " ".join([case["question"], *case["required_concepts"]])
        ranked = bm25_scores(
            query, documents, document_frequency, average_length
        )
        unseen_ranked = [row for row in ranked if row[1] not in pooled[query_id]]
        for score, chunk_id in unseen_ranked[:bm25_top_n]:
            add(chunk_id, "bm25_top_unseen", score)

        concepts = [concept.lower() for concept in case["required_concepts"]]
        concept_matches = []
        lexical_lookup = {chunk_id: score for score, chunk_id in ranked}
        for chunk_id, chunk in chunks.items():
            if chunk_id in pooled[query_id]:
                continue
            text = chunk["retrieval_text"].lower()
            coverage = sum(concept in text for concept in concepts)
            if coverage >= 2:
                concept_matches.append(
                    (coverage, lexical_lookup.get(chunk_id, 0.0), chunk_id)
                )
        concept_matches.sort(key=lambda item: (-item[0], -item[1], item[2]))
        for coverage, score, chunk_id in concept_matches[:concept_top_n]:
            add(chunk_id, f"required_concept_match:{coverage}", score)

        ordered = sorted(
            candidates.values(),
            key=lambda row: (
                "seed_answer_missing" not in row["audit_sources"],
                -row["lexical_score"],
                row["chunk_id"],
            ),
        )
        for index, candidate in enumerate(ordered, start=1):
            candidate["candidate_id"] = f"A{index:02d}"
        audit_rows.append(
            {
                "query_id": query_id,
                "question": case["question"],
                "purpose": case["purpose"],
                "required_concepts": case["required_concepts"],
                "candidates": ordered,
            }
        )
    return audit_rows


def build_adjudication_targets(
    cases: list[dict[str, Any]],
    merged: list[dict[str, Any]],
    audit_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    case_lookup = {case["query_id"]: case for case in cases}
    targets_by_query: dict[str, list[dict[str, Any]]] = {
        case["query_id"]: [] for case in cases
    }
    for row in merged:
        if not row["adjudication_reasons"]:
            continue
        allowed_relevance = [0, 1, 2, 3]
        if "control_sample_binary_conflict" not in row["adjudication_reasons"]:
            allowed_relevance = (
                [2, 3] if row["provisional_binary_relevant"] else [0, 1]
            )
        targets_by_query[row["query_id"]].append(
            {
                **{
                    field: row[field]
                    for field in (
                        "candidate_id",
                        "chunk_id",
                        "law_name",
                        "article_label",
                        "article_title",
                        "retrieval_text",
                    )
                },
                "target_type": "pooled_grade_adjudication",
                "target_reasons": row["adjudication_reasons"],
                "allowed_relevance": allowed_relevance,
            }
        )
    for audit in audit_rows:
        for candidate in audit["candidates"]:
            targets_by_query[audit["query_id"]].append(
                {
                    **candidate,
                    "target_type": "missing_candidate_audit",
                    "target_reasons": candidate["audit_sources"],
                    "allowed_relevance": [0, 1, 2, 3],
                }
            )
    result = []
    for query_id, candidates in targets_by_query.items():
        case = case_lookup[query_id]
        result.append(
            {
                "query_id": query_id,
                "question": case["question"],
                "purpose": case["purpose"],
                "required_concepts": case["required_concepts"],
                "candidates": candidates,
            }
        )
    return result


def build_review_input(target: dict[str, Any]) -> str:
    payload = {
        "query_id": target["query_id"],
        "question": target["question"],
        "purpose": target["purpose"],
        "required_concepts": target["required_concepts"],
        "candidates": [
            {
                field: candidate[field]
                for field in (
                    "candidate_id",
                    "law_name",
                    "article_label",
                    "article_title",
                    "retrieval_text",
                    "allowed_relevance",
                )
            }
            for candidate in target["candidates"]
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def validate_decisions(target: dict[str, Any], decisions: QuestionDecision) -> None:
    if decisions.query_id != target["query_id"]:
        raise ValueError("재검토 응답 query_id 불일치")
    expected = {candidate["candidate_id"] for candidate in target["candidates"]}
    actual = {decision.candidate_id for decision in decisions.decisions}
    if len(actual) != len(decisions.decisions) or actual != expected:
        raise ValueError(
            f"재검토 후보 불일치: {target['query_id']}, "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    allowed = {
        candidate["candidate_id"]: candidate["allowed_relevance"]
        for candidate in target["candidates"]
    }
    for decision in decisions.decisions:
        if decision.relevance not in allowed[decision.candidate_id]:
            raise ValueError(
                f"허용 범위 밖 재검토 점수: {target['query_id']} "
                f"{decision.candidate_id}={decision.relevance}, "
                f"allowed={allowed[decision.candidate_id]}"
            )
        candidate_allowed = allowed[decision.candidate_id]
        if candidate_allowed == [0, 1, 2, 3]:
            if (
                decision.relevance != decision.independent_relevance
                or decision.majority_class_assessment != "not_applicable"
            ):
                raise ValueError(
                    f"독립 판정 규칙 위반: {target['query_id']} "
                    f"{decision.candidate_id}"
                )
            continue
        expected_assessment = (
            "supported"
            if decision.independent_relevance in candidate_allowed
            else "unsupported"
        )
        if decision.majority_class_assessment != expected_assessment:
            raise ValueError(
                f"다수결 방향 평가 불일치: {target['query_id']} "
                f"{decision.candidate_id}"
            )


def load_openai_client() -> Any:
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError as error:
        raise RuntimeError("python-dotenv가 필요합니다.") from error
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(".env에 OPENAI_API_KEY를 설정하세요.")
    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError("OpenAI SDK가 필요합니다.") from error
    return OpenAI(max_retries=6, timeout=300.0)


def run_adjudication(
    targets: list[dict[str, Any]],
    target_path: Path,
    output_path: Path,
    manifest_path: Path,
    model: str = DEFAULT_MODEL,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    completed_rows = read_jsonl(output_path) if output_path.exists() else []
    completed = {row["query_id"]: row for row in completed_rows}
    api_client = client or load_openai_client()
    for index, target in enumerate(targets, start=1):
        query_id = target["query_id"]
        if query_id in completed:
            parsed = QuestionDecision.model_validate(completed[query_id])
            validate_decisions(target, parsed)
            print(f"근거 재검토 재사용: {index}/{len(targets)} {query_id}")
            continue
        response = None
        last_error = None
        for attempt in range(1, 4):
            response = api_client.responses.parse(
                model=model,
                reasoning={"effort": "medium"},
                instructions=ADJUDICATION_INSTRUCTIONS,
                input=build_review_input(target),
                text_format=QuestionDecision,
                max_output_tokens=16_000,
                store=False,
            )
            if response.output_parsed is None:
                last_error = ValueError(
                    f"구조화 재검토 응답 없음: {query_id}"
                )
                continue
            try:
                validate_decisions(target, response.output_parsed)
                break
            except ValueError as error:
                last_error = error
                print(
                    f"재검토 응답 재시도: {query_id} "
                    f"{attempt}/3 ({error})"
                )
        else:
            raise ValueError(
                f"재검토 응답 3회 검증 실패: {query_id}"
            ) from last_error
        if response is None or response.output_parsed is None:
            raise AssertionError("검증된 재검토 응답이 없습니다.")
        result = response.output_parsed.model_dump()
        chunk_ids = {
            candidate["candidate_id"]: candidate["chunk_id"]
            for candidate in target["candidates"]
        }
        for decision in result["decisions"]:
            decision["chunk_id"] = chunk_ids[decision["candidate_id"]]
        result["review_metadata"] = {
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "reasoning_effort": "medium",
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "response_id": response.id,
            "usage": response.usage.model_dump() if response.usage else {},
        }
        completed[query_id] = result
        ordered = [completed[row["query_id"]] for row in targets if row["query_id"] in completed]
        write_jsonl(output_path, ordered)
        print(f"근거 재검토 완료: {index}/{len(targets)} {query_id}")

    ordered = [completed[target["query_id"]] for target in targets]
    manifest = {
        "schema_version": "0.1",
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "target_sha256": file_sha256(target_path),
        "question_count": len(targets),
        "candidate_count": sum(len(row["candidates"]) for row in targets),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "request_usage": [
            {
                "query_id": row["query_id"],
                **row.get("review_metadata", {}).get("usage", {}),
            }
            for row in ordered
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ordered


def finalize_provisional_dataset(
    cases: list[dict[str, Any]],
    merged: list[dict[str, Any]],
    audit_rows: list[dict[str, Any]],
    decision_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    decisions = {}
    for row in decision_rows:
        for decision in row["decisions"]:
            decisions[(row["query_id"], decision["chunk_id"])] = decision
    audit_lookup = {
        (row["query_id"], candidate["chunk_id"]): candidate
        for row in audit_rows
        for candidate in row["candidates"]
    }
    merged_by_query: dict[str, list[dict[str, Any]]] = {}
    for row in merged:
        merged_by_query.setdefault(row["query_id"], []).append(row)

    output = []
    low_confidence = []
    added_relevant = []
    majority_class_conflicts = []
    for case in cases:
        judgments = []
        for row in merged_by_query[case["query_id"]]:
            key = (case["query_id"], row["chunk_id"])
            decision = decisions.get(key)
            if row["adjudication_reasons"]:
                if decision is None:
                    raise ValueError(f"재검토 결과 누락: {key}")
                relevance = decision["relevance"]
                reason = f"근거 재검토: {decision['reason']}"
                if decision["confidence"] == "low":
                    low_confidence.append(key)
                if decision["majority_class_assessment"] == "unsupported":
                    majority_class_conflicts.append(
                        {
                            "query_id": key[0],
                            "chunk_id": key[1],
                            "majority_relevance": relevance,
                            "independent_relevance": decision[
                                "independent_relevance"
                            ],
                        }
                    )
            else:
                relevance = row["provisional_grade"]
                reason = "검수자 점수 다수 또는 사람·AI 일치"
            judgments.append(
                {
                    "chunk_id": row["chunk_id"],
                    "relevance": relevance,
                    "reason": reason,
                }
            )

        for key, candidate in audit_lookup.items():
            if key[0] != case["query_id"]:
                continue
            decision = decisions.get(key)
            if decision is None:
                raise ValueError(f"누락 검사 판정 없음: {key}")
            if decision["confidence"] == "low":
                low_confidence.append(key)
            if decision["relevance"] >= 1:
                judgments.append(
                    {
                        "chunk_id": key[1],
                        "relevance": decision["relevance"],
                        "reason": f"후보 밖 정답 검사: {decision['reason']}",
                    }
                )
                if decision["relevance"] >= 2:
                    added_relevant.append(
                        {
                            "query_id": key[0],
                            "chunk_id": key[1],
                            "relevance": decision["relevance"],
                            "sources": candidate["audit_sources"],
                        }
                    )
        judgments.sort(key=lambda item: item["chunk_id"])
        provisional = dict(case)
        provisional["judgments"] = judgments
        provisional["review"] = {
            **case["review"],
            "status": "draft",
            "reviewed_by": "annotator_a, annotator_b, AI evidence adjudication",
            "updated_at": datetime.now(timezone.utc).date().isoformat(),
        }
        output.append(provisional)
    summary = {
        "question_count": len(output),
        "judgment_count": sum(len(case["judgments"]) for case in output),
        "low_confidence": low_confidence,
        "majority_class_conflicts": majority_class_conflicts,
        "added_relevant": added_relevant,
    }
    return output, summary


def build_readiness_report(
    provisional: list[dict[str, Any]],
    merged: list[dict[str, Any]],
    audit_rows: list[dict[str, Any]],
    decision_rows: list[dict[str, Any]],
    summary: dict[str, Any],
    chunks: dict[str, dict[str, Any]],
    rankings_path: Path,
    provisional_path: Path,
) -> dict[str, Any]:
    query_ids = {case["query_id"] for case in provisional}
    positive_gold = {
        case["query_id"]: {
            judgment["chunk_id"]
            for judgment in case["judgments"]
            if judgment["relevance"] >= 2
        }
        for case in provisional
    }
    relevance_counts: Counter[int] = Counter()
    for case in provisional:
        seen = set()
        for judgment in case["judgments"]:
            chunk_id = judgment["chunk_id"]
            if chunk_id in seen:
                raise ValueError(
                    f"잠정 골드 중복 청크: {case['query_id']} {chunk_id}"
                )
            if chunk_id not in chunks:
                raise ValueError(f"잠정 골드 청크가 코퍼스에 없음: {chunk_id}")
            seen.add(chunk_id)
            relevance_counts[judgment["relevance"]] += 1
        if not positive_gold[case["query_id"]]:
            raise ValueError(f"관련 정답 청크가 없는 질문: {case['query_id']}")

    rankings = json.loads(rankings_path.read_text(encoding="utf-8"))
    ranking_models = []
    ranked_union = {query_id: set() for query_id in query_ids}
    for model_key, model_rankings in rankings.items():
        if set(model_rankings) != query_ids:
            raise ValueError(f"순위 질문 집합 불일치: {model_key}")
        result_counts = set()
        for query_id, rows in model_rankings.items():
            result_counts.add(len(rows))
            ranks = [row["rank"] for row in rows]
            if ranks != list(range(1, len(rows) + 1)):
                raise ValueError(f"순위 번호 오류: {model_key} {query_id}")
            chunk_ids = [row["chunk_id"] for row in rows]
            if len(set(chunk_ids)) != len(chunk_ids):
                raise ValueError(f"순위 중복 청크: {model_key} {query_id}")
            missing = [chunk_id for chunk_id in chunk_ids if chunk_id not in chunks]
            if missing:
                raise ValueError(
                    f"순위 청크가 코퍼스에 없음: {model_key} {missing}"
                )
            ranked_union[query_id].update(chunk_ids)
        ranking_models.append(
            {
                "model_key": model_key,
                "query_count": len(model_rankings),
                "result_counts": sorted(result_counts),
            }
        )

    decisions = {
        (row["query_id"], decision["chunk_id"]): decision
        for row in decision_rows
        for decision in row["decisions"]
    }
    control_samples = []
    for row in merged:
        if "control_sample_binary_conflict" not in row["adjudication_reasons"]:
            continue
        decision = decisions[(row["query_id"], row["chunk_id"])]
        control_samples.append(
            {
                "query_id": row["query_id"],
                "candidate_id": row["candidate_id"],
                "chunk_id": row["chunk_id"],
                "votes": row["votes"],
                "independent_relevance": decision["independent_relevance"],
                "confidence": decision["confidence"],
                "reason": decision["reason"],
                "evidence_excerpt": decision["evidence_excerpt"],
            }
        )

    missing_seed_answers = [
        {
            "query_id": row["query_id"],
            "chunk_id": candidate["chunk_id"],
        }
        for row in audit_rows
        for candidate in row["candidates"]
        if "seed_answer_missing" in candidate["audit_sources"]
    ]
    positive_not_in_original_rankings = [
        {"query_id": query_id, "chunk_id": chunk_id}
        for query_id, chunk_ids in positive_gold.items()
        for chunk_id in sorted(chunk_ids - ranked_union[query_id])
    ]
    blockers = summary["majority_class_conflicts"]
    return {
        "schema_version": "0.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "gold_dataset": str(provisional_path.relative_to(PROJECT_ROOT)),
        "gold_dataset_sha256": file_sha256(provisional_path),
        "status": {
            "provisional_metrics_ready": True,
            "final_metrics_ready": not blockers,
            "finalization_blocker_count": len(blockers),
        },
        "dataset": {
            "question_count": len(provisional),
            "judgment_count": sum(
                len(case["judgments"]) for case in provisional
            ),
            "relevance_counts": {
                str(score): relevance_counts[score] for score in range(4)
            },
            "positive_threshold": 2,
        },
        "adjudication": {
            "pooled_candidates": len(merged),
            "reviewed_target_count": sum(
                len(row["decisions"]) for row in decision_rows
            ),
            "low_confidence": summary["low_confidence"],
            "majority_class_conflicts": blockers,
            "control_sample_conflicts": control_samples,
        },
        "missing_candidate_audit": {
            "reviewed_candidate_count": sum(
                len(row["candidates"]) for row in audit_rows
            ),
            "missing_seed_answers": missing_seed_answers,
            "added_relevant": summary["added_relevant"],
            "positive_not_in_original_top_k": positive_not_in_original_rankings,
        },
        "rankings": {
            "path": str(rankings_path.relative_to(PROJECT_ROOT)),
            "sha256": file_sha256(rankings_path),
            "models": ranking_models,
            "integrity_checks_passed": True,
        },
        "next_step": (
            "Calculate provisional retrieval metrics. Resolve majority-class "
            "conflicts before publishing final metrics."
        ),
    }


def prepare_files(args: argparse.Namespace) -> tuple[list[dict], list[dict], list[dict]]:
    cases = read_jsonl(args.cases.resolve())
    blind_pool = read_jsonl(args.blind_pool.resolve())
    merged = merge_reviews(
        blind_pool,
        read_jsonl(args.human.resolve()),
        read_jsonl(args.ai.resolve()),
        read_jsonl(args.team.resolve()),
        read_jsonl(args.selection.resolve()),
    )
    audit = audit_missing_candidates(
        cases,
        blind_pool,
        load_chunks(args.chunk_root.resolve()),
    )
    targets = build_adjudication_targets(cases, merged, audit)
    write_jsonl(args.merged.resolve(), merged)
    write_jsonl(args.audit.resolve(), audit)
    write_jsonl(args.targets.resolve(), targets)
    print(
        "재검토 준비 완료: "
        f"병합={len(merged)}, "
        f"세부등급/통제={sum(bool(row['adjudication_reasons']) for row in merged)}, "
        f"누락검사={sum(len(row['candidates']) for row in audit)}, "
        f"전체재검토={sum(len(row['candidates']) for row in targets)}"
    )
    return cases, merged, audit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--blind-pool", type=Path, default=DEFAULT_BLIND_POOL)
    parser.add_argument("--human", type=Path, default=DEFAULT_HUMAN)
    parser.add_argument("--ai", type=Path, default=DEFAULT_AI)
    parser.add_argument("--team", type=Path, default=DEFAULT_TEAM)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--chunk-root", type=Path, default=DEFAULT_CHUNK_ROOT)
    parser.add_argument("--merged", type=Path, default=DEFAULT_MERGED)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--provisional", type=Path, default=DEFAULT_PROVISIONAL)
    parser.add_argument("--rankings", type=Path, default=DEFAULT_RANKINGS)
    parser.add_argument("--readiness", type=Path, default=DEFAULT_READINESS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prepare-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cases, merged, audit = prepare_files(args)
    if args.prepare_only:
        return
    targets = read_jsonl(args.targets.resolve())
    decisions = run_adjudication(
        targets,
        args.targets.resolve(),
        args.decisions.resolve(),
        args.manifest.resolve(),
        args.model,
    )
    provisional, summary = finalize_provisional_dataset(
        cases, merged, audit, decisions
    )
    write_jsonl(args.provisional.resolve(), provisional)
    readiness = build_readiness_report(
        provisional,
        merged,
        audit,
        decisions,
        summary,
        load_chunks(args.chunk_root.resolve()),
        args.rankings.resolve(),
        args.provisional.resolve(),
    )
    args.readiness.resolve().write_text(
        json.dumps(readiness, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
