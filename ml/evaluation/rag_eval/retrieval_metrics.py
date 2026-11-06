# retrieval_metrics.py
"""
Description: 정답 라벨이 있는 질문별 검색 순위로 Recall, Precision, context precision(AP), MRR, nDCG를 계산하고
기준 설정과의 차이를 paired bootstrap으로 추정한다. LLM을 호출하지 않는다.
Author: ooheunsu
Date: 2026-11-06
Before:
    - 질문별 검색 순위(chunk_id 목록)와 관련도 0~3 라벨이 준비된 상태.
After:
    - k별 질문 평균 지표와 기준 대비 차이의 95% 신뢰구간을 반환함.
"""

from __future__ import annotations

import math
import random
from typing import Mapping, Sequence

POSITIVE_THRESHOLD = 2  # 기존 50문항 평가(statute-50-v01)와 같은 정답 기준
DEFAULT_KS = (3, 5, 10)


def _dcg(relevances: Sequence[int]) -> float:
    return sum(
        (2**relevance - 1) / math.log2(rank + 1)
        for rank, relevance in enumerate(relevances, start=1)
    )


def query_metrics_at_k(
    ranked_chunk_ids: Sequence[str],
    judgments: Mapping[str, int],
    k: int,
) -> dict[str, float]:
    """한 질문의 상위 k개 결과 지표를 계산한다.

    context_precision은 RAGAS context precision과 같은 발상(정답 위치마다 Precision@위치를
    더함)이다. 분모는 min(정답 수, k)인 AP@k이며, 정답 라벨로 계산하므로 LLM 호출이 필요 없다.
    """

    relevant = {c for c, r in judgments.items() if r >= POSITIVE_THRESHOLD}
    if not relevant:
        raise ValueError("관련도 2 이상인 정답 청크가 없습니다.")
    top = list(dict.fromkeys(ranked_chunk_ids))[:k]  # 중복 제거 후 상위 k
    hits = [chunk in relevant for chunk in top]
    found = sum(hits)
    precision_sum, seen = 0.0, 0
    for rank, hit in enumerate(hits, start=1):
        if hit:
            seen += 1
            precision_sum += seen / rank
    first = next((rank for rank, hit in enumerate(hits, start=1) if hit), None)
    ideal = sorted(judgments.values(), reverse=True)[:k]
    ideal_dcg = _dcg(ideal)
    gains = [judgments.get(chunk, 0) for chunk in top]
    return {
        "recall": found / len(relevant),
        "precision": found / k,
        "context_precision": precision_sum / min(len(relevant), k),
        "mrr": 0.0 if first is None else 1 / first,
        "ndcg": 0.0 if ideal_dcg == 0 else _dcg(gains) / ideal_dcg,
        "hit": float(found > 0),
        "returned": float(len(top)),
        "empty": float(len(top) == 0),
    }


def evaluate_rankings(
    rankings: Mapping[str, Sequence[str]],
    labels: Mapping[str, Mapping[str, int]],
    ks: Sequence[int] = DEFAULT_KS,
) -> dict[str, object]:
    """질문별 지표와 k별 평균(macro)을 반환한다."""

    if set(rankings) != set(labels):
        raise ValueError("검색 순위와 정답 라벨의 질문 집합이 다릅니다.")
    per_query = {
        query_id: {k: query_metrics_at_k(rankings[query_id], labels[query_id], k) for k in ks}
        for query_id in sorted(labels)
    }
    macro: dict[int, dict[str, float]] = {}
    for k in ks:
        names = next(iter(per_query.values()))[k].keys()
        macro[k] = {
            name: sum(rows[k][name] for rows in per_query.values()) / len(per_query)
            for name in names
        }
    return {"per_query": per_query, "macro": macro}


def paired_bootstrap_difference(
    candidate: Mapping[str, float],
    baseline: Mapping[str, float],
    *,
    samples: int = 2000,
    seed: int = 20260106,
) -> dict[str, float]:
    """질문별 점수 차이의 평균과 95% 신뢰구간을 구한다(질문 단위 재표집)."""

    if set(candidate) != set(baseline):
        raise ValueError("비교할 질문 집합이 다릅니다.")
    diffs = [candidate[q] - baseline[q] for q in sorted(candidate)]
    rng = random.Random(seed)
    means = sorted(
        sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(samples)
    )
    return {
        "mean_diff": sum(diffs) / len(diffs),
        "ci_low": means[int(0.025 * samples)],
        "ci_high": means[int(0.975 * samples) - 1],
    }
