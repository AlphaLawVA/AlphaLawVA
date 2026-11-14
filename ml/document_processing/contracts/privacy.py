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
MASKED_CORPORATE_NUMBER = "[법인·사업자등록번호 마스킹]"
MASKED_PHONE_NUMBER = "[전화번호 마스킹]"
MASKED_ACCOUNT_NUMBER = "[계좌번호 마스킹]"

# 법인등록번호도 뒷자리 첫 숫자가 1~8이라 주민등록번호와 숫자 형태만으로는 구분할
# 수 없다. 번호 주변(한 줄·행 단위)에 법인·단체를 나타내는 말이 있으면 주민등록번호가
# 아니라 법인·사업자등록번호로 본다. 등기부 등 단어(토큰) 단위로 먼저 마스킹한 뒤
# 줄로 합치는 호출부에서는 이 패턴을 다시 써서 합쳐진 줄 기준으로 재판정한다
# (registry_document_ai_extractor.relabel_corporate_number 참고).
CORPORATE_INDICATOR_PATTERN = re.compile(
    r"주식회사|㈜|유한회사|합자회사|합명회사|재단법인|사단법인|협동조합|농업협동조합|"
    r"수산업협동조합|새마을금고|신용협동조합|공사|공단|은행|보증보험"
)

PHONE_NUMBER_PATTERN = re.compile(
    r"(?<!\d)(?:0(?:2|[3-6]\d|10))\s*-?\s*\d{3,4}\s*-?\s*\d{4}(?!\d)"
)
ACCOUNT_NUMBER_PATTERN = re.compile(
    r"(?P<label>(?:입금\s*)?계좌(?:번호)?|은행|국민|신한|우리|하나|농협|기업|"
    r"카카오(?:뱅크)?|토스(?:뱅크)?|새마을금고|우체국)\s*[:：]?\s*"
    r"(?P<number>\d{2,4}(?:\s*-?\s*\d{2,6}){2,4})(?!\d)"
)


def mask_resident_registration_numbers(text: str) -> str:
    """주민등록번호 전체를 복원 불가능한 고정 문구로 치환한다.

    법인·단체 이름 뒤에 붙은 법인등록번호는 자릿수가 같아 같은 패턴에 걸리므로,
    번호 앞에 법인·단체를 나타내는 말이 있으면 다른 문구로 치환한다.
    """

    def _replace(match: re.Match[str]) -> str:
        prefix = text[: match.start()]
        if CORPORATE_INDICATOR_PATTERN.search(prefix[-20:]):
            return MASKED_CORPORATE_NUMBER
        return MASKED_RESIDENT_NUMBER

    return RESIDENT_NUMBER_PATTERN.sub(_replace, text)


def relabel_corporate_number(text: str) -> str:
    """단어(토큰) 단위로 먼저 마스킹해 문맥이 끊긴 탓에 주민등록번호로 잘못 마스킹된

    법인·단체 번호를 되살린다. 등본처럼 토큰 단위로 OCR·마스킹한 뒤 한 줄·행으로
    합치는 호출부는 합친 뒤 이 함수를 불러, 그 줄에 법인·단체를 나타내는 말이 있으면
    해당 줄 안의 [주민등록번호 마스킹]을 [법인·사업자등록번호 마스킹]으로 바꾼다.
    """

    if MASKED_RESIDENT_NUMBER not in text or not CORPORATE_INDICATOR_PATTERN.search(text):
        return text
    return text.replace(MASKED_RESIDENT_NUMBER, MASKED_CORPORATE_NUMBER)


def contains_resident_registration_number(text: str) -> bool:
    """원문에 완전한 주민등록번호 형태가 남아 있는지 확인한다."""
    return RESIDENT_NUMBER_PATTERN.search(text) is not None


def mask_sensitive_contract_text(text: str) -> str:
    """계약 분석에 불필요한 식별·연락·계좌 정보를 고정 문구로 치환한다."""

    text = mask_resident_registration_numbers(text)
    text = PHONE_NUMBER_PATTERN.sub(MASKED_PHONE_NUMBER, text)
    return ACCOUNT_NUMBER_PATTERN.sub(
        lambda match: f"{match['label']} {MASKED_ACCOUNT_NUMBER}", text
    )


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
            else mask_sensitive_contract_text(text)
        )
        redacted.append(item)
    return redacted
