# precedent_case_repository.py
"""
Description: final_cases의 판례 JSON을 판례일련번호로 안전하게 읽고 검증한다.
경로 조작과 손상되거나 식별자가 다른 원본 데이터 사용을 차단한다.
Author: choeminju
Date: 2026-10-13
Before:
    - API 계층에서 final_cases 판례 JSON을 일관되게 읽는 저장소가 없는 상태.
After:
    - 검증된 판례일련번호로 원본 판례 데이터를 읽는 저장소가 제공됨.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FINAL_CASES_DIR = (
    PROJECT_ROOT / "local_data" / "precedents" / "processed" / "final_cases"
)


class PrecedentCaseRepositoryError(Exception):
    """Base error for final_cases data access."""


class InvalidPrecedentIdError(PrecedentCaseRepositoryError):
    """Raised when a precedent ID cannot safely identify a JSON file."""


class PrecedentCaseNotFoundError(PrecedentCaseRepositoryError):
    """Raised when a requested final_cases JSON file does not exist."""


class PrecedentCaseDataError(PrecedentCaseRepositoryError):
    """Raised when a final_cases JSON file is unreadable or inconsistent."""


class PrecedentCaseRepository:
    def __init__(self, cases_dir: Path = DEFAULT_FINAL_CASES_DIR) -> None:
        self.cases_dir = cases_dir

    def get(self, precedent_id: str) -> dict[str, Any]:
        normalized_id = self._validate_precedent_id(precedent_id)
        case_path = self.cases_dir / f"{normalized_id}.json"
        if not case_path.is_file():
            raise PrecedentCaseNotFoundError(
                f"판례 데이터를 찾을 수 없습니다: {normalized_id}"
            )

        try:
            case_data = json.loads(case_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise PrecedentCaseDataError(
                f"판례 데이터를 읽을 수 없습니다: {normalized_id}"
            ) from error

        if not isinstance(case_data, dict):
            raise PrecedentCaseDataError(
                f"판례 데이터 형식이 올바르지 않습니다: {normalized_id}"
            )

        stored_id = case_data.get("판례일련번호")
        if str(stored_id) != normalized_id:
            raise PrecedentCaseDataError(
                f"판례 데이터 식별자가 일치하지 않습니다: {normalized_id}"
            )
        return case_data

    @staticmethod
    def _validate_precedent_id(precedent_id: str) -> str:
        normalized_id = precedent_id.strip()
        if not normalized_id or not normalized_id.isascii() or not normalized_id.isdigit():
            raise InvalidPrecedentIdError("판례일련번호는 ASCII 숫자만 허용됩니다.")
        return normalized_id
