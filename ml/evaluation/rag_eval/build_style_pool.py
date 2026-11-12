# build_style_pool.py
"""
Description: 특약 요청·계약서 검토 질의(서비스 입력 스타일)의 법령 검색 후보 풀을 만든다.
dense 상위 20개와 리랭커 재정렬 상위 10개를 합쳐 질문별 후보를 만들고, AI 관련도 판정에 쓸 블라인드 풀과
두 방식의 순위를 local_data/에 저장한다. LLM을 호출하지 않으며 검색기와 서비스 코드는 읽기만 한다.
Author: ooheunsu
Date: 2026-11-12
Before:
    - 법령 ChromaDB, BGE-M3 모델 캐시, 리랭커 모델 폴더, cases_v02.json이 준비된 상태.
After:
    - <출력폴더>/blind_pool.jsonl(순서와 방식 정보를 숨긴 후보 풀)과 rankings.json이 생성됨.
"""

from __future__ import annotations

import argparse
import gc
import json
import random
import time
from pathlib import Path
from typing import Any

from ml.evaluation.rag_eval.rerank_eval import DEFAULT_CASES, passage_text

DENSE_POOL = 20
RERANK_POOL = 10
RERANK_CANDIDATES = 30
SEED = "style-pool-v1"


def style_queries(cases_path: Path) -> list[dict[str, str]]:
    """서비스 입력과 같은 형태의 질의(접두어 포함)를 모은다. 프롬프트 주입 시험 질의는 뺀다."""

    data = json.loads(cases_path.read_text(encoding="utf-8"))
    rows: list[dict[str, str]] = []
    for kind in ("clause_cases", "review_cases"):
        for case in data[kind]:
            for index, item in enumerate(case["items"], start=1):
                query = item["query"]
                if "INJECTED" in query or "이전 지시를 모두 무시" in query:
                    continue
                rows.append(
                    {
                        "query_id": f"{case['id']}#{index}",
                        "question": query,
                        "kind": kind,
                        "category": item["category"],
                    }
                )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reranker-path", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0, help="앞에서 N개 질의만 처리(속도 시험용)")
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()

    import torch
    from sentence_transformers import CrossEncoder

    from ml.rag.statute_retriever import BgeM3StatuteRetriever

    queries = style_queries(args.cases)
    if args.limit:
        queries = queries[: args.limit]
    retriever = BgeM3StatuteRetriever(device="cpu")
    dense = retriever.search_many([q["question"] for q in queries], top_k=50)
    del retriever
    gc.collect()

    model = CrossEncoder(
        str(args.reranker_path),
        device="cpu",
        max_length=512,
        model_kwargs={"torch_dtype": torch.float32},
    )
    print(f"리랭커 로드 완료: dtype={next(model.model.parameters()).dtype}", flush=True)

    rankings: dict[str, Any] = {}
    chunks: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()
    for done, (query, results) in enumerate(zip(queries, dense, strict=True), start=1):
        head = results[:RERANK_CANDIDATES]
        scores = model.predict(
            [(query["question"], passage_text(r)) for r in head],
            batch_size=args.batch_size,
            show_progress_bar=False,
        )
        order = [r.chunk_id for r, _ in sorted(zip(head, scores), key=lambda p: float(p[1]), reverse=True)]
        rankings[query["query_id"]] = {
            "dense": [r.chunk_id for r in results],
            "reranked": order + [r.chunk_id for r in results[RERANK_CANDIDATES:]],
        }
        for r in results:
            chunks[r.chunk_id] = {
                "law_name": r.law_name or "",
                "article_label": r.article_label or "",
                "article_title": r.article_title or "",
                "retrieval_text": r.text,
            }
        elapsed = time.perf_counter() - started
        print(f"후보 풀 {done}/{len(queries)} 질문, 경과 {elapsed:.0f}초, 남은 시간 약 {elapsed / done * (len(queries) - done):.0f}초", flush=True)

    rng = random.Random(SEED)
    blind_rows = []
    for query in queries:
        rank = rankings[query["query_id"]]
        pool = list(dict.fromkeys(rank["dense"][:DENSE_POOL] + rank["reranked"][:RERANK_POOL]))
        rng.shuffle(pool)  # 순위·방식 정보를 숨기려고 섞는다.
        blind_rows.append(
            {
                "query_id": query["query_id"],
                "question": query["question"],
                "purpose": "서비스 입력 스타일 질의에 필요한 법령 근거 검색 평가",
                "category": query["category"],
                "critical": False,
                "candidates": [
                    {"candidate_id": f"cand-{i:02d}", "chunk_id": cid, **chunks[cid]}
                    for i, cid in enumerate(pool, start=1)
                ],
            }
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "rankings.json").write_text(json.dumps(rankings, ensure_ascii=False), encoding="utf-8")
    (args.output_dir / "blind_pool.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in blind_rows) + "\n", encoding="utf-8"
    )
    sizes = [len(r["candidates"]) for r in blind_rows]
    print(f"완료: 질문 {len(queries)}개, 후보 합계 {sum(sizes)}건(질문당 {min(sizes)}~{max(sizes)}건)", flush=True)


if __name__ == "__main__":
    main()
