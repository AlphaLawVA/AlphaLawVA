# evaluate_similarity_explanation_v6.py
"""
Description: A문체 판례 검색 질문으로 Gemini 판례 비교 설명의 근거성·형식·응답시간을 기록한다.
Author: choeminju
Date: 2026-11-02
Before:
    - A/B/C 질문 평가셋, BGE-M3 판례 검색 DB, final_cases, Gemini API 키가 준비된 상태.
After:
    - 현재 프롬프트 버전 비교 설명의 입력·출력·사용량을 별도 평가 폴더에 저장함.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.services.gemini_similarity_explainer import (  # noqa: E402
    DEFAULT_GEMINI_BASE_URL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_MAX_OUTPUT_TOKENS,
    GeminiSimilarityExplainer,
    PROMPT_VERSION,
)
from api.services.precedent_case_repository import PrecedentCaseRepository  # noqa: E402
from ml.evaluation.precedents.evaluate_query_guidance_variants import (  # noqa: E402
    DEFAULT_QUERIES_CSV,
    load_queries,
)
from ml.rag.precedent_retriever import (  # noqa: E402
    BGE_M3_MODEL_NAME,
    DEFAULT_CHUNKING_STRATEGY,
    BgeM3PrecedentRetriever,
)
from precedents.precedent_config import load_env_file  # noqa: E402


DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT
    / "local_data"
    / "precedents"
    / "evaluation"
    / "similarity_explanation_runs"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Gemini precedent explanations.")
    parser.add_argument("--queries-csv", type=Path, default=DEFAULT_QUERIES_CSV)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--model", default=DEFAULT_GEMINI_MODEL)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=DEFAULT_MAX_OUTPUT_TOKENS,
    )
    parser.add_argument("--max-chunks", type=int, default=3)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--summarize-existing", action="store_true")
    return parser.parse_args()


def make_output_dir(path: Path | None, model: str) -> Path:
    if path is not None:
        path.mkdir(parents=True, exist_ok=True)
        return path
    safe_model = model.replace(":", "_").replace("/", "_")
    path = DEFAULT_OUTPUT_ROOT / f"{safe_model}_{PROMPT_VERSION}_{datetime.now():%Y%m%d_%H%M%S}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_review_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "query_id", "query", "precedent_id", "case_number", "court_name",
        "decision_date", "format_compliant", "latency_ms", "matched_reason",
        "user_grounding_score", "precedent_grounding_score", "retrieval_reason_score",
        "difference_score", "tone_safety_score", "total_score", "review_note",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            result = row["result"]
            writer.writerow({
                "query_id": row["query_id"],
                "query": row["query"],
                "precedent_id": result["precedent_id"],
                "case_number": result["case_no"] or "",
                "court_name": result["court_name"] or "",
                "decision_date": result["decision_date"] or "",
                "format_compliant": row["success"],
                "latency_ms": row["latency_ms"],
                "matched_reason": row.get("matched_reason", ""),
                "user_grounding_score": "",
                "precedent_grounding_score": "",
                "retrieval_reason_score": "",
                "difference_score": "",
                "tone_safety_score": "",
                "total_score": "",
                "review_note": "",
            })


def main() -> None:
    args = parse_args()
    if not 1 <= args.max_chunks <= 3:
        raise ValueError("--max-chunks는 1~3이어야 합니다.")

    if args.summarize_existing:
        if args.output_dir is None:
            raise ValueError("--summarize-existing에는 --output-dir가 필요합니다.")
        rows = [
            json.loads(line)
            for line in (args.output_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        write_review_csv(args.output_dir / "manual_review.csv", rows)
        usage_rows = [
            json.loads(line)
            for line in (args.output_dir / "gemini_usage.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        summary = build_summary(
            rows,
            usage_rows,
            args.model,
            max_chunks=args.max_chunks,
            elapsed_seconds=0.0,
        )
        (args.output_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    queries = [item for item in load_queries(args.queries_csv) if item["variant"] == "A"]
    started_at = time.perf_counter()
    output_dir = make_output_dir(args.output_dir, args.model)
    usage_manifest_path = output_dir / "gemini_usage.jsonl"
    results_path = output_dir / "results.jsonl"
    results_path.write_text("", encoding="utf-8")
    retriever = BgeM3PrecedentRetriever(device=args.device)
    repository = PrecedentCaseRepository()
    load_env_file()
    explainer = GeminiSimilarityExplainer(
        api_key=os.environ.get("GEMINI_API_KEY", ""),
        model=args.model,
        base_url=DEFAULT_GEMINI_BASE_URL,
        timeout_seconds=args.timeout,
        max_output_tokens=args.max_output_tokens,
        max_chunks=args.max_chunks,
        usage_manifest_path=usage_manifest_path,
    )

    rows = []
    for index, query_row in enumerate(queries, start=1):
        search_result = retriever.search(query_row["query"], top_k=1)[0]
        precedent = repository.get(search_result.precedent_id)
        call_started_at = time.perf_counter()
        try:
            reason = explainer.explain(
                query=query_row["query"],
                precedent=precedent,
                matched_chunks=search_result.matched_chunks,
            )
            success, error = True, None
        except Exception as exception:
            reason, success, error = "", False, f"{type(exception).__name__}: {exception}"
        row = {
            **query_row,
            "result": search_result.to_dict(),
            "matched_chunks": [chunk.to_dict() for chunk in search_result.matched_chunks[:args.max_chunks]],
            "precedent_summary": precedent.get("생성요약"),
            "easy_summary": precedent.get("쉬운요약"),
            "matched_reason": reason,
            "success": success,
            "error": error,
            "latency_ms": round((time.perf_counter() - call_started_at) * 1000, 2),
        }
        rows.append(row)
        with results_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"[{index}/{len(queries)}] {query_row['query_id']}: success={success}, latency={row['latency_ms']:.0f}ms")

    write_review_csv(output_dir / "manual_review.csv", rows)
    usage_rows = [
        json.loads(line)
        for line in usage_manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    summary = build_summary(
        rows,
        usage_rows,
        args.model,
        max_chunks=args.max_chunks,
        elapsed_seconds=time.perf_counter() - started_at,
    )
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"결과 저장: {output_dir}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def build_summary(
    rows: list[dict[str, Any]],
    usage_rows: list[dict[str, Any]],
    model: str,
    *,
    max_chunks: int,
    elapsed_seconds: float,
) -> dict[str, Any]:
    return {
        "schema_version": "precedent_similarity_explanation_eval.v1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": "gemini",
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "query_variant": "A",
        "query_count": len(rows),
        "success_count": sum(row["success"] for row in rows),
        "format_compliance_rate": round(sum(row["success"] for row in rows) / len(rows), 4),
        "mean_latency_ms": round(sum(row["latency_ms"] for row in rows) / len(rows), 2),
        "input_tokens": sum(item["input_tokens"] for item in usage_rows),
        "output_tokens": sum(item["output_tokens"] for item in usage_rows),
        "estimated_cost_usd": round(
            sum(item["estimated_cost_usd"] for item in usage_rows), 8
        ),
        "elapsed_seconds_including_retrieval": round(elapsed_seconds, 3),
        "retrieval": {
            "method": "bge-m3-dense",
            "embedding_model": BGE_M3_MODEL_NAME,
            "chunking_strategy": DEFAULT_CHUNKING_STRATEGY,
            "top_precedents": 1,
            "max_matched_chunks": max_chunks,
        },
        "review_rubric": {
            "user_grounding_score": "0~2",
            "precedent_grounding_score": "0~2",
            "retrieval_reason_score": "0~2",
            "difference_score": "0~2",
            "tone_safety_score": "0~2",
        },
    }


if __name__ == "__main__":
    main()
