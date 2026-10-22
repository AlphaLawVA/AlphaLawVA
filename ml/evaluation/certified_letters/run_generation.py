# run_generation.py
"""
Description: 합성 내용증명 평가셋을 실제 로컬 검색기와 승인된 생성 모델로 실행한다.
호출별 사용량과 비용을 기록하고 세션 상한 안에서 오프라인 안전 평가까지 수행한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 합성 평가셋, 로컬 법령·판례 DB, 모델 캐시와 승인된 API 키가 준비된 상태.
After:
    - 로컬 전용 attempts, usage, metadata와 품질·안전 평가 결과가 생성.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from api.services.certified_letter_configuration import (
    create_certified_letter_service_from_env,
)
from ml.agent.certified_letter.gemini import DEFAULT_GEMINI_MODEL
from ml.agent.certified_letter.prompts import PROMPT_VERSION
from ml.evaluation.certified_letters.evaluate_outputs import (
    RecordedAttempt,
    evaluate,
    load_attempts,
    load_cases,
    load_run_metadata,
    sha256_file,
    write_artifacts,
)
from precedents.precedent_config import load_env_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="승인된 Gemini 호출로 내용증명 합성 평가셋을 실행합니다."
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--project-data-root", type=Path, required=True)
    parser.add_argument("--max-cost-usd", type=float, required=True)
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--model", default=DEFAULT_GEMINI_MODEL)
    parser.add_argument("--statute-reranker", default="dense")
    parser.add_argument("--precedent-reranker", default="dense")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.max_cost_usd <= 0:
        raise ValueError("max-cost-usd는 0보다 커야 합니다.")
    if args.max_output_tokens < 1:
        raise ValueError("max-output-tokens는 1 이상이어야 합니다.")

    output_dir = args.output_dir.resolve()
    attempts_path = output_dir / "attempts.jsonl"
    usage_path = output_dir / "usage.jsonl"
    metadata_path = output_dir / "run_metadata.json"
    if attempts_path.exists() and not args.resume:
        raise FileExistsError(
            f"기존 평가 응답이 있습니다. --resume을 사용하세요: {attempts_path}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    load_env_file(args.env_file.resolve())
    existing_cost = _usage_totals(usage_path)["estimated_cost_usd"]
    remaining_budget = args.max_cost_usd - existing_cost
    if remaining_budget <= 0:
        raise RuntimeError("기존 사용량이 승인된 비용 상한에 도달했습니다.")
    _configure_environment(
        args=args,
        output_dir=output_dir,
        remaining_budget=remaining_budget,
    )
    metadata = _run_metadata(args)
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    cases = load_cases(args.dataset.resolve())
    if args.case_id:
        selected_ids = set(args.case_id)
        cases = [case for case in cases if case.case_id in selected_ids]
        missing_ids = sorted(selected_ids - {case.case_id for case in cases})
        if missing_ids:
            raise ValueError("평가셋에 없는 case-id: " + ", ".join(missing_ids))
    existing_attempts = load_attempts(attempts_path) if attempts_path.exists() else []
    completed_ids = {item.case_id for item in existing_attempts}
    service = create_certified_letter_service_from_env()
    if service.graph is None:
        raise RuntimeError("Gemini API 키 또는 내용증명 런타임 설정이 없습니다.")

    for case in cases:
        if case.case_id in completed_ids:
            continue
        before_calls = _usage_totals(usage_path)["record_count"]
        started = time.monotonic()
        execution_error = None
        response = None
        diagnostics: dict[str, Any] = {}
        try:
            submission = service.submit(
                case.request,
                idempotency_key=f"{case.dataset_version}:{case.case_id}",
            )
            state = service.process(submission.response.draft_id)
            if state is not None:
                diagnostics = {
                    "repair_attempts": int(state.get("repair_attempts", 0)),
                    "validation_issue_codes": [
                        item.code for item in state.get("validation_issues", ())
                    ],
                }
            response = service.get(submission.response.draft_id).model_dump(mode="json")
        except Exception as error:
            execution_error = type(error).__name__
        latency_ms = round((time.monotonic() - started) * 1000, 3)
        totals = _usage_totals(usage_path)
        attempt = RecordedAttempt(
            case_id=case.case_id,
            response=response,
            latency_ms=latency_ms,
            model_calls=totals["record_count"] - before_calls,
            execution_error=execution_error,
            diagnostics=diagnostics,
        )
        _append_jsonl(attempts_path, attempt.model_dump(mode="json"))
        status = response.get("status") if response else "runner_error"
        outcome = response.get("outcome") if response else None
        print(
            json.dumps(
                {
                    "case_id": case.case_id,
                    "status": status,
                    "outcome": outcome,
                    "model_calls": attempt.model_calls,
                    "cumulative_cost_usd": totals["estimated_cost_usd"],
                },
                ensure_ascii=False,
            )
        )
        if totals["estimated_cost_usd"] >= args.max_cost_usd:
            break

    attempts = load_attempts(attempts_path)
    artifacts = evaluate(
        cases=cases,
        attempts=attempts,
        run_metadata=load_run_metadata(metadata_path),
        dataset_sha256=sha256_file(args.dataset.resolve()),
        attempts_sha256=sha256_file(attempts_path),
    )
    write_artifacts(output_dir / "evaluation", artifacts)
    totals = _usage_totals(usage_path)
    print(
        json.dumps(
            {
                "summary": artifacts.summary,
                "usage": totals,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _configure_environment(
    *,
    args: argparse.Namespace,
    output_dir: Path,
    remaining_budget: float,
) -> None:
    data_root = args.project_data_root.resolve()
    values = {
        "CERTIFIED_LETTER_GENERATION_ENABLED": "true",
        "CERTIFIED_LETTER_GEMINI_MODEL": args.model,
        "CERTIFIED_LETTER_GEMINI_MAX_OUTPUT_TOKENS": str(args.max_output_tokens),
        "CERTIFIED_LETTER_GEMINI_USAGE_MANIFEST": str(output_dir / "usage.jsonl"),
        "CERTIFIED_LETTER_GEMINI_SESSION_BUDGET_USD": str(remaining_budget),
        "ALPHALAWVA_LOCAL_DATA_DIR": str(data_root / "local_data"),
        "CERTIFIED_LETTER_STATUTE_DB_DIR": str(
            data_root / "data/statutes/vectorstores/bge_m3"
        ),
        "CERTIFIED_LETTER_PRECEDENT_DB_DIR": str(
            data_root / "local_data/precedents/vector_dbs/precedents_bge-m3_v1"
        ),
        "CERTIFIED_LETTER_MODEL_CACHE_DIR": str(data_root / "data/statutes/models"),
        "CERTIFIED_LETTER_STATUTE_RERANKER": args.statute_reranker,
        "CERTIFIED_LETTER_PRECEDENT_RERANKER": args.precedent_reranker,
    }
    os.environ.update(values)


def _run_metadata(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "provider": "google",
        "model": args.model,
        "prompt_version": PROMPT_VERSION,
        "statute_embedding": "BAAI/bge-m3",
        "statute_chunking": "article_with_overflow_split_v01",
        "statute_retrieval": "dense_candidate20_then_top5",
        "statute_reranker": args.statute_reranker,
        "precedent_embedding": "BAAI/bge-m3",
        "precedent_chunking": "A_reason_summary_v1",
        "precedent_retrieval": "dense_candidate20_then_top5",
        "precedent_reranker": args.precedent_reranker,
        "max_output_tokens": args.max_output_tokens,
        "max_cost_usd": args.max_cost_usd,
    }


def _usage_totals(path: Path) -> dict[str, int | float]:
    if not path.exists():
        return {
            "record_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
        }
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {
        "record_count": len(records),
        "input_tokens": sum(int(item.get("input_tokens", 0)) for item in records),
        "output_tokens": sum(int(item.get("output_tokens", 0)) for item in records),
        "total_tokens": sum(int(item.get("total_tokens", 0)) for item in records),
        "estimated_cost_usd": round(
            sum(float(item.get("estimated_cost_usd", 0.0)) for item in records),
            8,
        ),
    }


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
