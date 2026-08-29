# precedent_keywords.py
"""
Description: AlphaLawVA 판례 수집에 사용할 주거용 부동산 매매·임대차 키워드를 관리한다.
회의에서 정한 키워드 묶음과 실제 판례 목록 API 요청용 검색어를 함께 둔다.
Author: choeminju
Date: 2026-08-11
Before:
    - 판례 수집 검색어가 코드 밖 자료에만 흩어져 있는 상태.

After:
    - 사건명 검색어와 본문 검색어를 스크립트에서 재사용 가능한 상수로 제공.
"""

from __future__ import annotations

from typing import Any


ALL = ["매매", "전세", "월세"]
SALE = ["매매"]
LEASE = ["전세", "월세"]
JEONSE = ["전세"]
MONTHLY = ["월세"]
OUT = ["범위 외"]

KEYWORDS_V01: dict[str, list[dict[str, Any]]] = {
    "common": [
        {
            "level": "핵심",
            "areas": ALL,
            "terms": [
                "주거용 부동산",
                "주택 매매",
                "부동산 매매",
                "주택임대차",
                "임대차계약",
                "전세계약",
                "월세계약",
                "임대차보증금",
            ],
        },
        {
            "level": "조합",
            "areas": ALL,
            "terms": [
                "부동산",
                "주택",
                "아파트",
                "공동주택",
                "단독주택",
                "다가구주택",
                "다세대주택",
                "연립주택",
            ],
        },
        {
            "level": "확장",
            "areas": ALL,
            "terms": ["빌라", "원룸", "투룸", "주거용 오피스텔", "임대주택"],
        },
    ],
    "law_specific": [
        {
            "level": "핵심",
            "areas": LEASE,
            "terms": [
                "주거용 건물",
                "임차주택",
                "주택의 인도",
                "주민등록",
                "확정일자",
                "대항력",
                "우선변제권",
                "최우선변제",
                "임차권등기명령",
                "계약갱신요구권",
                "묵시적 갱신",
            ],
        },
        {
            "level": "핵심",
            "areas": ALL,
            "terms": [
                "부동산 거래 신고",
                "주택 임대차 계약 신고",
                "중개대상물 확인·설명",
                "거래계약서 작성·교부",
                "소유권이전등기",
            ],
        },
        {
            "level": "조합",
            "areas": LEASE,
            "terms": [
                "임대인 지위 승계",
                "보증금 중 일정액",
                "차임 증감청구",
                "월차임 전환",
                "배당요구",
                "경매",
                "공매",
            ],
        },
        {
            "level": "조합",
            "areas": ALL,
            "terms": [
                "권리관계",
                "하자보수",
                "담보책임",
                "관리비",
                "장기수선충당금",
                "계약의 해제",
                "손해배상",
            ],
        },
    ],
    "precedent_specific": [
        {
            "level": "핵심",
            "areas": LEASE,
            "terms": [
                "임대차보증금반환",
                "보증금반환",
                "건물인도",
                "건물명도",
                "임차권등기명령",
                "임대인 지위 승계",
            ],
        },
        {
            "level": "핵심",
            "areas": SALE,
            "terms": [
                "소유권이전등기",
                "매매대금",
                "매매대금반환",
                "계약금반환",
                "소유권이전등기말소",
            ],
        },
        {
            "level": "조합",
            "areas": ALL,
            "terms": [
                "동시이행",
                "채무불이행",
                "계약해제",
                "원상회복",
                "손해배상",
                "부당이득",
                "착오",
                "사기",
                "기망",
            ],
        },
        {
            "level": "조합",
            "areas": ALL,
            "terms": [
                "설명의무",
                "주의의무",
                "중개사 과실",
                "확인의무",
                "배당이의",
                "사해행위취소",
                "점유이전금지가처분",
            ],
        },
    ],
    "everyday": [
        {
            "level": "확장",
            "areas": LEASE,
            "terms": [
                "집주인",
                "세입자",
                "전세금",
                "월세 보증금",
                "보증금을 못 받음",
                "보증금 떼임",
                "전세사기",
                "깡통전세",
                "계약 연장",
                "묵시적 연장",
            ],
        },
        {
            "level": "확장",
            "areas": SALE,
            "terms": [
                "집을 삼",
                "집을 팖",
                "계약금 돌려받기",
                "잔금을 못 받음",
                "집주인이 바뀜",
                "등기를 안 해줌",
            ],
        },
        {
            "level": "확장",
            "areas": ALL,
            "terms": [
                "계약 파기",
                "이중계약",
                "대리계약",
                "불리한 특약",
                "등기부등본",
                "근저당이 있는 집",
                "압류된 집",
            ],
        },
    ],
    "legal_terms": [
        {
            "level": "핵심",
            "areas": LEASE,
            "terms": [
                "대항력",
                "우선변제권",
                "최우선변제권",
                "확정일자",
                "임차권등기",
                "임대차 존속기간",
                "계약갱신요구권",
            ],
        },
        {
            "level": "조합",
            "areas": ALL,
            "terms": [
                "의사표시",
                "착오취소",
                "사기취소",
                "채무불이행",
                "이행지체",
                "이행불능",
                "동시이행항변권",
                "계약해제",
                "해약금",
                "위약금",
                "손해배상",
                "원상회복",
            ],
        },
        {
            "level": "조합",
            "areas": SALE,
            "terms": [
                "매도인의 담보책임",
                "권리의 하자",
                "물건의 하자",
                "소유권 이전",
                "이중매매",
            ],
        },
    ],
    "contract_terms": [
        {
            "level": "핵심",
            "areas": ALL,
            "terms": [
                "계약당사자",
                "매도인",
                "매수인",
                "임대인",
                "임차인",
                "대리인",
                "계약금",
                "중도금",
                "잔금",
                "특약사항",
            ],
        },
        {
            "level": "핵심",
            "areas": LEASE,
            "terms": [
                "임차보증금",
                "차임",
                "월차임",
                "임대차기간",
                "입주일",
                "전입신고",
                "보증금 반환",
            ],
        },
        {
            "level": "조합",
            "areas": ALL,
            "terms": [
                "소재지",
                "지목",
                "건물구조",
                "용도",
                "면적",
                "대지권",
                "인도일",
                "제세공과금",
                "관리비",
                "수선",
                "하자",
                "계약 해제",
                "중개보수",
            ],
        },
    ],
    "claim_types": [
        {
            "level": "핵심",
            "areas": LEASE,
            "terms": [
                "임대차보증금",
                "임대차보증금반환청구",
                "건물인도청구",
                "건물명도청구",
            ],
        },
        {
            "level": "핵심",
            "areas": SALE,
            "terms": [
                "소유권이전등기청구",
                "매매대금청구",
                "계약금반환청구",
                "소유권이전등기말소청구",
            ],
        },
        {
            "level": "조합",
            "areas": ALL,
            "terms": [
                "손해배상청구",
                "부당이득반환청구",
                "채무부존재확인",
                "배당이의",
                "사해행위취소",
                "가처분",
                "가압류",
            ],
        },
    ],
    "rights_and_registration": [
        {
            "level": "핵심",
            "areas": ALL,
            "terms": [
                "소유권",
                "소유권이전등기",
                "근저당권",
                "저당권",
                "전세권",
                "임차권등기",
                "가압류",
                "가처분",
                "압류",
            ],
        },
        {
            "level": "조합",
            "areas": ALL,
            "terms": [
                "가등기",
                "말소등기",
                "신탁등기",
                "공동담보",
                "채권최고액",
                "등기명의인",
                "처분제한",
                "권리변동",
            ],
        },
        {
            "level": "확장",
            "areas": SALE,
            "terms": ["대지권", "대지권비율", "구분소유권", "공유지분"],
        },
    ],
    "law_names": [
        {
            "level": "핵심",
            "areas": ALL,
            "terms": [
                "민법",
                "공인중개사법",
                "부동산등기법",
                "부동산 거래신고 등에 관한 법률",
            ],
        },
        {"level": "핵심", "areas": LEASE, "terms": ["주택임대차보호법", "주택임대차보호법 시행령"]},
        {
            "level": "조합",
            "areas": ALL,
            "terms": [
                "집합건물의 소유 및 관리에 관한 법률",
                "공동주택관리법",
                "주택법",
                "주민등록법",
            ],
        },
        {
            "level": "확장",
            "areas": ALL,
            "terms": [
                "민사집행법",
                "부동산 실권리자명의 등기에 관한 법률",
                "신탁법",
                "건축법",
            ],
        },
        {
            "level": "확장",
            "areas": LEASE,
            "terms": [
                "민간임대주택에 관한 특별법",
                "전세사기피해자 지원 및 주거안정에 관한 특별법",
                "주택도시기금법",
                "국세기본법",
                "지방세기본법",
                "국세징수법",
                "지방세징수법",
            ],
        },
    ],
    "low_priority_or_excluded": [
        {
            "level": "제외",
            "areas": OUT,
            "terms": ["상가건물", "상가임대차", "권리금", "영업용 건물", "공장", "창고", "산업단지"],
        },
        {
            "level": "제외",
            "areas": OUT,
            "terms": ["농지", "임야", "광업권", "어업권", "분묘기지권", "토지수용", "공익사업", "개발부담금"],
        },
        {
            "level": "제외",
            "areas": OUT,
            "terms": ["도시개발", "재개발", "재건축", "정비사업", "분양권", "입주권", "토지거래허가"],
        },
    ],
}

