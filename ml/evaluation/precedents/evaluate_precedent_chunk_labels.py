# evaluate_precedent_chunk_labels.py
"""
Description: 고정 판례 후보의 질문-청크 쌍을 블라인드 평가용으로 만들고
0~3점 청크 관련도 라벨로 Dense와 리랭커 순위를 다시 평가한다.
Author: choeminju
Date: 2026-11-12
Before:
    - 판례 리랭커 v2 후보 풀과 모델별 순위 파일이 준비된 상태.
After:
    - 블라인드 청크 평가 파일 또는 청크 관련도 기반 모델 비교 결과가 생성된다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RELEVANT_THRESHOLD = 2
LABEL_FIELDS = [
    "adjudication_id",
    "query_id",
    "case_key",
    "precedent_id",
    "chunk_id",
    "relevance",
    "rationale",
    "reviewer",
]
REVIEW_FIELDS = [
    "adjudication_id",
    "query_id",
    "dispute_type",
    "query_specificity",
    "query",
    "case_key",
    "precedent_id",
    "case_number",
    "case_name",
    "court_name",
    "decision_date",
    "chunk_id",
    "chunk_type",
    "chunk_text",
    "relevance",
    "rationale",
    "reviewer",
]
METHOD_SUMMARY_FIELDS = [
    "method",
    "query_count",
    "ndcg@5",
    "precision@3",
    "recall@5",
    "mrr@5",
    "mean_top3_score",
    "oracle_chunk_match_rate@5",
    "mean_chunk_regret@5",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def adjudication_id(query_id: str, case_key: str, chunk_id: str) -> str:
    value = f"{query_id}\0{case_key}\0{chunk_id}".encode()
    return hashlib.sha256(value).hexdigest()[:16]


def build_review_rows(candidate_pool: dict[str, Any], seed: int) -> list[dict[str, Any]]:
    rows = []
    for query_row in candidate_pool["queries"]:
        query_rows = []
        for candidate in query_row["candidates"]:
            for chunk in candidate["chunks"]:
                query_rows.append(
                    {
                        "adjudication_id": adjudication_id(
                            query_row["query_id"],
                            candidate["case_key"],
                            chunk["chunk_id"],
                        ),
                        "query_id": query_row["query_id"],
                        "dispute_type": query_row["dispute_type"],
                        "query_specificity": query_row["query_specificity"],
                        "query": query_row["query"],
                        "case_key": candidate["case_key"],
                        "precedent_id": candidate["precedent_id"],
                        "case_number": candidate["case_number"],
                        "case_name": candidate["case_name"],
                        "court_name": candidate["court_name"],
                        "decision_date": candidate["decision_date"],
                        "chunk_id": chunk["chunk_id"],
                        "chunk_type": chunk["chunk_type"],
                        "chunk_text": " ".join(chunk["text"].split()),
                        "relevance": "",
                        "rationale": "",
                        "reviewer": "",
                    }
                )
        random.Random(f"{seed}:{query_row['query_id']}").shuffle(query_rows)
        rows.extend(query_rows)
    return rows


def prepare_review(args: argparse.Namespace) -> None:
    candidate_pool = json.loads(args.candidate_pool.read_text(encoding="utf-8"))
    rows = build_review_rows(candidate_pool, args.seed)
    write_csv(args.output_csv, rows, REVIEW_FIELDS)
    write_json(
        args.output_csv.with_suffix(".manifest.json"),
        {
            "schema_version": "precedent_chunk_adjudication.v1",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "candidate_pool_sha256": file_sha256(args.candidate_pool),
            "query_count": len({row["query_id"] for row in rows}),
            "question_chunk_pair_count": len(rows),
            "seed": args.seed,
            "rubric": {
                "3": "질문의 핵심 사실관계와 법적 쟁점에 직접 답한다.",
                "2": "핵심 법리는 관련 있지만 사실관계나 쟁점 일부가 다르다.",
                "1": "같은 분야이나 질문 해결에는 간접적으로만 도움 된다.",
                "0": "질문과 실질적으로 무관하다.",
            },
        },
    )
    print(f"블라인드 평가 파일: {args.output_csv} ({len(rows)}행)")


def load_labels(path: Path) -> dict[str, dict[str, Any]]:
    labels = {}
    paths = sorted(path.glob("*.csv")) if path.is_dir() else [path]
    if not paths:
        raise ValueError(f"평가 CSV를 찾을 수 없습니다: {path}")
    for labels_path in paths:
        for row in read_csv(labels_path):
            item_id = row.get("adjudication_id", "").strip()
            raw_relevance = row.get("relevance", "").strip()
            if not item_id or not raw_relevance:
                raise ValueError(f"평가 ID 또는 점수가 없습니다: {row}")
            try:
                relevance = int(raw_relevance)
            except ValueError as error:
                raise ValueError(f"점수는 0~3 정수여야 합니다: {raw_relevance}") from error
            if relevance not in {0, 1, 2, 3}:
                raise ValueError(f"점수는 0~3이어야 합니다: {relevance}")
            if item_id in labels:
                raise ValueError(f"평가 ID가 중복되었습니다: {item_id}")
            labels[item_id] = {**row, "relevance": relevance}
    return labels


def labels_sha256(path: Path) -> str:
    if path.is_file():
        return file_sha256(path)
    digest = hashlib.sha256()
    paths = sorted(path.glob("*.csv"))
    if not paths:
        raise ValueError(f"평가 CSV를 찾을 수 없습니다: {path}")
    for labels_path in paths:
        digest.update(labels_path.name.encode())
        digest.update(bytes.fromhex(file_sha256(labels_path)))
    return digest.hexdigest()


def dcg(relevances: list[int]) -> float:
    return sum(
        ((2**relevance) - 1) / math.log2(index + 2)
        for index, relevance in enumerate(relevances)
    )


def compute_chunk_metrics(
    selected_relevances: list[int],
    oracle_relevances: list[int],
) -> dict[str, float]:
    selected_top5 = selected_relevances[:5]
    oracle_top5 = sorted(oracle_relevances, reverse=True)[:5]
    ideal_dcg = dcg(oracle_top5)
    first_relevant = next(
        (
            rank
            for rank, score in enumerate(selected_top5, start=1)
            if score >= RELEVANT_THRESHOLD
        ),
        None,
    )
    top3 = selected_relevances[:3]
    relevant_oracle_count = sum(
        score >= RELEVANT_THRESHOLD for score in oracle_relevances
    )
    return {
        "ndcg@5": round(dcg(selected_top5) / ideal_dcg, 6) if ideal_dcg else 0.0,
        "precision@3": round(
            sum(score >= RELEVANT_THRESHOLD for score in top3) / max(len(top3), 1),
            6,
        ),
        "recall@5": round(
            sum(score >= RELEVANT_THRESHOLD for score in selected_top5)
            / relevant_oracle_count,
            6,
        )
        if relevant_oracle_count
        else 0.0,
        "mrr@5": round(1 / first_relevant, 6) if first_relevant else 0.0,
        "mean_top3_score": round(sum(top3) / max(len(top3), 1), 6),
    }


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def candidate_label_maps(
    candidate_pool: dict[str, Any],
    labels: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    chunk_scores: dict[str, dict[str, int]] = defaultdict(dict)
    oracle_case_scores: dict[str, dict[str, int]] = defaultdict(dict)
    expected_ids = set()
    for query_row in candidate_pool["queries"]:
        query_id = query_row["query_id"]
        for candidate in query_row["candidates"]:
            case_scores = []
            for chunk in candidate["chunks"]:
                item_id = adjudication_id(query_id, candidate["case_key"], chunk["chunk_id"])
                expected_ids.add(item_id)
                if item_id not in labels:
                    raise ValueError(f"청크 평가가 누락되었습니다: {item_id}")
                score = int(labels[item_id]["relevance"])
                chunk_scores[query_id][chunk["chunk_id"]] = score
                case_scores.append(score)
            oracle_case_scores[query_id][candidate["case_key"]] = max(case_scores)
    extras = set(labels) - expected_ids
    if extras:
        raise ValueError(f"후보 풀에 없는 평가 ID가 있습니다: {sorted(extras)[:5]}")
    return dict(chunk_scores), dict(oracle_case_scores)


def dense_rankings(candidate_pool: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    return {
        query_row["query_id"]: [
            {
                "case_key": candidate["case_key"],
                "chunk_id": candidate["chunks"][0]["chunk_id"],
            }
            for candidate in query_row["candidates"]
        ]
        for query_row in candidate_pool["queries"]
    }


def reranker_rankings(path: Path) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(path):
        grouped[row["query_id"]].append(
            {
                "rank": row["rank"],
                "case_key": "".join(row["case_number"].split()).casefold()
                or f"precedent:{row['precedent_id']}",
                "chunk_id": row["best_chunk_id"],
            }
        )
    for rows in grouped.values():
        rows.sort(key=lambda row: int(row["rank"]))
    return dict(grouped)


def score_method(
    method: str,
    rankings: dict[str, list[dict[str, str]]],
    chunk_scores: dict[str, dict[str, int]],
    oracle_case_scores: dict[str, dict[str, int]],
    query_metadata: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    query_rows = []
    for query_id, ranked_rows in rankings.items():
        selected = [chunk_scores[query_id][row["chunk_id"]] for row in ranked_rows]
        oracle = list(oracle_case_scores[query_id].values())
        metrics = compute_chunk_metrics(selected, oracle)
        top5 = ranked_rows[:5]
        oracle_matches = [
            selected[index] == oracle_case_scores[query_id][row["case_key"]]
            for index, row in enumerate(top5)
        ]
        regrets = [
            oracle_case_scores[query_id][row["case_key"]] - selected[index]
            for index, row in enumerate(top5)
        ]
        query_rows.append(
            {
                "method": method,
                "query_id": query_id,
                **query_metadata[query_id],
                **metrics,
                "oracle_chunk_match_rate@5": round(sum(oracle_matches) / len(top5), 6),
                "mean_chunk_regret@5": round(sum(regrets) / len(top5), 6),
            }
        )
    summary = {
        "method": method,
        "query_count": len(query_rows),
        **{
            field: mean([float(row[field]) for row in query_rows])
            for field in METHOD_SUMMARY_FIELDS[2:]
        },
    }
    return query_rows, summary


def score_labels(args: argparse.Namespace) -> None:
    candidate_pool = json.loads(args.candidate_pool.read_text(encoding="utf-8"))
    labels = load_labels(args.labels_csv)
    chunk_scores, oracle_case_scores = candidate_label_maps(candidate_pool, labels)
    metadata = {
        row["query_id"]: {
            "dispute_type": row["dispute_type"],
            "query_specificity": row["query_specificity"],
        }
        for row in candidate_pool["queries"]
    }
    methods = {"dense": dense_rankings(candidate_pool)}
    for rankings_path in args.rankings_csv:
        method = rankings_path.parent.name
        methods[method] = reranker_rankings(rankings_path)

    all_query_rows = []
    summaries = []
    for method, rankings in methods.items():
        query_rows, summary = score_method(
            method,
            rankings,
            chunk_scores,
            oracle_case_scores,
            metadata,
        )
        all_query_rows.extend(query_rows)
        summaries.append(summary)

    write_csv(args.output_dir / "method_summary.csv", summaries, METHOD_SUMMARY_FIELDS)
    write_csv(
        args.output_dir / "query_metrics.csv",
        all_query_rows,
        [
            "method",
            "query_id",
            "dispute_type",
            "query_specificity",
            *METHOD_SUMMARY_FIELDS[2:],
        ],
    )
    write_json(
        args.output_dir / "manifest.json",
        {
            "schema_version": "precedent_chunk_relevance_comparison.v1",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "candidate_pool_sha256": file_sha256(args.candidate_pool),
            "labels_csv_sha256": labels_sha256(args.labels_csv),
            "label_count": len(labels),
            "label_distribution": dict(
                sorted(Counter(item["relevance"] for item in labels.values()).items())
            ),
            "reviewer_distribution": dict(
                sorted(Counter(item["reviewer"] for item in labels.values()).items())
            ),
            "summaries": summaries,
        },
    )
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="판례 질문-청크 관련도 평가")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="블라인드 평가 CSV를 만든다.")
    prepare.add_argument("--candidate-pool", type=Path, required=True)
    prepare.add_argument("--output-csv", type=Path, required=True)
    prepare.add_argument("--seed", type=int, default=20261112)
    prepare.set_defaults(handler=prepare_review)

    score = subparsers.add_parser("score", help="완료된 청크 라벨로 모델을 평가한다.")
    score.add_argument("--candidate-pool", type=Path, required=True)
    score.add_argument("--labels-csv", type=Path, required=True)
    score.add_argument("--rankings-csv", type=Path, nargs="+", required=True)
    score.add_argument("--output-dir", type=Path, required=True)
    score.set_defaults(handler=score_labels)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
