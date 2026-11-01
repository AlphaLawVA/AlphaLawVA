# __init__.py
"""
Description: AlphaLawVA의 기능별 LangGraph 조합 계층을 정의한다.
규칙 검증, 검색, 생성 노드를 기능 단위로 분리해 배치한다.
Author: ooheunsu
Date: 2026-10-27
Before:
    - 계약서 검토의 상태 기반 실행 그래프를 둘 패키지가 없는 상태.
After:
    - 계약서 검토, 특약 생성, 내용증명 워크플로를 배치할 패키지가 제공됨.
"""
