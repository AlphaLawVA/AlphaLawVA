# evaluate_precedent_embeddings.py
"""
Description: 판례 검색 골드셋을 기준으로 A안 벡터DB의 임베딩 모델별 검색 성능을 평가한다.
청크 검색 결과를 판례 단위로 묶은 뒤 Recall, MRR, nDCG 등 검색 지표를 계산한다.
Author: choeminju
Date: 2026-09-13
Before:
    - 판례 검색 골드셋 CSV와 A_reason_summary_v1 벡터DB 3종이 준비된 상태.
    - 같은 청킹 결과를 KURE, BGE-M3, OpenAI 임베딩으로 각각 적재한 상태.
After:
    - local_data/precedents/evaluation/embedding_eval_runs/ 하위에 모델별 평가 결과 CSV와 manifest가 생성.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml.evaluation.precedents.pool_precedent_candidates import (  # noqa: E402
    DEFAULT_VECTOR_DB_ROOT,
    compact_text,
    embed_query,
    load_dotenv,
    load_openai_client,
    load_sentence_transformer,
    parse_embedding_keys,
    read_json,
    search_collection,
)


LOCAL_DATA_ROOT = PROJECT_ROOT / "local_data"
DEFAULT_GOLDSET_DIR = LOCAL_DATA_ROOT / "precedents" / "evaluation" / "goldset_v01"
DEFAULT_QUESTIONS_CSV = DEFAULT_GOLDSET_DIR / "precedent_retrieval_gold_questions_v01.csv"
DEFAULT_LABELS_CSV = DEFAULT_GOLDSET_DIR / "precedent_retrieval_gold_labels_v01.csv"
DEFAULT_OUTPUT_ROOT = (
    LOCAL_DATA_ROOT / "precedents" / "evaluation" / "embedding_eval_runs"
)
DEFAULT_CHUNK_TOP_K = 10
DEFAULT_METRIC_KS = (5, 10)
DEFAULT_CHUNKING_STRATEGY = "A_reason_summary_v1"
RELEVANT_THRESHOLD = 2


@dataclass(slots=True)
class GoldQuery:
    """평가 질문 한 건과 분석용 라벨을 담는다."""

    query_id: str
    query: str
    dispute_type: str = ""
    query_specificity: str = ""
    scenario_tags: str = ""


@dataclass(slots=True)
class RetrievedPrecedent:
    """청크 검색 결과를 판례 단위로 묶은 구조."""

    precedent_id: str
    best_chunk_rank: int = 10**9
    best_distance: float | None = None
    chunk_ids: list[str] = field(default_factory=list)
    chunk_types: list[str] = field(default_factory=list)

    def add_chunk(
        self,
        chunk_id: str,
        chunk_type: str,
        chunk_rank: int,
        distance: float | None,
    ) -> None:
        """같은 판례에서 검색된 청크 정보를 추가하고 대표 순위를 갱신한다."""
        self.chunk_ids.append(chunk_id)
        if chunk_type:
            self.chunk_types.append(chunk_type)
        self.best_chunk_rank = min(self.best_chunk_rank, chunk_rank)
        if distance is None:
            return
        if self.best_distance is None or distance < self.best_distance:
            self.best_distance = distance


def parse_args() -> argparse.Namespace:
    """명령행 옵션을 정의한다."""
    parser = argparse.ArgumentParser(
        description="Evaluate precedent embedding retrieval against goldset.",
    )
    parser.add_argument(
        "--questions-csv",
        type=Path,
        default=DEFAULT_QUESTIONS_CSV,
        help="골드셋 질문 CSV 경로.",
    )
    parser.add_argument(
        "--labels-csv",
        type=Path,
        default=DEFAULT_LABELS_CSV,
        help="골드셋 판례 라벨 CSV 경로.",
    )
    parser.add_argument(
        "--vector-db-root",
        type=Path,
        default=DEFAULT_VECTOR_DB_ROOT,
        help="A_reason_summary_v1__* 벡터DB 폴더들이 들어 있는 루트.",
    )
    parser.add_argument(
        "--chunking-strategy",
        default=DEFAULT_CHUNKING_STRATEGY,
        help="평가할 벡터DB 청킹 전략명. 예: A_reason_summary_v1, B_reason_summary_issue_holding_v1",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="평가 결과 저장 폴더. 기본값은 타임스탬프 폴더.",
    )
    parser.add_argument(
        "--embeddings",
        default="kure,bge-m3,openai",
        help="평가할 임베딩 설정. 예: kure,bge-m3,openai",
    )
    parser.add_argument(
        "--chunk-top-k",
        type=int,
        default=DEFAULT_CHUNK_TOP_K,
        help="모델별 Chroma에서 먼저 가져올 청크 Top-K.",
    )
    parser.add_argument(
        "--metric-ks",
        default="5,10",
        help="판례 단위 지표를 계산할 K 목록. 예: 5,10",
    )
    parser.add_argument(
        "--query-limit",
        type=int,
        default=None,
        help="테스트용 질문 개수 제한.",
    )
    return parser.parse_args()


def now_utc_iso() -> str:
    """현재 UTC 시각을 ISO 문자열로 반환한다."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_output_dir(base_dir: Path | None) -> Path:
    """평가 결과 폴더를 만들고 반환한다."""
    if base_dir is not None:
        output_dir = base_dir
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = DEFAULT_OUTPUT_ROOT / f"{DEFAULT_CHUNKING_STRATEGY}_goldset_v01_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def make_strategy_output_dir(base_dir: Path | None, chunking_strategy: str) -> Path:
    """청킹 전략명이 포함된 평가 결과 폴더를 만들고 반환한다."""
    if base_dir is not None:
        output_dir = base_dir
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = DEFAULT_OUTPUT_ROOT / f"{chunking_strategy}_goldset_v01_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def parse_metric_ks(raw_value: str) -> list[int]:
    """쉼표로 받은 K 목록을 양의 정수 목록으로 변환한다."""
    values = [int(value.strip()) for value in raw_value.split(",") if value.strip()]
    if not values or any(value <= 0 for value in values):
        raise ValueError("--metric-ks에는 양의 정수를 하나 이상 넣어야 합니다.")
    return sorted(set(values))


