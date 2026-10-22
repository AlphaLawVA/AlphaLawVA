# evaluate_outputs.py
"""
Description: 저장된 내용증명 API 응답을 버전 고정 합성 평가셋과 비교한다.
계약 사실, 법령 근거, 판례 분리와 실행 메타데이터를 오프라인으로 판정한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 평가셋과 생성 응답 JSONL, 실행 구성 JSON이 준비된 상태.
After:
    - 재현 가능한 case_results.jsonl, summary.json, manifest.json이 생성.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from api.models.certified_letters import (
    CertifiedLetterDraftJobResponse,
    CertifiedLetterDraftRequest,
    CertifiedLetterJobStatus,
    CertifiedLetterOutcome,
)
from ml.agent.certified_letter.normalizer import normalize_request
from ml.agent.certified_letter.state import (
    CertifiedLetterRequestData,
    ContractData,
    DraftPartyData,
    LetterDraftData,
    PartyData,
    PaymentData,
    PeriodData,
    PropertyData,
    StatuteEvidenceData,
)
from ml.agent.certified_letter.validators import (
    LAW_CITATION_PATTERN,
    CertifiedLetterDraftValidator,
)


EVALUATOR_VERSION = "certified_letter_output_eval_v01"
DATASET_VERSION = "certified_letter_eval_v01"
REQUIRED_RUN_METADATA = (
    "provider",
    "model",
    "prompt_version",
    "statute_embedding",
    "statute_chunking",
    "precedent_embedding",
    "precedent_chunking",
    "precedent_reranker",
)
FACT_ISSUE_CODES = {
    "sender_mismatch",
    "recipient_mismatch",
    "unexpected_money",
    "unexpected_date",
    "missing_deadline",
}
SAFETY_GATE_CODES = {
    "unsupported_statute_citation",
    "unexpected_money",
    "unexpected_date",
    "precedent_in_body",
    "internal_prompt_exposure",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExpectedResult(StrictModel):
    outcome: CertifiedLetterOutcome
    required_body_terms: list[str] = Field(default_factory=list)
    forbidden_output_terms: list[str] = Field(default_factory=list)
    max_model_calls: int = Field(ge=0)


class EvaluationCase(StrictModel):
    case_id: str = Field(min_length=1, max_length=100)
    dataset_version: str
    scenario: str = Field(min_length=1, max_length=300)
    is_synthetic: bool
    request: CertifiedLetterDraftRequest
    expected: ExpectedResult

    @model_validator(mode="after")
    def validate_version_and_synthetic_data(self) -> EvaluationCase:
        if self.dataset_version != DATASET_VERSION:
            raise ValueError(f"dataset_version은 {DATASET_VERSION}이어야 합니다.")
        if not self.is_synthetic:
            raise ValueError("저장소 평가셋에는 합성 사례만 허용됩니다.")
        return self


class RecordedAttempt(StrictModel):
    case_id: str = Field(min_length=1, max_length=100)
    response: dict[str, Any] | None = None
    latency_ms: float = Field(ge=0)
    model_calls: int = Field(ge=0)
    execution_error: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_result_presence(self) -> RecordedAttempt:
        if self.response is None and self.execution_error is None:
            raise ValueError("response 또는 execution_error 중 하나는 필요합니다.")
        return self


@dataclass(frozen=True)
class EvaluationArtifacts:
    case_results: list[dict[str, Any]]
    summary: dict[str, Any]
    manifest: dict[str, Any]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number} JSON 파싱 실패") from error
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} 객체가 필요합니다.")
        records.append(value)
    return records


def load_cases(path: Path) -> list[EvaluationCase]:
    cases = [EvaluationCase.model_validate(item) for item in read_jsonl(path)]
    _require_unique([item.case_id for item in cases], "평가셋 case_id")
    if not cases:
        raise ValueError("평가셋이 비어 있습니다.")
    return cases


def load_attempts(path: Path) -> list[RecordedAttempt]:
    attempts = [RecordedAttempt.model_validate(item) for item in read_jsonl(path)]
    _require_unique([item.case_id for item in attempts], "응답 case_id")
    return attempts


def load_run_metadata(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("실행 메타데이터는 JSON 객체여야 합니다.")
    missing = [
        key for key in REQUIRED_RUN_METADATA if not str(value.get(key, "")).strip()
    ]
    if missing:
        raise ValueError("실행 메타데이터 누락: " + ", ".join(missing))
    return value


def evaluate(
    *,
    cases: list[EvaluationCase],
    attempts: list[RecordedAttempt],
    run_metadata: dict[str, Any],
    dataset_sha256: str,
    attempts_sha256: str,
) -> EvaluationArtifacts:
    missing_metadata = [
        key for key in REQUIRED_RUN_METADATA if not str(run_metadata.get(key, "")).strip()
    ]
    if missing_metadata:
        raise ValueError("실행 메타데이터 누락: " + ", ".join(missing_metadata))

    attempt_by_id = {item.case_id: item for item in attempts}
    case_ids = {item.case_id for item in cases}
    missing_case_ids = sorted(case_ids - attempt_by_id.keys())
    unexpected_case_ids = sorted(attempt_by_id.keys() - case_ids)
    case_results = [
        _evaluate_case(case, attempt_by_id.get(case.case_id)) for case in cases
    ]
    summary = _make_summary(
        case_results,
        missing_case_ids=missing_case_ids,
        unexpected_case_ids=unexpected_case_ids,
    )
    manifest = {
        "schema_version": "certified_letter_evaluation_manifest.v1",
        "evaluator_version": EVALUATOR_VERSION,
        "dataset_version": DATASET_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset_sha256": dataset_sha256,
        "attempts_sha256": attempts_sha256,
        "case_count": len(cases),
        "attempt_count": len(attempts),
        "run_metadata": run_metadata,
        "overall_pass": summary["overall_pass"],
    }
    return EvaluationArtifacts(case_results, summary, manifest)


def _evaluate_case(
    case: EvaluationCase,
    attempt: RecordedAttempt | None,
) -> dict[str, Any]:
    if attempt is None:
        return {
            "case_id": case.case_id,
            "scenario": case.scenario,
            "passed": False,
            "failure_reasons": ["missing_attempt"],
            "expected_outcome": case.expected.outcome.value,
            "observed_outcome": None,
            "validation_issue_codes": [],
            "citation_count": 0,
            "supported_citation_count": 0,
            "latency_ms": None,
            "model_calls": None,
            "system_failure_disguised_as_success": False,
        }

    reasons = []
    issue_codes: list[str] = []
    observed_outcome = None
    citation_count = 0
    supported_citation_count = 0
    response = None
    response_schema_error = False
    if attempt.response is None:
        reasons.append("execution_error")
    else:
        try:
            response = CertifiedLetterDraftJobResponse.model_validate(attempt.response)
        except ValidationError:
            response_schema_error = True
            reasons.append("response_schema_invalid")

    disguised_failure = bool(
        attempt.execution_error
        and isinstance(attempt.response, dict)
        and attempt.response.get("status") == CertifiedLetterJobStatus.COMPLETED.value
    )
    if disguised_failure:
        reasons.append("system_failure_disguised_as_success")

    if response is not None:
        observed_outcome = response.outcome.value if response.outcome else None
        if response.status is not CertifiedLetterJobStatus.COMPLETED:
            reasons.append("job_not_completed")
        if response.outcome is not case.expected.outcome:
            reasons.append("outcome_mismatch")
        if response.outcome is CertifiedLetterOutcome.DRAFT_READY:
            assert response.result is not None
            normalized = normalize_request(_to_internal_request(case.request))
            statutes = _to_statute_evidence(response)
            draft = _to_draft(response)
            issues = CertifiedLetterDraftValidator().validate(
                normalized,
                statutes,
                draft,
            )
            issue_codes = [item.code for item in issues]
            if issue_codes:
                reasons.append("production_validation_failed")
            citation_count, supported_citation_count = _citation_counts(
                draft.body,
                statutes,
            )
            missing_terms = [
                term
                for term in case.expected.required_body_terms
                if term not in draft.body
            ]
            if missing_terms:
                reasons.append("required_body_term_missing")
        elif response.result is not None:
            reasons.append("unexpected_result")

        generated_text = _generated_text(response).lower()
        if any(
            term.lower() in generated_text
            for term in case.expected.forbidden_output_terms
        ):
            reasons.append("forbidden_output_term")

    if attempt.model_calls > case.expected.max_model_calls:
        reasons.append("model_call_limit_exceeded")
    if response_schema_error and attempt.execution_error:
        reasons.append("execution_and_schema_failure")

    reasons = list(dict.fromkeys(reasons))
    return {
        "case_id": case.case_id,
        "scenario": case.scenario,
        "passed": not reasons,
        "failure_reasons": reasons,
        "expected_outcome": case.expected.outcome.value,
        "observed_outcome": observed_outcome,
        "validation_issue_codes": issue_codes,
        "citation_count": citation_count,
        "supported_citation_count": supported_citation_count,
        "latency_ms": attempt.latency_ms,
        "model_calls": attempt.model_calls,
        "system_failure_disguised_as_success": disguised_failure,
    }


def _make_summary(
    results: list[dict[str, Any]],
    *,
    missing_case_ids: list[str],
    unexpected_case_ids: list[str],
) -> dict[str, Any]:
    evaluated = [item for item in results if item["latency_ms"] is not None]
    draft_results = [
        item for item in results if item["observed_outcome"] == "draft_ready"
    ]
    issue_codes = [
        code for item in results for code in item["validation_issue_codes"]
    ]
    citation_count = sum(item["citation_count"] for item in results)
    supported_count = sum(item["supported_citation_count"] for item in results)
    fact_consistent = sum(
        not (set(item["validation_issue_codes"]) & FACT_ISSUE_CODES)
        for item in draft_results
    )
    precedent_separated = sum(
        "precedent_in_body" not in item["validation_issue_codes"]
        for item in draft_results
    )
    outcome_matches = sum(
        item["expected_outcome"] == item["observed_outcome"] for item in results
    )
    latencies = [float(item["latency_ms"]) for item in evaluated]
    gates = {
        "zero_unsupported_statute_citations": (
            "unsupported_statute_citation" not in issue_codes
        ),
        "zero_invented_contract_amounts": "unexpected_money" not in issue_codes,
        "zero_invented_contract_dates": "unexpected_date" not in issue_codes,
        "zero_precedent_citations_in_body": "precedent_in_body" not in issue_codes,
        "zero_internal_prompt_exposure": "internal_prompt_exposure" not in issue_codes,
        "zero_system_failures_disguised_as_success": not any(
            item["system_failure_disguised_as_success"] for item in results
        ),
    }
    complete = not missing_case_ids and not unexpected_case_ids
    overall_pass = complete and all(item["passed"] for item in results) and all(
        gates.values()
    )
    return {
        "schema_version": "certified_letter_evaluation_summary.v1",
        "dataset_version": DATASET_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "case_count": len(results),
        "evaluated_count": len(evaluated),
        "passed_count": sum(item["passed"] for item in results),
        "missing_case_ids": missing_case_ids,
        "unexpected_case_ids": unexpected_case_ids,
        "metrics": {
            "outcome_accuracy": _ratio(outcome_matches, len(results)),
            "statute_citation_precision": _ratio(supported_count, citation_count),
            "contract_fact_consistency_rate": _ratio(
                fact_consistent,
                len(draft_results),
            ),
            "precedent_body_separation_rate": _ratio(
                precedent_separated,
                len(draft_results),
            ),
            "model_calls_total": sum(
                int(item["model_calls"] or 0) for item in results
            ),
            "latency_ms": _latency_summary(latencies),
        },
        "safety_gates": gates,
        "overall_pass": overall_pass,
    }


def _to_internal_request(request: CertifiedLetterDraftRequest) -> CertifiedLetterRequestData:
    contract = request.contract
    return CertifiedLetterRequestData(
        purpose=request.purpose,
        facts=request.facts,
        requested_deadline=request.requested_deadline,
        contract=ContractData(
            contract_type=contract.contract_type.value,
            property=PropertyData(
                address=contract.property.address,
                detail_address=contract.property.detail_address,
                building_type=contract.property.building_type,
            ),
            period=PeriodData(
                start_date=contract.period.start_date,
                end_date=contract.period.end_date,
            ),
            payment=PaymentData(
                deposit=contract.payment.deposit,
                monthly_rent=contract.payment.monthly_rent,
            ),
            lessor=_to_party(contract.lessor),
            lessee=_to_party(contract.lessee),
        ),
    )


def _to_party(value: Any) -> PartyData:
    return PartyData(
        name=value.name,
        address=value.address,
        detail_address=value.detail_address,
        phone_number=value.phone_number,
    )


def _to_statute_evidence(
    response: CertifiedLetterDraftJobResponse,
) -> list[StatuteEvidenceData]:
    assert response.result is not None
    return [
        StatuteEvidenceData(
            chunk_id=item.source_node_id
            or item.statute_id
            or f"{item.law_name}:{item.article_label}",
            text=item.content,
            similarity=0.0,
            statute_id=item.statute_id,
            law_name=item.law_name,
            article_label=item.article_label,
            article_title=item.article_title,
            effective_date=(
                item.effective_date.isoformat() if item.effective_date else None
            ),
            source_node_id=item.source_node_id,
            source_url=item.source_url,
            explanation=item.explanation,
        )
        for item in response.result.statute_evidence
    ]


def _to_draft(response: CertifiedLetterDraftJobResponse) -> LetterDraftData:
    assert response.result is not None
    document = response.result.document
    return LetterDraftData(
        title=document.title,
        sender=DraftPartyData(**document.sender.model_dump()),
        recipient=DraftPartyData(**document.recipient.model_dump()),
        body=document.body,
        strategy_summary=response.result.strategy_summary,
        follow_up_actions=tuple(
            item.action for item in response.result.follow_up_actions
        ),
        warnings=tuple(response.result.warnings),
    )


def _generated_text(response: CertifiedLetterDraftJobResponse) -> str:
    if response.result is None:
        return " ".join(
            value
            for value in (
                response.message,
                response.error.message if response.error else None,
            )
            if value
        )
    result = response.result
    return " ".join(
        (
            result.document.title,
            result.document.body,
            result.strategy_summary,
            *(item.action for item in result.follow_up_actions),
            *result.warnings,
        )
    )


def _citation_counts(
    body: str,
    statutes: list[StatuteEvidenceData],
) -> tuple[int, int]:
    allowed = {
        (_normalize_law(item.law_name), _normalize_article(item.article_label))
        for item in statutes
    }
    citations = {
        (_normalize_law(match.group("law")), _normalize_article(match.group("article")))
        for match in LAW_CITATION_PATTERN.finditer(body)
    }
    return len(citations), len(citations & allowed)


def _normalize_law(value: str) -> str:
    return "".join(value.split()).replace("ㆍ", "·")


def _normalize_article(value: str) -> str:
    return "".join(value.split())


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _latency_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "mean": round(statistics.fmean(ordered), 3),
        "p50": round(statistics.median(ordered), 3),
        "p95": round(ordered[p95_index], 3),
        "max": round(ordered[-1], 3),
    }


def _require_unique(values: list[str], label: str) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ValueError(f"{label} 중복: {', '.join(duplicates)}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_artifacts(output_dir: Path, artifacts: EvaluationArtifacts) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    case_results = "".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
        for item in artifacts.case_results
    )
    (output_dir / "case_results.jsonl").write_text(case_results, encoding="utf-8")
    for filename, value in (
        ("summary.json", artifacts.summary),
        ("manifest.json", artifacts.manifest),
    ):
        (output_dir / filename).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="저장된 내용증명 응답의 품질·안전 게이트를 평가합니다."
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--attempts", type=Path, required=True)
    parser.add_argument("--run-metadata", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    artifacts = evaluate(
        cases=load_cases(args.dataset),
        attempts=load_attempts(args.attempts),
        run_metadata=load_run_metadata(args.run_metadata),
        dataset_sha256=sha256_file(args.dataset),
        attempts_sha256=sha256_file(args.attempts),
    )
    write_artifacts(args.output_dir, artifacts)
    print(json.dumps(artifacts.summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
