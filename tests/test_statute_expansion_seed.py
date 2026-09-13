# test_statute_expansion_seed.py
"""
Description: 법령 검색 평가셋 신규 39문항의 배분과 시드 근거를 검증한다.
Author: ooheunsu
Date: 2026-09-04
Before:
    - 확장 계획과 Q12~Q50 시드 평가 데이터가 작성된 상태.

After:
    - 문항 수, 유형·난이도·영역 분포와 시드 청크 무결성이 검증됨.
"""

import json
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = ROOT / "data" / "statutes" / "evaluation"
SEED_DATASET = EVALUATION_ROOT / "datasets" / "expansion_v01_seed.jsonl"
RECHECKED_DATASET = (
    EVALUATION_ROOT / "datasets" / "expansion_v01_evidence_rechecked.jsonl"
)
COMBINED_DATASET = (
    EVALUATION_ROOT / "datasets" / "statute_retrieval_v01_50_draft.jsonl"
)
RECHECK_SPEC = EVALUATION_ROOT / "expansion_seed_ai_recheck_v01.json"
EXPANSION_PLAN = EVALUATION_ROOT / "expansion_plan_v01.json"
QUESTION_REVIEW = EVALUATION_ROOT / "approvals" / "expansion_v01_question_review.json"
CHUNK_ROOT = ROOT / "data" / "statutes" / "chunks"


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _load_chunk_ids() -> set[str]:
    chunk_ids: set[str] = set()
    for path in CHUNK_ROOT.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        chunk_ids.update(chunk["chunk_id"] for chunk in payload["chunks"])
    return chunk_ids


def test_expansion_seed_matches_approved_distribution() -> None:
    cases = _load_jsonl(SEED_DATASET)
    plan = json.loads(EXPANSION_PLAN.read_text(encoding="utf-8"))
    slots = {slot["query_id"]: slot for slot in plan["slots"]}

    assert len(cases) == 39
    assert [case["query_id"] for case in cases] == [
        f"statute_retrieval_v01_q{number:03d}" for number in range(12, 51)
    ]
    assert set(slots) == {case["query_id"] for case in cases}

    for case in cases:
        slot = slots[case["query_id"]]
        assert case["category"] == slot["category"]
        assert case["difficulty"] == slot["difficulty"]
        assert case["legal_domain"] == slot["legal_domain"]
        assert case["critical"] is slot["critical"]

    for key in ("category", "difficulty", "legal_domain"):
        assert Counter(case[key] for case in cases) == plan["new_targets"][key]
    assert sum(case["critical"] for case in cases) == plan["new_targets"]["critical"]


def test_expansion_seed_matches_case_schema() -> None:
    schema = json.loads((EVALUATION_ROOT / "schema_v01.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    for case in _load_jsonl(SEED_DATASET):
        assert not list(validator.iter_errors(case)), case["query_id"]


def test_expansion_seed_uses_existing_positive_chunks_and_stays_draft() -> None:
    cases = _load_jsonl(SEED_DATASET)
    chunk_ids = _load_chunk_ids()

    for case in cases:
        assert case["review"]["status"] == "draft"
        assert case["review"]["reviewed_by"] is None
        assert case["source"]["synthetic"] is True
        assert 1 <= len(case["required_concepts"]) <= 5
        assert case["judgments"]
        assert all(judgment["relevance"] >= 2 for judgment in case["judgments"])
        assert all(judgment["chunk_id"] in chunk_ids for judgment in case["judgments"])


def test_expansion_seed_does_not_duplicate_questions() -> None:
    expansion = _load_jsonl(SEED_DATASET)
    approved = _load_jsonl(EVALUATION_ROOT / "datasets" / "pilot_v01_approved.jsonl")
    questions = [case["question"] for case in approved + expansion]

    assert len(questions) == 50
    assert len(set(questions)) == 50


def test_expansion_question_review_covers_all_new_questions() -> None:
    review = json.loads(QUESTION_REVIEW.read_text(encoding="utf-8"))
    cases = _load_jsonl(SEED_DATASET)
    expected_ids = {case["query_id"] for case in cases}
    approved_ids = set(review["approved_without_change"])
    revised_ids = {item["query_id"] for item in review["resolved_changes"]}

    assert review["review_scope"] == "question_naturalness_only"
    assert review["submitted_summary"] == {
        "total": 39,
        "approved": 30,
        "revision_requested": 8,
        "excluded": 1,
    }
    assert len(approved_ids) == 30
    assert len(revised_ids) == 9
    assert not approved_ids & revised_ids
    assert approved_ids | revised_ids == expected_ids


def test_expansion_evidence_recheck_applies_all_decisions() -> None:
    source = _load_jsonl(SEED_DATASET)
    rechecked = _load_jsonl(RECHECKED_DATASET)
    spec = json.loads(RECHECK_SPEC.read_text(encoding="utf-8"))
    source_scores = {
        (case["query_id"], judgment["chunk_id"]): judgment["relevance"]
        for case in source
        for judgment in case["judgments"]
    }
    rechecked_scores = {
        (case["query_id"], judgment["chunk_id"]): judgment["relevance"]
        for case in rechecked
        for judgment in case["judgments"]
    }

    assert len(source_scores) == 74
    assert len(rechecked_scores) == 79
    assert sum(score >= 2 for score in rechecked_scores.values()) == 72
    assert Counter(rechecked_scores.values()) == {1: 7, 2: 17, 3: 55}
    for item in spec["score_overrides"]:
        key = (item["query_id"], item["chunk_id"])
        assert source_scores[key] == item["from"]
        assert rechecked_scores[key] == item["to"]
    for item in spec["missing_seed_additions"]:
        key = (item["query_id"], item["chunk_id"])
        assert key not in source_scores
        assert rechecked_scores[key] == item["relevance"]


def test_expansion_evidence_recheck_is_valid_draft() -> None:
    schema = json.loads((EVALUATION_ROOT / "schema_v01.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    chunk_ids = _load_chunk_ids()

    for case in _load_jsonl(RECHECKED_DATASET):
        assert not list(validator.iter_errors(case)), case["query_id"]
        assert case["review"]["status"] == "draft"
        assert case["review"]["reviewed_by"].startswith("Codex AI evidence recheck")
        assert all(judgment["chunk_id"] in chunk_ids for judgment in case["judgments"])


def test_combined_dataset_exactly_joins_approved_pilot_and_expansion() -> None:
    approved = _load_jsonl(EVALUATION_ROOT / "datasets" / "pilot_v01_approved.jsonl")
    expansion = _load_jsonl(RECHECKED_DATASET)
    combined = _load_jsonl(COMBINED_DATASET)

    assert combined == approved + expansion
    assert [case["query_id"] for case in combined] == [
        f"statute_retrieval_v01_q{number:03d}" for number in range(1, 51)
    ]
    assert Counter(case["review"]["status"] for case in combined) == {
        "approved": 11,
        "draft": 39,
    }