def read_csv(path: Path) -> list[dict[str, str]]:
    """UTF-8 BOM이 있어도 읽을 수 있게 CSV를 dict 목록으로 읽는다."""
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    """dict row 목록을 CSV 파일로 저장한다."""
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def load_questions(path: Path, query_limit: int | None) -> list[GoldQuery]:
    """골드셋 질문 CSV에서 평가 대상 질문을 읽는다."""
    queries: list[GoldQuery] = []
    for row in read_csv(path):
        query_id = compact_text(row.get("query_id"))
        query = compact_text(row.get("query"))
        if not query_id or not query:
            continue
        queries.append(
            GoldQuery(
                query_id=query_id,
                query=query,
                dispute_type=compact_text(row.get("dispute_type")),
                query_specificity=compact_text(row.get("query_specificity")),
                scenario_tags=compact_text(row.get("scenario_tags")),
            )
        )
        if query_limit is not None and len(queries) >= query_limit:
            break
    return queries


def load_gold_labels(path: Path) -> dict[str, dict[str, int]]:
    """골드 라벨 CSV를 query_id -> precedent_id -> relevance 구조로 읽는다."""
    labels: dict[str, dict[str, int]] = defaultdict(dict)
    for row in read_csv(path):
        query_id = compact_text(row.get("query_id"))
        precedent_id = compact_text(row.get("precedent_id"))
        if not query_id or not precedent_id:
            continue
        try:
            relevance = int(compact_text(row.get("relevance")) or "0")
        except ValueError:
            relevance = 0
        labels[query_id][precedent_id] = relevance
    return dict(labels)


