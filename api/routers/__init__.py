# __init__.py
"""
Description: AlphaLawVA API 라우터 패키지를 정의한다.
기능별 HTTP 엔드포인트 모듈을 이 패키지에 배치한다.
Author: choeminju
Date: 2026-10-13
Before:
    - 기능별 API 라우터를 배치할 패키지가 없는 상태.
After:
    - 요청과 응답 처리를 담당하는 라우터 패키지가 제공됨.
"""

from api.routers.certified_letters import router as certified_letters_router
from api.routers.precedents import router as precedents_router

__all__ = ["certified_letters_router", "precedents_router"]
