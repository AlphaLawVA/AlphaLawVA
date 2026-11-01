# privacy.py
"""
Description: OCR 텍스트에서 저장하면 안 되는 주민등록번호를 탐지하고
후속 구조화·AI 처리 전에 마스킹한다.
Author: ooheunsu
Date: 2026-10-05
Before:
    - OCR 결과에 계약서 원문의 고유식별정보가 포함될 수 있는 상태.
After:
    - 주민등록번호가 마스킹된 텍스트만 후속 처리에 전달됨.
"""

from __future__ import annotations

import re
from typing import Any


RESIDENT_NUMBER_PATTERN = re.compile(
    r"(?<!\d)(\d{6})\s*-?\s*([1-8]\d{6})(?!\d)"
)
MASKED_RESIDENT_NUMBER = "[주민등록번호 마스킹]"


def mask_resident_registration_numbers(text: str) -> str:
    """주민등록번호 전체를 복원 불가능한 고정 문구로 치환한다."""
    return RESIDENT_NUMBER_PATTERN.sub(MASKED_RESIDENT_NUMBER, text)


def contains_resident_registration_number(text: str) -> bool:
    """원문에 완전한 주민등록번호 형태가 남아 있는지 확인한다."""
    return RESIDENT_NUMBER_PATTERN.search(text) is not None


def redact_resident_number_rows(
    recognized_lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """주민등록번호 라벨과 같은 행의 OCR 조각을 좌표 기준으로 폐기한다."""
    label_rows = []
    for line in recognized_lines:
        normalized = re.sub(r"\s", "", str(line.get("text", "")))
        if "주민등록번호" in normalized:
            box = line.get("box", [0, 0, 0, 0])
            label_rows.append(((box[1] + box[3]) / 2, box[0]))

    redacted = []
    for line in recognized_lines:
        item = dict(line)
        text = str(item.get("text", ""))
        box = item.get("box", [0, 0, 0, 0])
        y_center = (box[1] + box[3]) / 2
        same_sensitive_row = any(
            abs(y_center - label_y) <= 35 and box[2] >= label_x
            for label_y, label_x in label_rows
        )
        normalized_label = re.sub(r"\s", "", text)
        digit_count = sum(character.isdigit() for character in text)
        sensitive_fragment = "주민등록번호" in normalized_label or digit_count >= 6
        item["text"] = (
            MASKED_RESIDENT_NUMBER
            if same_sensitive_row and sensitive_fragment
            else mask_resident_registration_numbers(text)
        )
        redacted.append(item)
    return redacted
