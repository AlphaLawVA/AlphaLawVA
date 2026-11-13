# compare_precedent_bm25_hybrid.py
"""
Description: 동일한 판례 골든셋과 청크로 BGE-M3 Dense, Kiwi BM25,
Weighted RRF 하이브리드 검색의 Top-K 품질과 실행시간을 비교한다.
Author: choeminju
Date: 2026-11-13
Before:
    - 판례 검색 골든셋, BGE-M3 ChromaDB와 로컬 BGE-M3 모델 캐시가 존재.
After:
    - 검색 방식별 요약·질문별 지표·순위·Dense 대비 복구 결과가 출력 폴더에 생성.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml.evaluation.precedents.evaluate_precedent_embeddings import (  # noqa: E402
    GoldQuery,
    compute_metrics,
    load_gold_labels,
    load_questions,
)
from ml.rag.precedent_retriever import BgeM3PrecedentRetriever  # noqa: E402


LOCAL_DATA_ROOT = PROJECT_ROOT / "local_data"
DEFAULT_GOLDSET_DIR = LOCAL_DATA_ROOT / "precedents/evaluation/goldset_v01"
DEFAULT_QUESTIONS = (
    DEFAULT_GOLDSET_DIR / "precedent_retrieval_gold_questions_v01.csv"
)
DEFAULT_LABELS = DEFAULT_GOLDSET_DIR / "precedent_retrieval_gold_labels_v01.csv"
DEFAULT_OUTPUT = (
    LOCAL_DATA_ROOT / "precedents/evaluation/hybrid_runs/precedent_bm25_hybrid_v01"
)
DEFAULT_TOP_K = 20
DEFAULT_RRF_K = 60
DEFAULT_BM25_K1 = 1.5
DEFAULT_BM25_B = 0.75
RELEVANT_THRESHOLD = 2
HYBRID_WEIGHTS = (
    (0.9, 0.1),
    (0.8, 0.2),
    (0.7, 0.3),
    (0.5, 0.5),
)
KIWI_CONTENT_TAGS = {
    "NNG",
    "NNP",
    "NNB",
    "NR",
    "NP",
    "VV",
    "VA",
    "VX",
    "VCP",
    "VCN",
    "MM",
    "MAG",
    "MAJ",
    "XR",
    "XSN",
    "SL",
    "SN",
}


@dataclass(frozen=True)
class RankedPrecedent:
    precedent_id: str
    rank: int
    score: float
    best_chunk_id: str


@dataclass(frozen=True)
class CorpusChunk:
    chunk_id: str
    precedent_id: str
    text: str


class KiwiTokenizer:
    """조사·어미·문장부호를 제외하고 검색 의미가 있는 형태소만 남긴다."""

    def __init__(self) -> None:
        try:
            from kiwipiepy import Kiwi
        except ImportError as error:
            raise RuntimeError(
                "Kiwi BM25 실험에는 requirements-preprocessing.txt가 필요합니다."
            ) from error
        self._kiwi = Kiwi()

    def __call__(self, text: str) -> tuple[str, ...]:
        return tuple(
            token.form.casefold()
            for token in self._kiwi.tokenize(text)
            if token.tag in KIWI_CONTENT_TAGS and token.form.strip()
        )


class PrecedentBm25Index:
    """판례 청크의 역색인으로 BM25를 계산하고 판례별 최고 청크를 고른다."""

    def __init__(
        self,
        chunks: Sequence[CorpusChunk],
        *,
        tokenizer: Callable[[str], Sequence[str]],
        k1: float = DEFAULT_BM25_K1,
        b: float = DEFAULT_BM25_B,
    ) -> None:
        if not chunks:
            raise ValueError("BM25 색인을 만들 판례 청크가 없습니다.")
        if k1 <= 0 or not 0 <= b <= 1:
            raise ValueError("BM25 k1은 양수이고 b는 0~1이어야 합니다.")
        self.chunks = tuple(chunks)
        self.tokenizer = tokenizer
        self.k1 = k1
        self.b = b
        self.lengths: list[int] = []
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for index, chunk in enumerate(self.chunks):
            tokens = tuple(tokenizer(chunk.text))
            self.lengths.append(len(tokens))
            frequencies = Counter(tokens)
            for term, frequency in frequencies.items():
                self.postings[term].append((index, frequency))
        self.average_length = max(sum(self.lengths) / len(self.lengths), 1.0)

    def search(self, query: str, *, top_k: int) -> list[RankedPrecedent]:
        if top_k < 1:
            raise ValueError("top_k는 1 이상이어야 합니다.")
        query_terms = set(self.tokenizer(query))
        chunk_scores: dict[int, float] = defaultdict(float)
        document_count = len(self.chunks)
        for term in query_terms:
            postings = self.postings.get(term, ())
            document_frequency = len(postings)
            if not document_frequency:
                continue
            inverse_frequency = math.log(
                1.0
                + (document_count - document_frequency + 0.5)
                / (document_frequency + 0.5)
            )
            for index, frequency in postings:
                length_normalizer = 1.0 - self.b + self.b * (
                    self.lengths[index] / self.average_length
                )
                chunk_scores[index] += inverse_frequency * (
                    frequency * (self.k1 + 1.0)
                    / (frequency + self.k1 * length_normalizer)
                )

        best_by_precedent: dict[str, tuple[float, str]] = {}
        for index, score in chunk_scores.items():
            chunk = self.chunks[index]
            current = best_by_precedent.get(chunk.precedent_id)
            candidate = (score, chunk.chunk_id)
            if current is None or candidate > current:
                best_by_precedent[chunk.precedent_id] = candidate
        ranked = sorted(
            best_by_precedent.items(),
            key=lambda item: (-item[1][0], item[0]),
        )[:top_k]
        return [
            RankedPrecedent(
                precedent_id=precedent_id,
                rank=rank,
                score=score,
                best_chunk_id=chunk_id,
            )
            for rank, (precedent_id, (score, chunk_id)) in enumerate(
                ranked, start=1
            )
        ]


def weighted_rrf(
    dense: Sequence[RankedPrecedent],
    bm25: Sequence[RankedPrecedent],
    *,
    dense_weight: float,
    bm25_weight: float,
    top_k: int,
    rrf_k: int = DEFAULT_RRF_K,
) -> list[RankedPrecedent]:
    if top_k < 1 or rrf_k < 1:
        raise ValueError("top_k와 rrf_k는 1 이상이어야 합니다.")
    if dense_weight < 0 or bm25_weight < 0 or dense_weight + bm25_weight <= 0:
        raise ValueError("RRF 가중치는 음수가 아니고 합이 양수여야 합니다.")
    scores: dict[str, float] = defaultdict(float)
    chunks: dict[str, str] = {}
    best_source_rank: dict[str, int] = {}
    for weight, ranking in ((dense_weight, dense), (bm25_weight, bm25)):
        for item in ranking:
            scores[item.precedent_id] += weight / (rrf_k + item.rank)
            chunks.setdefault(item.precedent_id, item.best_chunk_id)
            best_source_rank[item.precedent_id] = min(
                best_source_rank.get(item.precedent_id, item.rank), item.rank
            )
    ranked_ids = sorted(
        scores,
        key=lambda precedent_id: (
            -scores[precedent_id],
            best_source_rank[precedent_id],
            precedent_id,
        ),
    )[:top_k]
    return [
        RankedPrecedent(
            precedent_id=precedent_id,
            rank=rank,
            score=scores[precedent_id],
            best_chunk_id=chunks[precedent_id],
        )
        for rank, precedent_id in enumerate(ranked_ids, start=1)
    ]


def evaluate_ranking(
    ranking: Sequence[RankedPrecedent],
    gold: dict[str, int],
    *,
    top_k: int,
) -> dict[str, Any]:
    return compute_metrics(list(ranking), gold, [top_k])


def load_corpus(collection: Any) -> list[CorpusChunk]:
    payload = collection.get(include=["documents", "metadatas"])
    ids = payload.get("ids", [])
    documents = payload.get("documents", [])
    metadatas = payload.get("metadatas", [])
    if not (len(ids) == len(documents) == len(metadatas)):
        raise ValueError("ChromaDB 청크 필드 길이가 일치하지 않습니다.")
    chunks = []
    for chunk_id, text, metadata in zip(ids, documents, metadatas, strict=True):
        metadata = metadata or {}
        precedent_id = str(
            metadata.get("precedent_id")
            or metadata.get("source_case_id")
            or _precedent_id_from_chunk_id(str(chunk_id))
            or ""
        ).strip()
        if precedent_id and str(text).strip():
            chunks.append(
                CorpusChunk(
                    chunk_id=str(chunk_id),
                    precedent_id=precedent_id,
                    text=str(text),
                )
            )
    if not chunks:
        raise ValueError("ChromaDB에서 판례 청크를 읽지 못했습니다.")
    return chunks


def _precedent_id_from_chunk_id(chunk_id: str) -> str | None:
    parts = chunk_id.split(":")
    return parts[1] if len(parts) >= 3 and parts[0] == "precedent" else None


def convert_dense_batches(batches: Sequence[Sequence[Any]]) -> list[list[RankedPrecedent]]:
    return [
        [
            RankedPrecedent(
                precedent_id=item.precedent_id,
                rank=rank,
                score=float(item.similarity),
                best_chunk_id=(
                    item.matched_chunks[0].chunk_id if item.matched_chunks else ""
                ),
            )
            for rank, item in enumerate(batch, start=1)
        ]
        for batch in batches
    ]


def method_name(dense_weight: float, bm25_weight: float) -> str:
    return f"hybrid_d{dense_weight:.1f}_b{bm25_weight:.1f}"


def mean(values: Iterable[float]) -> float:
    items = list(values)
    return round(sum(items) / len(items), 6) if items else 0.0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="판례 Dense·BM25·Weighted RRF 검색을 90문항으로 비교합니다."
    )
    parser.add_argument("--questions-csv", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--labels-csv", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--db-dir", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--query-limit", type=int)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    return parser.parse_args()


def _summary(
    method: str,
    query_rows: list[dict[str, Any]],
    *,
    top_k: int,
    elapsed_seconds: float,
) -> dict[str, Any]:
    return {
        "method": method,
        "query_count": len(query_rows),
        f"recall@{top_k}": mean(float(row[f"recall@{top_k}"]) for row in query_rows),
        f"mrr@{top_k}": mean(float(row[f"mrr@{top_k}"]) for row in query_rows),
        f"ndcg@{top_k}": mean(float(row[f"ndcg@{top_k}"]) for row in query_rows),
        f"precision@{top_k}": mean(
            float(row[f"precision@{top_k}"]) for row in query_rows
        ),
        f"hit@{top_k}": mean(float(row[f"hit@{top_k}"]) for row in query_rows),
        "avg_found": mean(float(row[f"found@{top_k}"]) for row in query_rows),
        "elapsed_seconds": round(elapsed_seconds, 6),
        "avg_milliseconds_per_query": round(
            elapsed_seconds * 1000 / max(len(query_rows), 1), 3
        ),
    }


def _method_query_rows(
    method: str,
    questions: Sequence[GoldQuery],
    rankings: Sequence[Sequence[RankedPrecedent]],
    labels: dict[str, dict[str, int]],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    rows = []
    for question, ranking in zip(questions, rankings, strict=True):
        metrics = evaluate_ranking(
            ranking,
            labels.get(question.query_id, {}),
            top_k=top_k,
        )
        rows.append(
            {
                "method": method,
                "query_id": question.query_id,
                "dispute_type": question.dispute_type,
                "query_specificity": question.query_specificity,
                "query": question.query,
                **metrics,
            }
        )
    return rows


def _ranking_rows(
    method: str,
    questions: Sequence[GoldQuery],
    rankings: Sequence[Sequence[RankedPrecedent]],
    labels: dict[str, dict[str, int]],
) -> list[dict[str, Any]]:
    rows = []
    for question, ranking in zip(questions, rankings, strict=True):
        gold = labels.get(question.query_id, {})
        for item in ranking:
            relevance = gold.get(item.precedent_id, 0)
            rows.append(
                {
                    "method": method,
                    "query_id": question.query_id,
                    "rank": item.rank,
                    "precedent_id": item.precedent_id,
                    "relevance": relevance,
                    "is_relevant": int(relevance >= RELEVANT_THRESHOLD),
                    "score": round(item.score, 10),
                    "best_chunk_id": item.best_chunk_id,
                }
            )
    return rows


def _dense_comparison_rows(
    questions: Sequence[GoldQuery],
    methods: dict[str, Sequence[Sequence[RankedPrecedent]]],
    labels: dict[str, dict[str, int]],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    dense = methods["dense"]
    rows = []
    for method, rankings in methods.items():
        if method == "dense":
            continue
        for question, dense_ranking, ranking in zip(
            questions, dense, rankings, strict=True
        ):
            gold = {
                precedent_id
                for precedent_id, relevance in labels.get(question.query_id, {}).items()
                if relevance >= RELEVANT_THRESHOLD
            }
            dense_found = {item.precedent_id for item in dense_ranking[:top_k]} & gold
            method_found = {item.precedent_id for item in ranking[:top_k]} & gold
            rows.append(
                {
                    "method": method,
                    "query_id": question.query_id,
                    "dense_found": len(dense_found),
                    "method_found": len(method_found),
                    "found_delta": len(method_found) - len(dense_found),
                    "recovered_gold_ids": "|".join(sorted(method_found - dense_found)),
                    "lost_gold_ids": "|".join(sorted(dense_found - method_found)),
                }
            )
    return rows


def main() -> None:
    args = parse_args()
    if args.top_k < 1:
        raise ValueError("top_k는 1 이상이어야 합니다.")
    questions = load_questions(args.questions_csv, args.query_limit)
    labels = load_gold_labels(args.labels_csv)
    if not questions:
        raise ValueError("평가 질문이 없습니다.")

    retriever = BgeM3PrecedentRetriever(
        db_dir=args.db_dir,
        model_cache=args.model_cache,
        device=args.device,
    )
    started = time.perf_counter()
    retriever.load()
    dense_load_seconds = time.perf_counter() - started
    assert retriever._collection is not None

    started = time.perf_counter()
    corpus = load_corpus(retriever._collection)
    tokenizer = KiwiTokenizer()
    bm25_index = PrecedentBm25Index(corpus, tokenizer=tokenizer)
    bm25_build_seconds = time.perf_counter() - started

    query_texts = [question.query for question in questions]
    started = time.perf_counter()
    dense_batches = convert_dense_batches(
        retriever.search_many(query_texts, top_k=args.top_k)
    )
    dense_seconds = time.perf_counter() - started

    started = time.perf_counter()
    bm25_batches = [
        bm25_index.search(query, top_k=args.top_k) for query in query_texts
    ]
    bm25_seconds = time.perf_counter() - started

    methods: dict[str, list[list[RankedPrecedent]]] = {
        "dense": dense_batches,
        "bm25": bm25_batches,
    }
    method_seconds = {"dense": dense_seconds, "bm25": bm25_seconds}
    for dense_weight, bm25_weight in HYBRID_WEIGHTS:
        name = method_name(dense_weight, bm25_weight)
        started = time.perf_counter()
        methods[name] = [
            weighted_rrf(
                dense,
                bm25,
                dense_weight=dense_weight,
                bm25_weight=bm25_weight,
                top_k=args.top_k,
            )
            for dense, bm25 in zip(dense_batches, bm25_batches, strict=True)
        ]
        method_seconds[name] = (
            dense_seconds + bm25_seconds + time.perf_counter() - started
        )

    query_rows = []
    ranking_rows = []
    summaries = []
    for method, rankings in methods.items():
        rows = _method_query_rows(
            method,
            questions,
            rankings,
            labels,
            top_k=args.top_k,
        )
        query_rows.extend(rows)
        ranking_rows.extend(_ranking_rows(method, questions, rankings, labels))
        summaries.append(
            _summary(
                method,
                rows,
                top_k=args.top_k,
                elapsed_seconds=method_seconds[method],
            )
        )

    comparison_rows = _dense_comparison_rows(
        questions,
        methods,
        labels,
        top_k=args.top_k,
    )
    comparison_summary = {}
    for method in methods:
        if method == "dense":
            continue
        rows = [row for row in comparison_rows if row["method"] == method]
        comparison_summary[method] = {
            "recall_win_queries": sum(row["found_delta"] > 0 for row in rows),
            "recall_tie_queries": sum(row["found_delta"] == 0 for row in rows),
            "recall_loss_queries": sum(row["found_delta"] < 0 for row in rows),
            "recovered_gold_pairs": sum(
                len(row["recovered_gold_ids"].split("|"))
                for row in rows
                if row["recovered_gold_ids"]
            ),
            "lost_gold_pairs": sum(
                len(row["lost_gold_ids"].split("|"))
                for row in rows
                if row["lost_gold_ids"]
            ),
        }

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    metric_fields = [
        "method",
        "query_count",
        f"recall@{args.top_k}",
        f"mrr@{args.top_k}",
        f"ndcg@{args.top_k}",
        f"precision@{args.top_k}",
        f"hit@{args.top_k}",
        "avg_found",
        "elapsed_seconds",
        "avg_milliseconds_per_query",
    ]
    query_fields = [
        "method",
        "query_id",
        "dispute_type",
        "query_specificity",
        "query",
        "gold_count",
        "retrieved_precedent_count",
        f"mrr@{args.top_k}",
        f"found@{args.top_k}",
        f"recall@{args.top_k}",
        f"hit@{args.top_k}",
        f"precision@{args.top_k}",
        f"ndcg@{args.top_k}",
    ]
    write_csv(output_dir / "method_summary.csv", summaries, metric_fields)
    write_csv(output_dir / "query_metrics.csv", query_rows, query_fields)
    write_csv(
        output_dir / "rankings.csv",
        ranking_rows,
        [
            "method",
            "query_id",
            "rank",
            "precedent_id",
            "relevance",
            "is_relevant",
            "score",
            "best_chunk_id",
        ],
    )
    write_csv(
        output_dir / "dense_comparison.csv",
        comparison_rows,
        [
            "method",
            "query_id",
            "dense_found",
            "method_found",
            "found_delta",
            "recovered_gold_ids",
            "lost_gold_ids",
        ],
    )
    manifest = {
        "schema_version": "precedent_bm25_hybrid_comparison.v1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": {
            "questions_csv": str(args.questions_csv.resolve()),
            "questions_sha256": sha256(args.questions_csv),
            "labels_csv": str(args.labels_csv.resolve()),
            "labels_sha256": sha256(args.labels_csv),
            "query_count": len(questions),
            "relevant_threshold": RELEVANT_THRESHOLD,
        },
        "retrieval": {
            "db_dir": str(args.db_dir.resolve()),
            "model_cache": str(args.model_cache.resolve()),
            "device": args.device,
            "corpus_chunk_count": len(corpus),
            "top_k": args.top_k,
            "bm25": {
                "tokenizer": "kiwi_content_morphemes_v1",
                "content_tags": sorted(KIWI_CONTENT_TAGS),
                "k1": DEFAULT_BM25_K1,
                "b": DEFAULT_BM25_B,
            },
            "rrf_k": DEFAULT_RRF_K,
            "hybrid_weights": [
                {"dense": dense, "bm25": bm25}
                for dense, bm25 in HYBRID_WEIGHTS
            ],
        },
        "timing": {
            "dense_load_seconds": round(dense_load_seconds, 6),
            "bm25_build_seconds": round(bm25_build_seconds, 6),
        },
        "summaries": summaries,
        "dense_comparison": comparison_summary,
        "paid_api_calls": 0,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    print(json.dumps(comparison_summary, ensure_ascii=False, indent=2))
    print(f"결과 폴더: {output_dir}")


if __name__ == "__main__":
    main()
