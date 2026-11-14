# retrieval_eval.py
"""
Description: 법령 검색 정답 50문항으로 dense, BM25, RRF, 가중합 하이브리드, 최소 유사도, 인접 조문 확장 설정을
같은 후보에서 비교하고 local_data/에 결과를 저장한다. LLM을 호출하지 않으며 검색기 코드는 읽기만 한다.
Author: ooheunsu
Date: 2026-11-06
Before:
    - 법령 ChromaDB, BGE-M3 모델 캐시, 정답 JSONL(관련도 0~3)이 준비된 상태.
After:
    - local_data/rag_eval/retrieval/<실행ID>/에 config.json, results.json, summary.md가 생성됨.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from unittest.mock import patch

from ml.evaluation.rag_eval.retrieval_metrics import (
    DEFAULT_KS,
    POSITIVE_THRESHOLD,
    evaluate_rankings,
    paired_bootstrap_difference,
    query_metrics_at_k,
)

DEFAULT_DEPTH = 50
RRF_K = 60  # ml.rag.statute_retriever.RRF_K와 같은 값
WEIGHTED_ALPHAS = (0.1, 0.2, 0.5, 0.8)  # BM25 가중치. 0.8은 논문(JKSCI 2025) 최적값, 0.1·0.2는 탐색용
MIN_SIMILARITIES = (0.25, 0.35, 0.5)  # 0.35는 서비스 기본값
SERVICE_TOP_K = 3
BASELINE_METHOD = "dense"
DEFAULT_OUTPUT_ROOT = Path("local_data/rag_eval/retrieval")


class CandidateSource(Protocol):
    """질문별 dense·BM25 후보와 코퍼스 순서를 제공하는 최소 인터페이스다."""

    def dense(self, queries: Sequence[str], depth: int) -> list[list[tuple[str, float]]]: ...

    def bm25(
        self, query: str, depth: int, tokenizer: str = "word"
    ) -> list[tuple[str, float]]: ...

    def corpus_ids(self) -> list[str]: ...


@dataclass(frozen=True)
class QueryCandidates:
    dense: list[tuple[str, float]]  # (chunk_id, 코사인 유사도)
    bm25: list[tuple[str, float]]  # 서비스 BM25(공백 단위 단어 토큰)
    bm25_ngram: list[tuple[str, float]]  # 글자 2-gram 토큰 BM25(평가 전용 변형)


def load_gold(path: Path) -> list[dict[str, Any]]:
    cases = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not cases:
        raise ValueError(f"정답 데이터가 비어 있습니다: {path}")
    return cases


def collect_candidates(
    source: CandidateSource,
    queries: Mapping[str, str],
    depth: int = DEFAULT_DEPTH,
) -> dict[str, QueryCandidates]:
    ids = sorted(queries)
    dense_batches = source.dense([queries[i] for i in ids], depth)
    return {
        query_id: QueryCandidates(
            dense=dense,
            bm25=source.bm25(queries[query_id], depth),
            bm25_ngram=source.bm25(queries[query_id], depth, "bigram"),
        )
        for query_id, dense in zip(ids, dense_batches, strict=True)
    }


def rank_dense(c: QueryCandidates) -> list[str]:
    return [chunk for chunk, _ in c.dense]


def rank_bm25(c: QueryCandidates) -> list[str]:
    return [chunk for chunk, _ in c.bm25]


def rank_bm25_ngram(c: QueryCandidates) -> list[str]:
    return [chunk for chunk, _ in c.bm25_ngram]


def rank_rrf(c: QueryCandidates, *, ngram: bool = False) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in (c.dense, c.bm25_ngram if ngram else c.bm25):
        for rank, (chunk, _) in enumerate(ranking, start=1):
            scores[chunk] = scores.get(chunk, 0.0) + 1.0 / (RRF_K + rank)
    return [chunk for chunk, _ in sorted(scores.items(), key=lambda x: (-x[1], x[0]))]


def rank_weighted(c: QueryCandidates, alpha: float, *, ngram: bool = False) -> list[str]:
    """alpha*BM25(min-max) + (1-alpha)*dense((cos+1)/2)로 합친다.

    후보 목록에 없는 쪽 점수는 그 목록의 최저 점수로 둔다(근사). 논문은 전체 청크를 점수화했다.
    """

    lexical = c.bm25_ngram if ngram else c.bm25
    if not c.dense and not lexical:
        return []
    bm_scores = dict(lexical)
    dense_scores = {chunk: (sim + 1) / 2 for chunk, sim in c.dense}
    bm_low = min(bm_scores.values(), default=0.0)
    bm_span = (max(bm_scores.values(), default=0.0) - bm_low) or 1.0
    dense_floor = min(dense_scores.values(), default=0.0)
    final: dict[str, float] = {}
    for chunk in set(bm_scores) | set(dense_scores):
        bm = (bm_scores[chunk] - bm_low) / bm_span if chunk in bm_scores else 0.0
        final[chunk] = alpha * bm + (1 - alpha) * dense_scores.get(chunk, dense_floor)
    return [chunk for chunk, _ in sorted(final.items(), key=lambda x: (-x[1], x[0]))]


def method_rankers() -> dict[str, Callable[[QueryCandidates], list[str]]]:
    rankers: dict[str, Callable[[QueryCandidates], list[str]]] = {
        "dense": rank_dense,
        "bm25": rank_bm25,
        "rrf": rank_rrf,
    }
    for alpha in WEIGHTED_ALPHAS:
        rankers[f"weighted_a{alpha}"] = lambda c, a=alpha: rank_weighted(c, a)
    rankers["bm25_ngram"] = rank_bm25_ngram
    rankers["rrf_ngram"] = lambda c: rank_rrf(c, ngram=True)
    for alpha in WEIGHTED_ALPHAS:
        rankers[f"weighted_ngram_a{alpha}"] = lambda c, a=alpha: rank_weighted(c, a, ngram=True)
    return rankers


def dense_with_min_similarity(
    c: QueryCandidates, *, top_k: int, min_similarity: float
) -> list[str]:
    """서비스 `ContractReviewRagRetriever`처럼 상위 k개 중 최소 유사도 이상만 남긴다."""

    return [chunk for chunk, sim in c.dense[:top_k] if sim >= min_similarity]


def expand_with_neighbors(
    ranked: Sequence[str], corpus_ids: Sequence[str], top_k: int
) -> list[str]:
    """상위 k개 뒤에 코퍼스 순서상 앞뒤 청크를 덧붙인다(논문의 슬라이딩 윈도우)."""

    position = {chunk: index for index, chunk in enumerate(corpus_ids)}
    head = list(ranked[:top_k])
    extra: list[str] = []
    for chunk in head:
        index = position.get(chunk)
        if index is None:
            continue
        for neighbor in (index - 1, index + 1):
            if 0 <= neighbor < len(corpus_ids):
                candidate = corpus_ids[neighbor]
                if candidate not in head and candidate not in extra:
                    extra.append(candidate)
    return head + extra


def build_labels(cases: Sequence[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return {
        case["query_id"]: {j["chunk_id"]: int(j["relevance"]) for j in case["judgments"]}
        for case in cases
    }


def run_retrieval_eval(
    cases: Sequence[dict[str, Any]],
    source: CandidateSource,
    *,
    depth: int = DEFAULT_DEPTH,
    ks: Sequence[int] = DEFAULT_KS,
) -> dict[str, Any]:
    labels = build_labels(cases)
    queries = {case["query_id"]: case["question"] for case in cases}
    candidates = collect_candidates(source, queries, depth)
    corpus = source.corpus_ids()

    method_rankings = {
        name: {q: ranker(candidates[q]) for q in labels}
        for name, ranker in method_rankers().items()
    }
    methods = {
        name: evaluate_rankings(rankings, labels, ks)
        for name, rankings in method_rankings.items()
    }

    bootstrap: dict[str, dict[str, dict[str, float]]] = {}
    base = methods[BASELINE_METHOD]["per_query"]
    for name, result in methods.items():
        if name == BASELINE_METHOD:
            continue
        bootstrap[name] = {}
        for k, metric in ((5, "recall"), (10, "ndcg")):
            if k not in ks:
                continue
            bootstrap[name][f"{metric}@{k}"] = paired_bootstrap_difference(
                {q: row[k][metric] for q, row in result["per_query"].items()},
                {q: row[k][metric] for q, row in base.items()},
            )

    service: dict[str, dict[str, float]] = {}
    for min_similarity in MIN_SIMILARITIES:
        rows = [
            query_metrics_at_k(
                dense_with_min_similarity(
                    candidates[q], top_k=SERVICE_TOP_K, min_similarity=min_similarity
                ),
                labels[q],
                SERVICE_TOP_K,
            )
            for q in sorted(labels)
        ]
        service[f"dense_k{SERVICE_TOP_K}_min{min_similarity}"] = {
            name: sum(row[name] for row in rows) / len(rows) for name in rows[0]
        }

    expansion: dict[str, dict[str, float]] = {}
    for name in ("dense", "rrf", "rrf_ngram"):
        expanded = {
            q: expand_with_neighbors(method_rankings[name][q], corpus, SERVICE_TOP_K)
            for q in labels
        }
        rows = [
            query_metrics_at_k(expanded[q], labels[q], max(len(expanded[q]), 1))
            for q in sorted(labels)
        ]
        expansion[f"{name}_k{SERVICE_TOP_K}+neighbors"] = {
            "recall": sum(r["recall"] for r in rows) / len(rows),
            "hit": sum(r["hit"] for r in rows) / len(rows),
            "returned": sum(r["returned"] for r in rows) / len(rows),
        }

    output = {
        "query_count": len(labels),
        "methods": {
            name: {"macro": result["macro"], "per_query": result["per_query"]}
            for name, result in methods.items()
        },
        "bootstrap_vs_dense": bootstrap,
        "service_view": service,
        "neighbor_expansion": expansion,
        "rankings_top10": {
            name: {q: ids[:10] for q, ids in rankings.items()}
            for name, rankings in method_rankings.items()
        },
    }
    return json.loads(json.dumps(output))  # 저장본과 같은 형태(키는 문자열)로 맞춘다.


def render_summary(config: Mapping[str, Any], results: Mapping[str, Any]) -> str:
    pct = lambda v: f"{v * 100:.1f}%"  # noqa: E731
    lines = [
        f"# 법령 검색 설정 비교 {config['run_id']}",
        "",
        f"- 정답 데이터: {config['dataset']} (sha256 {config['dataset_sha256']}), {results['query_count']}문항",
        f"- 정답 기준: 관련도 {POSITIVE_THRESHOLD} 이상 / 후보 깊이 {config['depth']} / 모델 {config['embedding_model']}",
        "- bm25: 서비스 현재 토큰화(공백 단위 단어) / *_ngram: 글자 2-gram 토큰화(평가 전용 변형)",
        "- context precision은 정답 위치별 Precision을 평균한 순위 가중 정밀도(AP@k)",
        "",
        "## 방식별 비교 (질문 평균)",
        "",
        "| 방식 | Recall@3 | Recall@5 | Recall@10 | CtxPrec@5 | MRR@10 | nDCG@10 | Hit@3 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, result in results["methods"].items():
        m = result["macro"]
        lines.append(
            f"| {name} | {pct(m['3']['recall'])} | {pct(m['5']['recall'])} | {pct(m['10']['recall'])} "
            f"| {pct(m['5']['context_precision'])} | {m['10']['mrr']:.3f} | {m['10']['ndcg']:.3f} "
            f"| {pct(m['3']['hit'])} |"
        )
    lines += [
        "",
        "## dense 대비 차이 (95% 신뢰구간, 질문 단위 bootstrap)",
        "",
        "| 방식 | Recall@5 차이 | nDCG@10 차이 |",
        "|---|---|---|",
    ]
    for name, rows in results["bootstrap_vs_dense"].items():
        cells = []
        for key in ("recall@5", "ndcg@10"):
            r = rows.get(key)
            cells.append("-" if r is None else f"{r['mean_diff']:+.3f} [{r['ci_low']:+.3f}, {r['ci_high']:+.3f}]")
        lines.append(f"| {name} | {cells[0]} | {cells[1]} |")
    lines += [
        "",
        "신뢰구간이 0을 포함하면 50문항에서는 우연과 구분되지 않는 차이다.",
        "",
        "## 서비스 방식 (dense 상위 3개 + 최소 유사도)",
        "",
        "| 설정 | 평균 반환 수 | 빈 결과 비율 | Recall | Hit |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, m in results["service_view"].items():
        lines.append(
            f"| {name} | {m['returned']:.2f} | {pct(m['empty'])} | {pct(m['recall'])} | {pct(m['hit'])} |"
        )
    lines += [
        "",
        "## 인접 조문 확장 (상위 3개 + 앞뒤 청크)",
        "",
        "| 설정 | 평균 반환 수 | Recall | Hit |",
        "|---|---:|---:|---:|",
    ]
    for name, m in results["neighbor_expansion"].items():
        lines.append(f"| {name} | {m['returned']:.1f} | {pct(m['recall'])} | {pct(m['hit'])} |")
    lines += [
        "",
        "## 한계",
        "",
        "- 정답 50문항은 일반 사용자 질문 스타일이며 서비스의 계약서 항목 질의와 분포가 다르다.",
        "- 법령 청크만 평가했다. 판례 검색은 별도 비교가 필요하다.",
        "- 가중합은 후보(깊이 상한) 안에서만 점수를 합치는 근사다.",
        "- RRF 유사도 값은 코사인과 단위가 달라, 서비스의 최소 유사도(0.35)를 그대로 쓰면 결과가 모두 걸러진다.",
    ]
    return "\n".join(lines) + "\n"


def write_outputs(
    output_dir: Path, config: Mapping[str, Any], results: Mapping[str, Any]
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    (output_dir / "summary.md").write_text(render_summary(config, results), encoding="utf-8")


_NGRAM_PATTERN = re.compile(r"[가-힣A-Za-z0-9]+")


def bigram_tokens(value: str) -> tuple[str, ...]:
    """조사가 붙은 한국어 단어도 겹치도록 글자 2-gram으로 토큰화한다(평가 전용)."""

    tokens: list[str] = []
    for word in _NGRAM_PATTERN.findall(value.lower()):
        if len(word) == 1:
            tokens.append(word)
        else:
            tokens.extend(word[i : i + 2] for i in range(len(word) - 1))
    return tuple(tokens)


class LiveStatuteSource:
    """BGE-M3 법령 검색기(읽기 전용)를 CandidateSource로 맞춘다."""

    def __init__(self, retriever: Any) -> None:
        self._retriever = retriever
        self._ngram_index: Any | None = None

    def dense(self, queries: Sequence[str], depth: int) -> list[list[tuple[str, float]]]:
        batches = self._retriever.search_many(list(queries), top_k=depth)
        return [[(r.chunk_id, r.similarity) for r in batch] for batch in batches]

    def bm25(
        self, query: str, depth: int, tokenizer: str = "word"
    ) -> list[tuple[str, float]]:
        from ml.rag import statute_retriever

        index = self._retriever._load_bm25_index()  # 공개 API가 점수를 노출하지 않아 읽기만 한다.
        if tokenizer == "word":
            return [(r.chunk_id, r.similarity) for r in index.search(query, top_k=depth)]
        # 서비스 코드는 바꾸지 않고, 평가 중에만 토큰화 함수를 바꿔 별도 색인을 만든다.
        with patch.object(statute_retriever, "_bm25_tokens", bigram_tokens):
            if self._ngram_index is None:
                self._ngram_index = statute_retriever._StatuteBm25Index(index.documents)
            results = self._ngram_index.search(query, top_k=depth)
        return [(r.chunk_id, r.similarity) for r in results]

    def corpus_ids(self) -> list[str]:
        return sorted(doc.chunk_id for doc in self._retriever._load_bm25_index().documents)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True, help="정답 JSONL 경로")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--depth", type=int, default=DEFAULT_DEPTH)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    args = parser.parse_args()

    from ml.rag.statute_retriever import BgeM3StatuteRetriever

    cases = load_gold(args.dataset)
    retriever = BgeM3StatuteRetriever(device=args.device)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    results = run_retrieval_eval(cases, LiveStatuteSource(retriever), depth=args.depth)
    config = {
        "run_id": run_id,
        "dataset": str(args.dataset),
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest()[:16],
        "embedding_model": "BAAI/bge-m3",
        "chunking_strategy": "article_with_overflow_split_v01",
        "depth": args.depth,
        "positive_threshold": POSITIVE_THRESHOLD,
        "rrf_k": RRF_K,
        "weighted_alphas": list(WEIGHTED_ALPHAS),
        "ngram_bm25": "character_bigram(eval-only)",
        "min_similarities": list(MIN_SIMILARITIES),
        "llm_calls": 0,
    }
    output_dir = args.output_dir or DEFAULT_OUTPUT_ROOT / run_id
    write_outputs(output_dir, config, results)
    print(f"완료: {output_dir}")


if __name__ == "__main__":
    main()
