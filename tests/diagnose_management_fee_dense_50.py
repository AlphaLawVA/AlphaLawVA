# diagnose_management_fee_dense_50.py
"""
Description: 관리비 대납·미지급 내용증명 질의에서 BGE-M3 판례 Dense 후보 50건을
조회해 목표 판례가 후보군에 포함되는지 확인하는 로컬 진단 도구다.
Author: choeminju
Date: 2026-11-16
Before:
    - ALPHALAWVA_PRECEDENT_DB_DIR과 BGE-M3 모델 캐시가 로컬 환경 변수에 설정된 상태.
After:
    - 목표 판례의 후보 포함 여부와 Dense 순위를 표준 출력으로 확인할 수 있음.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


shared_env_file = os.environ.get("ALPHALAWVA_SHARED_ENV_FILE", "").strip()
if shared_env_file:
    load_dotenv(Path(shared_env_file), override=False)

from ml.rag.precedent_retriever import BgeM3PrecedentRetriever


BASE_QUERY = (
    "전세 임대차 11월분 관리비 지급 요구 계약서에는 관리비를 정액 월 180,000원으로 "
    "정하고 있습니다. 임차인은 10월 31일에 입주했고, 11월 관리비 180,000원은 "
    "제가 관리사무소에 대신 납부했습니다. 11월 10일 문자로 이 금액을 보내 달라고 "
    "요청했지만 아직 입금이 없습니다."
)
QUERY_WITH_ISSUE_TERM = f"{BASE_QUERY} 관리비 미납"
TARGET_PRECEDENT_ID = "147076"
TARGET_CASE_NUMBER = "2005가합100279,2006가합62053"
CANDIDATE_LIMIT = 50


def print_result(label: str, query: str, results) -> None:
    target = next(
        (result for result in results if result.precedent_id == TARGET_PRECEDENT_ID),
        None,
    )
    print(f"\n[{label}]")
    print(f"질의: {query}")
    print(f"서로 다른 판례 후보 수: {len(results)}")
    if target is None:
        print(f"목표 판례 미포함: {TARGET_CASE_NUMBER} ({TARGET_PRECEDENT_ID})")
    else:
        print(
            "목표 판례 포함: "
            f"순위 {target.rank}, 유사도 {target.similarity:.4f}, "
            f"사건번호 {target.case_no}"
        )

    for result in results:
        print(
            f"{result.rank:>2}. {result.precedent_id} | {result.case_no} | "
            f"{result.similarity:.4f} | {result.case_name}"
        )


def main() -> None:
    retriever = BgeM3PrecedentRetriever()
    result_batches = retriever.search_many(
        (BASE_QUERY, QUERY_WITH_ISSUE_TERM),
        top_k=CANDIDATE_LIMIT,
    )
    print_result("기준 검색", BASE_QUERY, result_batches[0])
    print_result("쟁점어 보강 검색", QUERY_WITH_ISSUE_TERM, result_batches[1])


if __name__ == "__main__":
    main()
