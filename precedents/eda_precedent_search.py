# eda_precedent_search.py
"""
Description: 자연어 질문 여러 개로 현재 판례 검색 결과를 간단히 살펴본다.
판례별 메타데이터, 요약 미리보기, 유사도와 최고 적중 청크를 터미널에 출력한다.
Author: choeminju
Date: 2026-10-14
Before:
    - BGE-M3 모델 캐시, 판례 ChromaDB, final_cases 데이터가 준비된 상태.
After:
    - 기본 질문 또는 명령행 질문별 판례 검색 결과를 빠르게 비교할 수 있음.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.models.precedents import PrecedentSearchRequest  # noqa: E402
from api.services.gemini_similarity_explainer import (  # noqa: E402
    create_gemini_similarity_explainer_from_env,
)
from api.services.precedent_search_service import (  # noqa: E402
    PrecedentSearchService,
)


# 질문을 직접 바꿔가며 결과를 비교하면 됩니다.
QUESTIONS = [
    "집주인이 개인회생 면책을 받은 뒤에도 전세보증금을 돌려주지 않았습니다.",
    "집주인이 바뀌었는데 새 집주인에게도 임대차계약을 주장할 수 있나요?",
    "살고 있는 집이 경매로 넘어가면 보증금을 우선해서 돌려받을 수 있나요?",
    "임대인이 누수 수리를 해주지 않아 계약을 해지하고 싶습니다.",
    "오늘 저녁 메뉴를 추천해 주세요.",
]

DEFAULT_TOP_K = 3
PREVIEW_LENGTH = 220


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="현재 BGE-M3 판례 검색 결과를 질문별로 확인합니다.",
    )
    parser.add_argument(
        "queries",
        nargs="*",
        help="검색할 질문. 생략하면 파일의 QUESTIONS를 사용합니다.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"질문별 판례 결과 수. 기본값: {DEFAULT_TOP_K}",
    )
    return parser.parse_args()


def compact(text: str | None, limit: int = PREVIEW_LENGTH) -> str:
    if not text:
        return "-"
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3]}..."


def print_query_results(
    service: PrecedentSearchService,
    query: str,
    top_k: int,
) -> None:
    response = service.search(
        PrecedentSearchRequest(query=query, limit=top_k),
    )

    print("\n" + "=" * 88)
    print(f"질문: {query}")
    print(f"상태: {response.status.value} | 판례 수: {response.result_count}")
    if response.message:
        print(f"안내: {response.message}")

    for item in response.results:
        precedent = item.precedent
        detail = service.get_detail(response.search_id, precedent.precedent_id)
        best_chunk = detail.similarity_explanation.matched_chunks[0]
        explanation = detail.similarity_explanation
        if service.similarity_explainer is not None:
            explanation = service.generate_similarity_explanation(
                response.search_id,
                precedent.precedent_id,
            )

        print("-" * 88)
        print(
            f"{item.rank}. {precedent.case_number or '-'} | "
            f"{precedent.case_name or '-'} | 유사도 {item.similarity_score:.4f}"
        )
        print(
            f"   법원/선고일: {precedent.court_name or '-'} / "
            f"{precedent.decision_date or '-'}"
        )
        print(
            f"   요약({item.preview.source.value if item.preview.source else '-'}): "
            f"{compact(item.preview.summary)}"
        )
        print(
            f"   최고 적중 청크({best_chunk.chunk_type or best_chunk.section or '-'}, "
            f"{best_chunk.similarity_score:.4f}): {compact(best_chunk.text)}"
        )
        print(
            "   유사한 이유: "
            f"{compact(explanation.matched_reason)}"
        )
        print(f"   공식 원문: {item.links.official_source}")


def main() -> None:
    args = parse_args()
    if not 1 <= args.top_k <= 20:
        raise SystemExit("--top-k는 1 이상 20 이하여야 합니다.")

    queries = args.queries or QUESTIONS
    service = PrecedentSearchService(
        similarity_explainer=create_gemini_similarity_explainer_from_env(),
    )
    for query in queries:
        print_query_results(service, query, args.top_k)


if __name__ == "__main__":
    main()
