# judge_style_pool.py
"""
Description: build_style_pool.py가 만든 블라인드 후보 풀을 기존 법령 관련도 판정 지침(statute-relevance-v0.1)으로
AI가 질문별로 판정한다. 호출 수·누적 비용을 출력하고 비용 한도를 넘으면 멈추며, 끝난 질문은 건너뛰고 이어서 실행한다.
Author: ooheunsu
Date: 2026-11-12
Before:
    - blind_pool.jsonl과 .env의 OPENAI_API_KEY가 준비된 상태(유료 호출).
After:
    - 출력 폴더에 질문별 판정(judgments.jsonl)과 사용량(usage.jsonl)이 쌓임.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ml.evaluation.review_statute_retrieval_pool import (
    SYSTEM_INSTRUCTIONS,
    QuestionAssessment,
    build_review_input,
    load_openai_client,
    read_jsonl,
    validate_assessment,
    validate_blind_pool,
)

# 1M 토큰당 가격(USD). gpt-6.1-terra는 OpenAI 공식 가격표(2026-11-06 확인)이다.
PRICES = {"gpt-6.1-terra": (1.0, 5.0)}
AUTH_RETRIES = 6  # 이 키는 401이 간헐적으로 나며 실패 호출은 과금되지 않는다.


def review_case_chat(client: Any, case: dict[str, Any], model: str) -> tuple[dict, dict]:
    """기존 판정 지침을 Chat Completions로 호출한다(이 키는 Responses API 권한이 없다)."""

    response = client.beta.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_INSTRUCTIONS},
            {"role": "user", "content": build_review_input(case)},
        ],
        response_format=QuestionAssessment,
        max_completion_tokens=16_000,
    )
    parsed = response.choices[0].message.parsed
    if parsed is None:
        raise ValueError(f"구조화된 응답이 없습니다: {case['query_id']}")
    validate_assessment(case, parsed)
    usage = {
        "input_tokens": response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
        "response_id": response.id,
    }
    result = parsed.model_dump()
    chunk_ids = {c["candidate_id"]: c["chunk_id"] for c in case["candidates"]}
    for assessment in result["assessments"]:
        assessment["chunk_id"] = chunk_ids[assessment["candidate_id"]]
    return result, usage


def call_with_retry(client: Any, case: dict[str, Any], model: str) -> tuple[dict, dict]:
    from openai import AuthenticationError

    last: Exception | None = None
    for attempt in range(1, AUTH_RETRIES + 1):
        try:
            return review_case_chat(client, case, model)
        except AuthenticationError as error:  # 간헐적 401
            if "Missing scopes" in str(error):  # 권한 부족은 재시도해도 같다
                raise RuntimeError(f"권한 부족: {case['query_id']}") from error
            last = error
            time.sleep(2 * attempt)
        except ValueError as error:  # 응답 구조 불일치는 한 번만 더 시도한다.
            last = error
            if attempt >= 2:
                break
    raise RuntimeError(f"판정 실패: {case['query_id']} ({type(last).__name__})")


def cost_usd(model: str, usage: dict[str, Any]) -> float:
    price_in, price_out = PRICES[model]
    return (usage.get("input_tokens", 0) * price_in + usage.get("output_tokens", 0) * price_out) / 1e6


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6.1-terra", choices=sorted(PRICES))
    parser.add_argument("--limit", type=int, default=0, help="앞에서 N개 질문만 판정(소량 시험용)")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-cost-usd", type=float, default=0.5)
    parser.add_argument("--confirm-live", action="store_true", help="유료 호출 확인 플래그")
    args = parser.parse_args()
    if not args.confirm_live:
        raise SystemExit("유료 호출입니다. --confirm-live를 붙여 실행하세요.")

    rows = read_jsonl(args.pool)
    validate_blind_pool(rows)
    if args.limit:
        rows = rows[: args.limit]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    judgments_path = args.output_dir / "judgments.jsonl"
    usage_path = args.output_dir / "usage.jsonl"
    done = {r["query_id"] for r in read_jsonl(judgments_path)} if judgments_path.exists() else set()
    todo = [r for r in rows if r["query_id"] not in done]
    spent = sum(cost_usd(args.model, u) for u in read_jsonl(usage_path)) if usage_path.exists() else 0.0
    candidates = sum(len(r["candidates"]) for r in todo)
    print(f"판정 대상 {len(todo)}질문(후보 {candidates}건), 이미 완료 {len(done)}질문, 누적 비용 ${spent:.3f}", flush=True)

    client = load_openai_client()
    started = time.perf_counter()
    finished = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for case in todo:
            if spent >= args.max_cost_usd:
                print(f"비용 한도 ${args.max_cost_usd} 도달, 남은 질문은 보내지 않는다.", flush=True)
                break
            futures[pool.submit(call_with_retry, client, case, args.model)] = case["query_id"]
        for future in as_completed(futures):
            query_id = futures[future]
            try:
                result, usage = future.result()
            except RuntimeError as error:
                print(f"실패(건너뜀): {error}", flush=True)
                continue
            spent += cost_usd(args.model, usage)
            with judgments_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
            with usage_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"query_id": query_id, **usage}, ensure_ascii=False) + "\n")
            finished += 1
            print(
                f"판정 {finished}/{len(futures)} 질문 완료({query_id}), 누적 비용 ${spent:.3f}, "
                f"경과 {time.perf_counter() - started:.0f}초",
                flush=True,
            )
    print(f"종료: 이번 실행 {finished}질문, 누적 비용 ${spent:.3f}", flush=True)


if __name__ == "__main__":
    main()