def load_eval_chroma_collection(
    vector_db_root: Path,
    chunking_strategy: str,
    embedding_key: str,
) -> tuple[Any, dict[str, Any]]:
    """청킹 전략명과 임베딩 이름으로 Chroma 컬렉션과 manifest를 연다."""
    try:
        import chromadb
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "벡터DB 검색에는 chromadb가 필요합니다. 설치 뒤 다시 실행해주세요."
        ) from exc

    db_dir = vector_db_root / f"{chunking_strategy}__{embedding_key}"
    manifest_path = db_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"벡터DB manifest를 찾지 못했습니다: {manifest_path}")

    manifest = read_json(manifest_path)
    manifest["db_dir"] = str(db_dir)
    client = chromadb.PersistentClient(path=str(db_dir / "chroma"))
    return client.get_collection(manifest["collection"]), manifest


def rank_precedents(search_result: dict[str, Any]) -> list[RetrievedPrecedent]:
    """검색된 청크를 precedent_id 기준으로 묶고 판례 단위 순위를 만든다."""
    pooled: dict[str, RetrievedPrecedent] = {}
    ids = search_result.get("ids", [[]])[0]
    metadatas = search_result.get("metadatas", [[]])[0]
    distances = search_result.get("distances", [[]])[0]

    for index, chunk_id in enumerate(ids):
        metadata = metadatas[index] or {}
        precedent_id = compact_text(
            metadata.get("precedent_id") or metadata.get("source_case_id")
        )
        if not precedent_id:
            continue
        candidate = pooled.setdefault(
            precedent_id,
            RetrievedPrecedent(precedent_id=precedent_id),
        )
        candidate.add_chunk(
            chunk_id=compact_text(chunk_id),
            chunk_type=compact_text(metadata.get("section") or metadata.get("chunk_type")),
            chunk_rank=index + 1,
            distance=distances[index] if index < len(distances) else None,
        )

    return sorted(
        pooled.values(),
        key=lambda item: (
            item.best_chunk_rank,
            item.best_distance if item.best_distance is not None else 10**9,
            item.precedent_id,
        ),
    )


def dcg(relevances: list[int]) -> float:
    """관련도 목록으로 DCG를 계산한다."""
    score = 0.0
    for index, relevance in enumerate(relevances, start=1):
        gain = (2**relevance) - 1
        discount = math.log2(index + 1)
        score += gain / discount
    return score


def compute_metrics(
    ranked_precedents: list[RetrievedPrecedent],
    gold_relevance: dict[str, int],
    metric_ks: list[int],
) -> dict[str, Any]:
    """판례 단위 검색 결과와 골드 라벨을 비교해 지표를 계산한다."""
    relevant_gold = {
        precedent_id: relevance
        for precedent_id, relevance in gold_relevance.items()
        if relevance >= RELEVANT_THRESHOLD
    }
    ranked_ids = [item.precedent_id for item in ranked_precedents]
    metrics: dict[str, Any] = {
        "gold_count": len(relevant_gold),
        "retrieved_precedent_count": len(ranked_ids),
    }

    first_relevant_rank: int | None = None
    for rank, precedent_id in enumerate(ranked_ids, start=1):
        if relevant_gold.get(precedent_id, 0) >= RELEVANT_THRESHOLD:
            first_relevant_rank = rank
            break

    max_k = max(metric_ks)
    metrics[f"mrr@{max_k}"] = (
        round(1 / first_relevant_rank, 6)
        if first_relevant_rank is not None and first_relevant_rank <= max_k
        else 0.0
    )

    for k in metric_ks:
        top_ids = ranked_ids[:k]
        found_ids = [precedent_id for precedent_id in top_ids if precedent_id in relevant_gold]
        found_count = len(set(found_ids))
        gold_count = max(len(relevant_gold), 1)
        retrieved_relevances = [gold_relevance.get(precedent_id, 0) for precedent_id in top_ids]
        ideal_relevances = sorted(relevant_gold.values(), reverse=True)[:k]
        ideal_score = dcg(ideal_relevances)

        metrics[f"found@{k}"] = found_count
        metrics[f"recall@{k}"] = round(found_count / gold_count, 6)
        metrics[f"hit@{k}"] = 1 if found_count > 0 else 0
        metrics[f"precision@{k}"] = round(found_count / k, 6)
        metrics[f"ndcg@{k}"] = (
            round(dcg(retrieved_relevances) / ideal_score, 6)
            if ideal_score > 0
            else 0.0
        )

    return metrics


