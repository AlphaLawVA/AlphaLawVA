"""관리비 질의의 법령 Dense 후보 10건을 비교하는 로컬 진단 도구다."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


shared_env_file = os.environ.get("ALPHALAWVA_SHARED_ENV_FILE", "").strip()
if shared_env_file:
    load_dotenv(Path(shared_env_file), override=True)

from ml.rag.statute_retriever import BgeM3StatuteRetriever


BASE_QUERY = (
    "전세 임대차 11월분 관리비 지급 요구 계약서에는 관리비를 정액 월 180,000원으로 "
    "정하고 있습니다. 임차인은 10월 31일에 입주했고, 11월 관리비 180,000원은 "
    "제가 관리사무소에 대신 납부했습니다. 11월 10일 문자로 이 금액을 보내 달라고 "
    "요청했지만 아직 입금이 없습니다."
)
GEMINI_KEYWORDS = "관리비 청구 대납 관리비 전세 임대차 관리비 미납"
QUERY_WITH_GEMINI_KEYWORDS = f"{BASE_QUERY} {GEMINI_KEYWORDS}"
CANDIDATE_LIMIT = 10


def print_results(label: str, query: str, results: list[object]) -> None:
    print(f"\n[{label}]")
    print(f"질의: {query}")
    for result in results:
        print(
            f"{result.rank:>2}. {result.similarity:.4f} | "
            f"{result.law_name} {result.article_label or ''} | "
            f"{result.article_title or ''}"
        )


def main() -> None:
    retriever = BgeM3StatuteRetriever(
        db_dir=Path(os.environ["CERTIFIED_LETTER_STATUTE_DB_DIR"]),
        model_cache=Path(os.environ["CERTIFIED_LETTER_MODEL_CACHE_DIR"]),
    )
    baseline, with_keywords = retriever.search_many(
        (BASE_QUERY, QUERY_WITH_GEMINI_KEYWORDS),
        top_k=CANDIDATE_LIMIT,
    )
    print_results("기준 검색", BASE_QUERY, baseline)
    print_results("Gemini 쟁점어 보강 검색", QUERY_WITH_GEMINI_KEYWORDS, with_keywords)


if __name__ == "__main__":
    main()