# 사건명 검색(search=1)에 바로 사용할 비교적 좁은 판례/청구 유형 키워드.
CASE_NAME_SEARCH_QUERIES = [
    "임대차보증금반환",
    "보증금반환",
    "보증금 반환",
    "전세보증금반환보증",
    "건물인도",
    "건물명도",
    "임차권등기명령",
    "소유권이전등기",
    "매매대금",
    "매매대금반환",
    "계약금반환",
    "소유권이전등기말소",
]

# 본문 검색(search=2)에 사용할 법적 쟁점 키워드.
BODY_SEARCH_QUERIES = [
    "대항력",
    "우선변제권",
    "최우선변제권",
    "확정일자",
    "임대인 지위 승계",
    "계약갱신요구권",
    "묵시적 갱신",
    "근저당권",
    "전세권",
    "가등기",
    "신탁등기",
    "중개사 과실",
    "중개대상물 확인·설명",
    "매도인의 담보책임",
    "이중매매",
]

# 단독 API 검색어로 쓰면 잡음이 너무 커지는 단어.
DO_NOT_COLLECT_DIRECTLY = [
    "부동산",
    "주택",
    "계약",
    "손해배상",
    "사기",
]


def get_collection_queries(smoke_test: bool = False) -> list[dict[str, object]]:
    """수집기가 순회할 검색어 목록을 search=1/search=2 형태로 반환한다."""
    case_name_queries = CASE_NAME_SEARCH_QUERIES[:1] if smoke_test else CASE_NAME_SEARCH_QUERIES
    body_queries = BODY_SEARCH_QUERIES[:1] if smoke_test else BODY_SEARCH_QUERIES

    searches: list[dict[str, object]] = []
    searches.extend(
        {
            "search_type": "case_name",
            "search": 1,
            "query": query,
            "source": "CASE_NAME_SEARCH_QUERIES",
        }
        for query in case_name_queries
    )
    searches.extend(
        {
            "search_type": "body",
            "search": 2,
            "query": query,
            "source": "BODY_SEARCH_QUERIES",
        }
        for query in body_queries
    )
    return searches
