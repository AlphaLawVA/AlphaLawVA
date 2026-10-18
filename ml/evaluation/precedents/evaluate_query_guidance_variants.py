# evaluate_query_guidance_variants.py
"""
Description: 생활어 판례 검색 질문의 A/B/C 표현을 BGE-M3로 검색하고
대표 적중 청크를 사람 평가용 CSV로 저장하며 입력된 1~3점 평점을 집계한다.
Author: choeminju
Date: 2026-10-17
Before:
    - A/B/C 질문 CSV와 로컬 BGE-M3 판례 벡터DB가 준비된 상태.
After:
    - local_data/precedents/evaluation/query_guidance_runs/에 검색 청크와 버전별 통계가 생성.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml.rag.precedent_retriever import (  # noqa: E402
    BGE_M3_MODEL_NAME,
    DEFAULT_CHUNKING_STRATEGY,
    BgeM3PrecedentRetriever,
)


DEFAULT_QUERIES_CSV = (
    Path(__file__).resolve().parent
    / "datasets"
    / "query_guidance_variants_v01.csv"
)
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT
    / "local_data"
    / "precedents"
    / "evaluation"
    / "query_guidance_runs"
)
DEFAULT_TOP_K = 3
VARIANTS = ("A", "B", "C")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate natural-language query guidance variants with BGE-M3.",
    )
    parser.add_argument("--queries-csv", type=Path, default=DEFAULT_QUERIES_CSV)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument(
        "--scores-csv",
        type=Path,
        default=None,
        help="human_score가 채워진 검색 결과 CSV를 집계할 때 사용한다.",
    )
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_output_dir(path: Path | None) -> Path:
    output_dir = path
    if output_dir is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = DEFAULT_OUTPUT_ROOT / f"query_guidance_v01_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def load_queries(path: Path) -> list[dict[str, str]]:
    rows = read_csv(path)
    required_fields = {"query_id", "scenario_id", "variant", "query"}
    if not rows or not required_fields.issubset(rows[0]):
        raise ValueError(f"질문 CSV에 필요한 컬럼이 없습니다: {sorted(required_fields)}")

    normalized = []
    scenario_variants: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        item = {field: row.get(field, "").strip() for field in required_fields}
        if not all(item.values()):
            raise ValueError(f"질문 CSV에 빈 값이 있습니다: {row}")
        if item["variant"] not in VARIANTS:
            raise ValueError(f"지원하지 않는 질문 버전입니다: {item['variant']}")
        scenario_variants[item["scenario_id"]].add(item["variant"])
        normalized.append(item)

    invalid = {
        scenario_id: sorted(variants)
        for scenario_id, variants in scenario_variants.items()
        if variants != set(VARIANTS)
    }
    if invalid:
        raise ValueError(f"시나리오별 A/B/C 구성이 완전하지 않습니다: {invalid}")
    return sorted(normalized, key=lambda row: (row["variant"], row["scenario_id"]))


def retrieve(args: argparse.Namespace) -> None:
    if args.top_k < 1:
        raise ValueError("--top-k는 1 이상이어야 합니다.")
    queries = load_queries(args.queries_csv)
    output_dir = make_output_dir(args.output_dir)
    retriever = BgeM3PrecedentRetriever(device=args.device)

    started = time.perf_counter()
    results_by_query = retriever.search_many(
        [row["query"] for row in queries],
        top_k=args.top_k,
    )
    elapsed_seconds = time.perf_counter() - started

    output_rows: list[dict[str, Any]] = []
    for query_row, results in zip(queries, results_by_query, strict=True):
        for result in results:
            best_chunk = min(result.matched_chunks, key=lambda chunk: chunk.distance)
            output_rows.append(
                {
                    **query_row,
                    "result_rank": result.rank,
                    "precedent_id": result.precedent_id,
                    "case_number": result.case_no or "",
                    "case_name": result.case_name or "",
                    "court_name": result.court_name or "",
                    "decision_date": result.decision_date or "",
                    "similarity": round(result.similarity, 6),
                    "chunk_rank": best_chunk.rank,
                    "chunk_id": best_chunk.chunk_id,
                    "chunk_type": best_chunk.chunk_type or "",
                    "section": best_chunk.section or "",
                    "chunk_text": " ".join(best_chunk.text.split()),
                    "human_score": "",
                    "review_note": "",
                }
            )

    fields = [
        "query_id",
        "scenario_id",
        "variant",
        "query",
        "result_rank",
        "precedent_id",
        "case_number",
        "case_name",
        "court_name",
        "decision_date",
        "similarity",
        "chunk_rank",
        "chunk_id",
        "chunk_type",
        "section",
        "chunk_text",
        "human_score",
        "review_note",
    ]
    review_path = output_dir / "retrieved_chunks_for_review.csv"
    write_csv(review_path, output_rows, fields)

    manifest = {
        "schema_version": "precedent_query_guidance_eval.v1",
        "created_at": now_utc_iso(),
        "queries_csv": str(args.queries_csv.resolve()),
        "query_count": len(queries),
        "scenario_count": len({row["scenario_id"] for row in queries}),
        "variants": list(VARIANTS),
        "retrieval_method": "bge-m3-dense",
        "embedding_model": BGE_M3_MODEL_NAME,
        "chunking_strategy": DEFAULT_CHUNKING_STRATEGY,
        "top_k_precedents": args.top_k,
        "representative_chunks_scored_per_query": args.top_k,
        "llm_calls": 0,
        "elapsed_seconds_including_model_load": round(elapsed_seconds, 4),
        "review_file": str(review_path),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"검색 완료: {len(queries)}개 질문, {len(output_rows)}개 대표 청크")
    print(f"소요 시간(모델 로드 포함): {elapsed_seconds:.2f}초")
    print(f"평가 파일: {review_path}")


def parse_score(row: dict[str, str]) -> int:
    raw_score = row.get("human_score", "").strip()
    try:
        score = int(raw_score)
    except ValueError as error:
        raise ValueError(
            f"{row.get('query_id')} rank={row.get('result_rank')}의 평점이 없습니다."
        ) from error
    if score not in {1, 2, 3}:
        raise ValueError(f"평점은 1, 2, 3 중 하나여야 합니다: {score}")
    return score


def summarize(args: argparse.Namespace) -> None:
    assert args.scores_csv is not None
    rows = read_csv(args.scores_csv)
    if not rows:
        raise ValueError("집계할 평점 행이 없습니다.")

    variant_scores: dict[str, list[int]] = defaultdict(list)
    variant_top1: dict[str, list[int]] = defaultdict(list)
    query_scores: dict[str, list[int]] = defaultdict(list)
    query_metadata: dict[str, dict[str, str]] = {}
    for row in rows:
        score = parse_score(row)
        variant = row["variant"].strip()
        query_id = row["query_id"].strip()
        variant_scores[variant].append(score)
        query_scores[query_id].append(score)
        query_metadata[query_id] = row
        if int(row["result_rank"]) == 1:
            variant_top1[variant].append(score)

    variant_rows = []
    for variant in VARIANTS:
        scores = variant_scores[variant]
        top1_scores = variant_top1[variant]
        variant_rows.append(
            {
                "variant": variant,
                "question_count": len(top1_scores),
                "scored_chunk_count": len(scores),
                "mean_score": round(sum(scores) / len(scores), 4),
                "mean_top1_score": round(sum(top1_scores) / len(top1_scores), 4),
                "score_3_count": scores.count(3),
                "score_3_rate": round(scores.count(3) / len(scores), 4),
                "score_2_or_3_count": sum(score >= 2 for score in scores),
                "score_2_or_3_rate": round(
                    sum(score >= 2 for score in scores) / len(scores),
                    4,
                ),
            }
        )

    question_rows = []
    scenario_means: dict[str, dict[str, float]] = defaultdict(dict)
    for query_id, scores in sorted(query_scores.items()):
        metadata = query_metadata[query_id]
        mean_score = round(sum(scores) / len(scores), 4)
        scenario_means[metadata["scenario_id"]][metadata["variant"]] = mean_score
        question_rows.append(
            {
                "query_id": query_id,
                "scenario_id": metadata["scenario_id"],
                "variant": metadata["variant"],
                "mean_score": mean_score,
                "score_3_count": scores.count(3),
                "score_2_or_3_count": sum(score >= 2 for score in scores),
            }
        )

    winner_rows = []
    variant_wins = defaultdict(int)
    for scenario_id, means in sorted(scenario_means.items()):
        best_score = max(means.values())
        winners = [variant for variant in VARIANTS if means[variant] == best_score]
        for winner in winners:
            variant_wins[winner] += 1
        winner_rows.append(
            {
                "scenario_id": scenario_id,
                "mean_A": means["A"],
                "mean_B": means["B"],
                "mean_C": means["C"],
                "best_mean_score": best_score,
                "winner": "+".join(winners),
            }
        )

    output_dir = args.scores_csv.resolve().parent
    write_csv(output_dir / "variant_summary.csv", variant_rows, list(variant_rows[0]))
    write_csv(output_dir / "query_summary.csv", question_rows, list(question_rows[0]))
    write_csv(output_dir / "scenario_winners.csv", winner_rows, list(winner_rows[0]))

    print("평점 집계 완료")
    for row in variant_rows:
        print(
            f"- {row['variant']}: 평균={row['mean_score']}, "
            f"Top1 평균={row['mean_top1_score']}, "
            f"3점 비율={row['score_3_rate']}, "
            f"2점 이상 비율={row['score_2_or_3_rate']}, "
            f"시나리오 승리={variant_wins[row['variant']]}"
        )


def main() -> None:
    args = parse_args()
    if args.scores_csv is not None:
        summarize(args)
    else:
        retrieve(args)


if __name__ == "__main__":
    main()
