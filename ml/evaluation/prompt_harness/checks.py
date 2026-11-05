# checks.py
"""
Description: 하네스가 쓰는 채점 규칙의 호환 경로이며 실제 구현은 ml.agent.output_validation에 있다.
서비스 실행 경로와 평가 하네스가 같은 규칙을 공유하도록 한 곳으로 모았다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 채점 규칙이 평가 하네스 안에만 있어 서비스 실행 중에는 쓰이지 않던 상태.
After:
    - 규칙 구현을 서비스 모듈로 옮기고 하네스는 같은 규칙을 가져다 씀.
"""

from ml.agent.output_validation import (  # noqa: F401
    CheckResult,
    ItemContext,
    evaluate_item,
    output_text,
)

__all__ = ["CheckResult", "ItemContext", "evaluate_item", "output_text"]
