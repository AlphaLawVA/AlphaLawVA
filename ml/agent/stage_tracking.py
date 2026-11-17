# stage_tracking.py
"""
Description: LangGraph 노드 실행 직전에 단계 이름을 알리고 노드별 소요 시간을 로그로 남긴다.
비동기 작업이 폴링 응답에 현재 단계를 표시할 수 있게 하며, 콜백이 없으면 동작이 바뀌지 않는다.
Author: ooheunsu
Date: 2026-11-17
Before:
    - 오래 걸리는 그래프 실행의 현재 단계와 단계별 소요 시간을 알 방법이 없는 상태.
After:
    - 노드 시작 시 on_stage 콜백이 호출되고 노드별 소요 시간이 서버 로그에 남음.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from functools import wraps
from time import perf_counter
from typing import Any


logger = logging.getLogger(__name__)

StageCallback = Callable[[str], None]


def staged_node(
    stage: str,
    node: Callable[[Any], Any],
    *,
    name: str,
) -> Callable[[Any], Any]:
    """노드를 감싸 시작 시 stage를 알리고 종료 시 소요 시간을 로그로 남긴다.

    콜백은 그래프 상태의 `on_stage` 키에서 읽는다. 콜백이 실패해도 그래프 실행은
    이어지며(진행 표시는 부가 기능이다) 요청 내용은 로그에 남기지 않는다.
    """

    @wraps(node)
    def run(state: Mapping[str, Any]) -> Any:
        notify = state.get("on_stage")
        if notify is not None:
            try:
                notify(stage)
            except Exception:  # noqa: BLE001 - 진행 표시 실패가 작업을 막으면 안 된다.
                logger.warning("단계 알림 실패: stage=%s", stage, exc_info=True)
        started = perf_counter()
        try:
            return node(state)
        finally:
            logger.info(
                "그래프 단계 소요: stage=%s node=%s elapsed=%.2fs",
                stage,
                name,
                perf_counter() - started,
            )

    return run