def average(values: list[float]) -> float:
    """빈 목록을 안전하게 처리하며 평균을 계산한다."""
    if not values:
        return 0.0
    return round(sum(values) / len(values), 6)


def summarize_metrics(
    model_key: str,
    query_rows: list[dict[str, Any]],
    metric_ks: list[int],
    elapsed_seconds: float,
) -> dict[str, Any]:
    """질문별 지표를 모델 단위 요약 지표로 집계한다."""
    summary: dict[str, Any] = {
        "embedding": model_key,
        "query_count": len(query_rows),
        "elapsed_seconds": round(elapsed_seconds, 2),
        "avg_seconds_per_query": round(elapsed_seconds / max(len(query_rows), 1), 4),
    }
    max_k = max(metric_ks)
    summary[f"mrr@{max_k}"] = average([float(row[f"mrr@{max_k}"]) for row in query_rows])
    for k in metric_ks:
        for metric_name in ("recall", "hit", "precision", "ndcg"):
            column = f"{metric_name}@{k}"
            summary[column] = average([float(row[column]) for row in query_rows])
        summary[f"avg_found@{k}"] = average([float(row[f"found@{k}"]) for row in query_rows])
    return summary


def write_manifest(
    output_dir: Path,
    args: argparse.Namespace,
    embeddings: list[str],
    metric_ks: list[int],
    summaries: list[dict[str, Any]],
) -> None:
    """평가 실행 조건과 요약 결과를 manifest로 저장한다."""
    manifest = {
        "schema_version": "precedent_embedding_eval_manifest.v1",
        "created_at": now_utc_iso(),
        "questions_csv": str(args.questions_csv),
        "labels_csv": str(args.labels_csv),
        "vector_db_root": str(args.vector_db_root),
        "output_dir": str(output_dir),
        "chunking_strategy": args.chunking_strategy,
        "embeddings": embeddings,
        "chunk_top_k": args.chunk_top_k,
        "metric_ks": metric_ks,
        "relevant_threshold": RELEVANT_THRESHOLD,
        "summaries": summaries,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """골드셋 질문을 3개 임베딩 벡터DB에 검색하고 검색 지표를 계산한다."""
    args = parse_args()
    load_dotenv()

    embeddings = parse_embedding_keys(args.embeddings)
    metric_ks = parse_metric_ks(args.metric_ks)
    output_dir = make_strategy_output_dir(args.output_dir, args.chunking_strategy)

    questions = load_questions(args.questions_csv, args.query_limit)
    gold_labels = load_gold_labels(args.labels_csv)
    if not questions:
        raise ValueError("평가할 질문이 없습니다.")

    print(
        f"판례 임베딩 평가 시작: 질문 {len(questions)}개, "
        f"strategy={args.chunking_strategy}, chunk_top_k={args.chunk_top_k}, "
        f"metrics={metric_ks}",
        flush=True,
    )

    all_query_rows: list[dict[str, Any]] = []
    all_retrieved_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    for model_index, embedding_key in enumerate(embeddings, start=1):
        collection, manifest = load_eval_chroma_collection(
            args.vector_db_root,
            args.chunking_strategy,
            embedding_key,
        )
        provider = manifest["embedding_provider"]
        model_name = manifest["embedding_model"]
        embedder = (
            load_sentence_transformer(model_name)
            if provider == "sentence_transformers"
            else load_openai_client()
        )

        print(
            f"[{model_index}/{len(embeddings)}] {embedding_key} 평가 시작: {model_name}",
            flush=True,
        )
        model_start = time.time()
        model_query_rows: list[dict[str, Any]] = []

        for query_index, query in enumerate(questions, start=1):
            query_start = time.time()
            query_vector = embed_query(provider, embedder, model_name, query.query)
            search_result = search_collection(collection, query_vector, args.chunk_top_k)
            ranked_precedents = rank_precedents(search_result)
            metrics = compute_metrics(
                ranked_precedents=ranked_precedents,
                gold_relevance=gold_labels.get(query.query_id, {}),
                metric_ks=metric_ks,
            )

            query_row: dict[str, Any] = {
                "embedding": embedding_key,
                "query_id": query.query_id,
                "dispute_type": query.dispute_type,
                "query_specificity": query.query_specificity,
                "query": query.query,
                "gold_count": metrics["gold_count"],
                "retrieved_precedent_count": metrics["retrieved_precedent_count"],
                "elapsed_seconds": round(time.time() - query_start, 4),
            }
            for key, value in metrics.items():
                if key not in query_row:
                    query_row[key] = value
            model_query_rows.append(query_row)
            all_query_rows.append(query_row)

            for rank, precedent in enumerate(ranked_precedents, start=1):
                relevance = gold_labels.get(query.query_id, {}).get(precedent.precedent_id, 0)
                all_retrieved_rows.append(
                    {
                        "embedding": embedding_key,
                        "query_id": query.query_id,
                        "query": query.query,
                        "rank": rank,
                        "precedent_id": precedent.precedent_id,
                        "relevance": relevance,
                        "is_relevant": 1 if relevance >= RELEVANT_THRESHOLD else 0,
                        "best_chunk_rank": precedent.best_chunk_rank,
                        "best_distance": precedent.best_distance,
                        "matched_chunk_count": len(precedent.chunk_ids),
                        "chunk_ids": "|".join(precedent.chunk_ids),
                        "chunk_types": "|".join(sorted(set(precedent.chunk_types))),
                    }
                )

            print(
                f"  [{query_index}/{len(questions)}] {query.query_id} "
                f"후보판례 {len(ranked_precedents)}건 "
                f"recall@10={metrics.get('recall@10', 0)} "
                f"{query_row['elapsed_seconds']:.2f}초",
                flush=True,
            )

        elapsed = time.time() - model_start
        summary_rows.append(
            summarize_metrics(embedding_key, model_query_rows, metric_ks, elapsed)
        )

    query_fields = [
        "embedding",
        "query_id",
        "dispute_type",
        "query_specificity",
        "query",
        "gold_count",
        "retrieved_precedent_count",
        "elapsed_seconds",
    ]
    max_k = max(metric_ks)
    query_fields.extend([f"mrr@{max_k}"])
    for k in metric_ks:
        query_fields.extend(
            [
                f"found@{k}",
                f"recall@{k}",
                f"hit@{k}",
                f"precision@{k}",
                f"ndcg@{k}",
            ]
        )

    summary_fields = ["embedding", "query_count", "elapsed_seconds", "avg_seconds_per_query"]
    summary_fields.extend([f"mrr@{max_k}"])
    for k in metric_ks:
        summary_fields.extend(
            [
                f"recall@{k}",
                f"hit@{k}",
                f"precision@{k}",
                f"ndcg@{k}",
                f"avg_found@{k}",
            ]
        )

    retrieved_fields = [
        "embedding",
        "query_id",
        "query",
        "rank",
        "precedent_id",
        "relevance",
        "is_relevant",
        "best_chunk_rank",
        "best_distance",
        "matched_chunk_count",
        "chunk_ids",
        "chunk_types",
    ]

    write_csv(output_dir / "summary_metrics.csv", summary_rows, summary_fields)
    write_csv(output_dir / "query_metrics.csv", all_query_rows, query_fields)
    write_csv(output_dir / "retrieved_precedents.csv", all_retrieved_rows, retrieved_fields)
    write_manifest(output_dir, args, embeddings, metric_ks, summary_rows)

    print("\n완료")
    print(f"- output_dir: {output_dir}")
    print(f"- summary: {output_dir / 'summary_metrics.csv'}")
    print(f"- query_metrics: {output_dir / 'query_metrics.csv'}")
    print(f"- retrieved: {output_dir / 'retrieved_precedents.csv'}")
    print("\n요약")
    for row in summary_rows:
        print(
            f"- {row['embedding']}: "
            f"Recall@10={row.get('recall@10')} "
            f"nDCG@10={row.get('ndcg@10')} "
            f"MRR@10={row.get('mrr@10')} "
            f"Hit@10={row.get('hit@10')}",
        )


if __name__ == "__main__":
    main()
