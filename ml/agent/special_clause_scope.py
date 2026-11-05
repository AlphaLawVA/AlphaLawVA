# special_clause_scope.py
"""
Description: 특약 생성 요청이 부동산 계약 특약과 관련 있는지 규칙으로 먼저 분류한다.
무관한 요청과 너무 짧은 요청은 검색·생성 모델을 호출하기 전에 일찍 분기한다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 부동산 계약과 무관한 요청도 검색과 생성 모델까지 그대로 전달되던 상태.
After:
    - 범위 안, 정보 부족, 범위 밖 세 갈래로 나누고 고정 안내 문구를 제공함.
"""

from __future__ import annotations

import re
from enum import Enum


class ScopeDecision(str, Enum):
    IN_SCOPE = "in_scope"
    NEEDS_INFORMATION = "needs_information"
    OUT_OF_SCOPE = "out_of_scope"


OUT_OF_SCOPE_MESSAGE = "부동산 계약 특약과 관련된 요청만 도와드릴 수 있어요."
TOO_SHORT_QUESTION = "어떤 상황의 특약인지 한 문장 이상으로 알려주세요."

# 부동산 계약·특약 요청에 자주 나오는 낱말. 하나라도 있으면 범위 안으로 본다.
_DOMAIN_KEYWORDS = (
    "특약", "계약", "임대", "임차", "매도", "매수", "매매", "전세", "월세", "보증금",
    "차임", "관리비", "잔금", "중도금", "계약금", "위약금", "집주인", "세입자", "주택",
    "아파트", "오피스텔", "빌라", "건물", "부동산", "등기", "근저당", "저당", "담보",
    "대출", "갱신", "수리", "수선", "하자", "누수", "원상", "복구", "입주", "퇴거",
    "명도", "전입", "확정일자", "보증보험", "반환", "해제", "해지", "도배", "장판",
    "보일러", "체납", "반려", "이사", "권리 변동", "권리변동",
)
_MIN_COMPACT_LENGTH = 4


def classify_request_scope(request: str) -> ScopeDecision:
    """요청 한 건을 범위 밖, 정보 부족, 범위 안 중 하나로 분류한다."""

    compact = re.sub(r"[\s?!.,]", "", request)
    if len(compact) < _MIN_COMPACT_LENGTH:
        return ScopeDecision.NEEDS_INFORMATION
    if not any(keyword in request for keyword in _DOMAIN_KEYWORDS):
        return ScopeDecision.OUT_OF_SCOPE
    return ScopeDecision.IN_SCOPE
