# test_finalize_statute_50_calibrated_review.py
"""
Description: 50문항 평가셋의 판정 우선순위와 최종 근거 재판정 반영을
검증한다.
Author: ooheunsu
Date: 2026-09-05
Before:
    - 평가셋 최종화 함수와 판정 이력 형식이 정의된 상태.

After:
    - 출처 우선순위, 품질 표본 통계와 최종 승인 동작이 검증됨.
"""

from ml.evaluation.finalize_statute_50_calibrated_review import (
    apply_evidence_adjudication,
    build_datasets,
    control_summary,
)


def _case(number, judgments):
    return {
        "query_id": f"statute_retrieval_v01_q{number:03d}",
        "question": "질문",
        "judgments": judgments,
        "review": {"status": "approved" if number <= 11 else "draft"},
    }


def test_build_datasets_applies_source_priority():
    draft = [_case(number, [{"chunk_id": f"seed-{number}", "relevance": 3}]) for number in range(1, 51)]
    blind = []
    first = []
    merged = []
    decisions = []
    for number in range(1, 51):
        query_id = f"statute_retrieval_v01_q{number:03d}"
        candidate = {"candidate_id": "C01", "chunk_id": f"seed-{number}"}
        blind.append({"query_id": query_id, "candidates": [candidate]})
        first.append({"query_id": query_id, "assessments": [{**candidate, "relevance": 2, "reason": "first"}]})
        if number >= 12:
            merged.append({"query_id": query_id, "candidate_id": "C01", "chunk_id": f"seed-{number}", "provisional_relevance": 2, "calibrated_reason": "second"})
    decisions.append({"query_id": "statute_retrieval_v01_q012", "candidate_id": "C01", "final_relevance": 3, "review_tier": "필수", "reason": ""})
    human = {"review_status": "completed", "decisions": decisions}

    hybrid, conservative, summary = build_datasets(draft, blind, first, merged, human)

    assert hybrid[11]["judgments"][0]["relevance"] == 3
    assert hybrid[11]["judgments"][0]["label_source"] == "human_review"
    assert hybrid[12]["judgments"][0]["label_source"] == "calibrated_ai"
    assert conservative[11]["judgments"][0]["relevance"] == 3
    assert summary["label_source_counts"]["human_review"] == 1


def test_control_summary_counts_exact_and_binary_agreement():
    human = {
        "decisions": [
            {"query_id": "q12", "candidate_id": "C01", "final_relevance": 2, "review_tier": "품질 표본"},
            {"query_id": "q12", "candidate_id": "C02", "final_relevance": 1, "review_tier": "품질 표본"},
        ]
    }
    merged = [
        {"query_id": "q12", "candidate_id": "C01", "provisional_relevance": 3},
        {"query_id": "q12", "candidate_id": "C02", "provisional_relevance": 1},
    ]

    summary = control_summary(human, merged)

    assert summary["exact_agreement_count"] == 1
    assert summary["binary_agreement_count"] == 2


def test_apply_evidence_adjudication_preserves_history_and_approves():
    cases = [
        _case(
            12,
            [
                {"chunk_id": "chunk-12", "relevance": 2},
                {"chunk_id": "core-12", "relevance": 3},
            ],
        )
    ]
    cases[0]["judgments"][0]["label_source"] = "human_review"
    adjudication = {
        "status": "approved",
        "reviewed_at": "2026-09-05",
        "decisions": [
            {
                "query_id": "statute_retrieval_v01_q012",
                "chunk_id": "chunk-12",
                "final_relevance": 1,
                "selected_basis": "ai_evidence_recheck",
                "reason": "질문에 직접 답하지 않는다.",
            }
        ],
    }

    approved, summary = apply_evidence_adjudication(cases, adjudication)

    judgment = approved[0]["judgments"][0]
    assert cases[0]["judgments"][0]["relevance"] == 2
    assert judgment["relevance"] == 1
    assert judgment["label_source"] == "evidence_adjudication_ai_evidence_recheck"
    assert approved[0]["review"]["status"] == "approved"
    assert summary["selected_basis_counts"] == {"ai_evidence_recheck": 1}
