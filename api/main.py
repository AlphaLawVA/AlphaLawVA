# main.py
"""
Description: AlphaLawVA FastAPI 애플리케이션을 생성하고 실행 진입점을 제공한다.
서버 상태 확인용 엔드포인트를 노출한다.
Author: choeminju
Date: 2026-10-13
Before:
    - api 패키지는 있지만 실행 가능한 FastAPI 애플리케이션이 없는 상태.
After:
    - api.main:app으로 서버를 실행하고 상태를 확인할 수 있음.
"""

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(
        title="AlphaLawVA API",
        description="AlphaLawVA backend API",
        version="0.1.0",
    )

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "AlphaLawVA"}

    return app


app = create_app()
