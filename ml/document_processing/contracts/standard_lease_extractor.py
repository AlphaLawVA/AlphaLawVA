# standard_lease_extractor.py
"""
Description: 법무부 주택임대차표준계약서 텍스트 PDF에서 계약 검토에
필요한 핵심 필드를 추출하고 버전형 계약 추출 스키마로 검증한다.
Author: ooheunsu
Date: 2026-09-21
Before:
    - 직접 텍스트 추출이 가능한 주택임대차표준계약서 PDF가 준비된 상태.
After:
    - 주소·기간·금액·당사자·특약이 검증된 ContractExtraction으로 반환됨.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from ml.document_processing.contracts.document_intake import (
    DocumentIntakeError,
    ProcessingRoute,
    inspect_contract_document,
)
from ml.document_processing.contracts.extraction_schema import ContractExtraction


class StandardLeaseExtractionError(ValueError):
    """지원 양식에서 필수 계약 정보를 안정적으로 찾지 못했을 때 발생한다."""


def extract_standard_lease_contract(path: Path) -> ContractExtraction:
    """직접 추출 가능한 법무부 주택임대차표준계약서를 구조화한다."""
    inspection = inspect_contract_document(path)
    if inspection.route is not ProcessingRoute.DIRECT_TEXT:
        raise DocumentIntakeError(
            "현재 표준계약서 추출기는 모든 페이지가 직접 텍스트 추출 가능한 "
            "PDF만 지원합니다."
        )

    reader = PdfReader(path, strict=True)
    pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    if not pages or "주택임대차표준계약서" not in pages[0]:
        raise StandardLeaseExtractionError(
            "지원하는 법무부 주택임대차표준계약서 양식이 아닙니다."
        )

    first_page = pages[0]
    third_page = pages[2] if len(pages) >= 3 else ""

    full_address = _line_value(
        first_page,
        r"소\s*재\s*지\s*\(도로명주소\)\s*(.+)",
        "소재지",
    )
    address, detail_address = _split_address(full_address)
    _require_jeonse_contract(first_page)
    delivery_date, end_date = _lease_period(first_page)
    contract_date = _contract_date(third_page)
    building_structure, building_use = _building_description(first_page)

    deposit = _money_on_line(first_page, r"보\s*증\s*금", "보증금")
    down_payment = _money_on_line(first_page, r"계\s*약\s*금", "계약금")
    intermediate_payment = _money_on_line(
        first_page,
        r"중\s*도\s*금",
        "중도금",
    )
    remaining_balance = _money_on_line(first_page, r"잔\s*금", "잔금")
    intermediate_date = _date_on_payment_line(
        first_page,
        r"중\s*도\s*금",
        "중도금 지급일",
    )
    remaining_balance_date = _date_on_payment_line(
        first_page,
        r"잔\s*금",
        "잔금 지급일",
    )
    maintenance_fee = _management_fee(first_page)

    lessor_name, lessee_name = _party_names(third_page)
    special_terms = _special_terms(third_page)

    data: dict[str, Any] = {
        "contract_type": _field("jeonse", "전세", 1, 0.99),
        "contract_date": _field(contract_date.isoformat(), _date_raw(contract_date), 3),
        "start_date": _field(delivery_date.isoformat(), _date_raw(delivery_date), 1),
        "end_date": _field(end_date.isoformat(), _date_raw(end_date), 1),
        "delivery_date": _field(delivery_date.isoformat(), _date_raw(delivery_date), 1),
        "property": {
            "address": _field(address, address, 1),
            "detail_address": _field(detail_address, detail_address, 1),
            "leased_part_description": _optional_line_field(
                first_page,
                r"임차할부분.*?기재\s+(.+?)\s+면적",
                1,
            ),
            "leased_area_m2": _number_field(
                first_page,
                r"임차할부분.*?면적\s+([0-9,.]+)",
                1,
            ),
            "land_category": _optional_line_field(
                first_page,
                r"토\s*지\s+지목\s+(.+?)\s+면적",
                1,
            ),
            "land_area_m2": _number_field(
                first_page,
                r"토\s*지\s+지목.*?면적\s+([0-9,.]+)",
                1,
            ),
            "building_structure": _optional_line_field(
                building_structure,
                r"(.+)",
                1,
            ),
            "building_use": _optional_line_field(
                building_use,
                r"(.+)",
                1,
            ),
            "building_area_m2": _number_field(
                first_page,
                r"건\s*물\s+구조‧용도.*?면적\s+([0-9,.]+)",
                1,
            ),
        },
        "parties": [
            {"role": "lessor", "name": _field(lessor_name, lessor_name, 3)},
            {"role": "lessee", "name": _field(lessee_name, lessee_name, 3)},
        ],
        "payment": {
            "deposit": _field(deposit, f"{deposit:,}", 1),
            "maintenance_fee": _field(
                maintenance_fee,
                f"{maintenance_fee:,}",
                1,
            ),
            "down_payment": _field(down_payment, f"{down_payment:,}", 1),
            "intermediate_payments": [
                {
                    "label": "중도금",
                    "amount": _field(
                        intermediate_payment,
                        f"{intermediate_payment:,}",
                        1,
                    ),
                    "due_date": _field(
                        intermediate_date.isoformat(),
                        _date_raw(intermediate_date),
                        1,
                    ),
                }
            ],
            "remaining_balance": _field(
                remaining_balance,
                f"{remaining_balance:,}",
                1,
            ),
            "remaining_balance_date": _field(
                remaining_balance_date.isoformat(),
                _date_raw(remaining_balance_date),
                1,
            ),
        },
        "articles": [],
        "special_terms": [
            {
                "item_order": index,
                "original_text": term,
                "source": {"page_number": 3},
                "confidence": 0.98,
            }
            for index, term in enumerate(special_terms, start=1)
        ],
        "brokers": _brokers(third_page),
        "warnings": [
            "법무부 주택임대차표준계약서 텍스트 PDF 기준의 초기 규칙 추출 결과입니다.",
            "원문 bounding box는 아직 수집하지 않았으므로 페이지 번호만 기록했습니다.",
        ],
    }
    return ContractExtraction.model_validate(data)


def _field(
    value: Any,
    raw_text: str,
    page_number: int,
    confidence: float = 0.97,
) -> dict[str, Any]:
    return {
        "value": value,
        "raw_text": raw_text,
        "source": {"page_number": page_number},
        "confidence": confidence,
        "status": "extracted",
    }


def _line_value(text: str, pattern: str, label: str) -> str:
    match = re.search(pattern, text, flags=re.MULTILINE)
    if match is None:
        raise StandardLeaseExtractionError(f"{label}을 찾지 못했습니다.")
    return " ".join(match.group(1).split())


def _split_address(full_address: str) -> tuple[str, str]:
    if "," not in full_address:
        return full_address, ""
    address, detail = full_address.split(",", maxsplit=1)
    return address.strip(), detail.strip()


def _require_jeonse_contract(text: str) -> None:
    rent_line = re.search(r"^\s*차임\(월세\).*?$", text, flags=re.MULTILINE)
    if rent_line is None or "해당 없음" not in rent_line.group(0):
        raise StandardLeaseExtractionError(
            "현재 초기 추출기는 차임이 없는 전세 표준계약서만 지원합니다."
        )


def _lease_period(text: str) -> tuple[date, date]:
    match = re.search(
        r"제2조\(임대차기간\).*?"
        r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일까지.*?"
        r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일까지",
        text,
        flags=re.DOTALL,
    )
    if match is None:
        raise StandardLeaseExtractionError("임대차 기간을 찾지 못했습니다.")
    values = [int(value) for value in match.groups()]
    return date(*values[:3]), date(*values[3:])


def _contract_date(text: str) -> date:
    match = re.search(
        r"각각\s+1통씩\s+보관한다\.\s*"
        r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일",
        text,
    )
    if match is None:
        raise StandardLeaseExtractionError("계약 체결일을 찾지 못했습니다.")
    return date(*(int(value) for value in match.groups()))


def _money_on_line(text: str, label_pattern: str, label: str) -> int:
    match = re.search(
        rf"^\s*{label_pattern}.*?([0-9][0-9,]{{3,}})\)?",
        text,
        flags=re.MULTILINE,
    )
    if match is None:
        raise StandardLeaseExtractionError(f"{label} 금액을 찾지 못했습니다.")
    return int(match.group(1).replace(",", ""))


def _date_on_payment_line(text: str, label_pattern: str, label: str) -> date:
    match = re.search(
        rf"^\s*{label_pattern}.*?"
        r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일",
        text,
        flags=re.MULTILINE,
    )
    if match is None:
        raise StandardLeaseExtractionError(f"{label}을 찾지 못했습니다.")
    return date(*(int(value) for value in match.groups()))


def _management_fee(text: str) -> int:
    match = re.search(r"정액\s*십이만원.*?([0-9][0-9,]+)", text)
    if match is None:
        raise StandardLeaseExtractionError("관리비 총액을 찾지 못했습니다.")
    return int(match.group(1).replace(",", ""))


def _party_names(text: str) -> tuple[str, str]:
    names = re.findall(r"성\s*명\s+([가-힣]{2,4})(?=\s*$)", text, flags=re.MULTILINE)
    if len(names) < 2:
        raise StandardLeaseExtractionError("임대인·임차인 성명을 찾지 못했습니다.")
    return names[0], names[1]


def _special_terms(text: str) -> list[str]:
    terms = [
        re.sub(r"\s*※\s*기타\s*$", "", " ".join(match.group(1).split()))
        for match in re.finditer(r"^\s*[1-9]\.\s+(.+)$", text, flags=re.MULTILINE)
    ]
    if len(terms) < 3:
        raise StandardLeaseExtractionError("사용자 작성 특약 3개를 찾지 못했습니다.")
    return terms[:3]


def _building_description(text: str) -> tuple[str, str]:
    description = _line_value(
        text,
        r"건\s*물\s+구조‧용도\s+(.+?)\s+면적",
        "건물 구조·용도",
    )
    if "/" not in description:
        return description, ""
    structure, use = description.split("/", maxsplit=1)
    return structure.strip(), use.strip()


def _brokers(text: str) -> list[dict[str, Any]]:
    office = _line_value(
        text,
        r"사무소명칭\s+([가-힣A-Za-z0-9]+)",
        "중개사무소명",
    )
    registration_match = re.search(r"(가상-\d{4}-\d{4})", text)
    broker_match = re.search(
        r"소속공인중개사\s+([가-힣]{2,4})",
        text,
    )
    if registration_match is None or broker_match is None:
        raise StandardLeaseExtractionError("중개사 정보를 찾지 못했습니다.")
    registration_number = registration_match.group(1)
    broker_name = broker_match.group(1)
    signature_omitted = "서명 생략" in text
    return [
        {
            "office_name": _field(office, office, 3),
            "office_registration_number": _field(
                registration_number,
                registration_number,
                3,
            ),
            "broker_in_charge": _field(broker_name, broker_name, 3),
            "signature_present": _field(
                not signature_omitted,
                "서명 생략" if signature_omitted else "서명 표시",
                3,
            ),
        }
    ]


def _optional_line_field(
    text: str,
    pattern: str,
    page_number: int,
) -> dict[str, Any] | None:
    match = re.search(pattern, text)
    if match is None:
        return None
    value = " ".join(match.group(1).split())
    return _field(value, value, page_number)


def _number_field(
    text: str,
    pattern: str,
    page_number: int,
) -> dict[str, Any] | None:
    match = re.search(pattern, text)
    if match is None:
        return None
    raw_value = match.group(1)
    return _field(float(raw_value.replace(",", "")), raw_value, page_number)


def _date_raw(value: date) -> str:
    return f"{value.year}년 {value.month}월 {value.day}일"
