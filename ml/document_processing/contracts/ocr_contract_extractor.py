# ocr_contract_extractor.py
"""
Description: PP-OCR 결과의 텍스트와 좌표를 이용해 표준 임대차·매매
계약서의 핵심 필드를 ContractExtraction JSON으로 구조화한다.
Author: ooheunsu
Date: 2026-10-05
Before:
    - 페이지별 OCR 문자열·신뢰도·bounding box가 준비된 상태.
After:
    - 개인정보가 제거되고 출처 페이지가 포함된 계약 추출 결과가 반환됨.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from ml.document_processing.contracts.extraction_schema import ContractExtraction
from ml.document_processing.contracts.privacy import (
    mask_resident_registration_numbers,
)


@dataclass(frozen=True)
class OcrLine:
    page_number: int
    text: str
    confidence: float
    box: tuple[int, int, int, int]

    @property
    def y_center(self) -> float:
        return (self.box[1] + self.box[3]) / 2


def extract_contract_from_ocr(ocr_document: dict[str, Any]) -> ContractExtraction:
    """OCR 산출물만 사용해 지원 계약서의 필드를 구조화한다."""
    pages = _load_pages(ocr_document)
    contract_type = _infer_contract_type(ocr_document["source_filename"], pages)
    data = (
        _extract_sale(pages)
        if contract_type == "sale"
        else _extract_lease(pages, contract_type)
    )
    return ContractExtraction.model_validate(data)


def _load_pages(document: dict[str, Any]) -> dict[int, list[OcrLine]]:
    pages: dict[int, list[OcrLine]] = {}
    for page in document["pages"]:
        number = int(page["page_number"])
        pages[number] = sorted(
            (
                OcrLine(
                    page_number=number,
                    text=mask_resident_registration_numbers(str(item["text"])),
                    confidence=float(item["confidence"]),
                    box=tuple(int(value) for value in item["box"]),
                )
                for item in page["recognized_lines"]
            ),
            key=lambda line: (line.box[1], line.box[0]),
        )
    return pages


def _infer_contract_type(filename: str, pages: dict[int, list[OcrLine]]) -> str:
    if "매매" in filename or _find_line(pages.get(1, []), "부동산매매계약서"):
        return "sale"
    if "월세" in filename:
        return "monthly_rent"
    return "jeonse"


def _extract_lease(
    pages: dict[int, list[OcrLine]],
    contract_type: str,
) -> dict[str, Any]:
    first = pages.get(1, [])
    third = pages.get(3, [])
    address, detail_address = _split_address(_row_value(first, "소재지"))
    names = _party_names(third, ("임대인", "임차인"))
    contract_dates = _all_dates(third)
    period_dates = _dates_near(first, "제2조")
    payment_dates = _all_dates(_lines_between(first, "계약금", "차임월세"))

    parties = [
        {"role": role, "name": _text_field(name, 3)}
        for role, name in zip(("lessor", "lessee"), names, strict=False)
    ]
    intermediate = _money_near(first, "중도금")
    payment: dict[str, Any] = {
        "deposit": _money_field(_money_near(first, "보증금"), 1),
        "maintenance_fee": _money_field(
            _money_near(first, "정액인경우"), 1, optional=True
        ),
        "down_payment": _money_field(
            _money_near(first, "계약금"), 1, optional=True
        ),
        "intermediate_payments": [],
        "remaining_balance": _money_field(
            _money_near(first, "잔금"), 1, optional=True
        ),
        "remaining_balance_date": _date_field(
            payment_dates[-1] if payment_dates else None, 1, optional=True
        ),
    }
    if intermediate is not None:
        payment["intermediate_payments"] = [
            {
                "label": "중도금",
                "amount": _money_field(intermediate, 1),
                "due_date": _date_field(
                    payment_dates[0] if payment_dates else None,
                    1,
                    optional=True,
                ),
            }
        ]
    if contract_type == "monthly_rent":
        payment["monthly_rent"] = _money_field(_money_near(first, "차임월세"), 1)
        payment["monthly_rent_payment_day"] = _text_field(
            _monthly_payment_day(first), 1, optional=True
        )
        payment["payment_timing"] = _text_field("unspecified", 1)

    special_terms = _lease_special_terms(third)
    return {
        "contract_type": _text_field(contract_type, 1),
        "contract_date": _date_field(
            contract_dates[-1] if contract_dates else None, 3
        ),
        "start_date": _date_field(
            period_dates[0] if period_dates else None, 1, optional=True
        ),
        "end_date": _date_field(
            period_dates[1] if len(period_dates) > 1 else None, 1, optional=True
        ),
        "delivery_date": _date_field(
            period_dates[0] if period_dates else None, 1, optional=True
        ),
        "property": _lease_property(first, address, detail_address),
        "parties": parties,
        "payment": payment,
        "articles": [],
        "special_terms": _text_blocks(special_terms, 3),
        "disclosures": {
            "tax_arrears": _text_field(
                _nearby_choice(first, "미납국세지방세", ("없음", "있음")),
                1,
                optional=True,
            ),
            "senior_deposit_status": _text_field(
                _nearby_choice(first, "선순위확정일자", ("해당없음", "해당있음")),
                1,
                optional=True,
            ),
        },
        "brokers": _brokers(third),
        "warnings": [
            "PP-OCRv5 손글씨 결과에 표준 양식 위치 규칙을 적용한 초기 추출입니다.",
            "low_confidence 또는 missing 필드는 사용자 확인이 필요합니다.",
        ],
    }


def _extract_sale(pages: dict[int, list[OcrLine]]) -> dict[str, Any]:
    first = pages.get(1, [])
    second = pages.get(2, [])
    third = pages.get(3, [])
    address, detail_address = _split_address(_row_value(first, "소재지"))
    names = _party_names(first, ("매도인", "매수인"))
    contract_dates = _all_dates(third)
    transfer_dates = _all_dates(_lines_between(second, "소유권이전", "제3조"))
    payment_lines = _lines_between(first, "매매대금", "제2조")
    rows = {
        label: _money_near(payment_lines, label)
        for label in ("매매대금", "계약금", "1차중도금", "2차중도금", "잔금")
    }
    payment_dates = _all_dates(payment_lines)
    installments = []
    for index, label in enumerate(("1차중도금", "2차중도금")):
        if rows[label] is not None:
            installments.append(
                {
                    "label": label,
                    "amount": _money_field(rows[label], 1),
                    "due_date": _date_field(
                        payment_dates[index] if index < len(payment_dates) else None,
                        1,
                        optional=True,
                    ),
                }
            )
    return {
        "contract_type": _text_field("sale", 1),
        "contract_date": _date_field(
            contract_dates[0] if contract_dates else None, 3
        ),
        "ownership_transfer_date": _date_field(
            transfer_dates[0] if transfer_dates else None, 2, optional=True
        ),
        "delivery_date": _date_field(
            transfer_dates[-1] if transfer_dates else None, 2, optional=True
        ),
        "property": {
            "address": _text_field(address, 1),
            "detail_address": _text_field(detail_address, 1),
            "building_area_m2": _number_field(_number_near(first, "면적"), 1),
        },
        "parties": [
            {"role": role, "name": _text_field(name, 1)}
            for role, name in zip(("seller", "buyer"), names, strict=False)
        ],
        "payment": {
            "sale_price": _money_field(rows["매매대금"], 1),
            "down_payment": _money_field(rows["계약금"], 1, optional=True),
            "intermediate_payments": installments,
            "remaining_balance": _money_field(rows["잔금"], 1, optional=True),
            "remaining_balance_date": _date_field(
                payment_dates[-1] if payment_dates else None, 1, optional=True
            ),
        },
        "articles": _text_blocks(_sale_articles(second), 2, article=True),
        "special_terms": _text_blocks(_sale_special_terms(third), 3),
        "disclosures": {
            "registry_checked": {
                "value": _find_line(second, "등기부등본") is not None,
                "raw_text": "등기부등본",
                "source": {"page_number": 2},
                "confidence": 0.9,
                "status": "extracted",
            }
        },
        "brokers": [],
        "warnings": [
            "서울중앙지방법원 매매계약서 위치 규칙을 적용한 초기 추출입니다.",
            "low_confidence 또는 missing 필드는 사용자 확인이 필요합니다.",
        ],
    }


def _lease_property(
    lines: list[OcrLine],
    address: str | None,
    detail_address: str | None,
) -> dict[str, Any]:
    building_row = _row_value(lines, "구조용도")
    structure, use = _split_building(building_row)
    areas = _numbers_on_rows(lines, "면적")
    return {
        "address": _text_field(address, 1),
        "detail_address": _text_field(detail_address, 1),
        "leased_part_description": _text_field(
            _row_value(lines, "임차할부분"), 1, optional=True
        ),
        "leased_area_m2": _number_field(areas[-1] if areas else None, 1),
        "land_category": _text_field(_row_value(lines, "지목"), 1, optional=True),
        "land_area_m2": _number_field(areas[0] if areas else None, 1),
        "building_structure": _text_field(structure, 1, optional=True),
        "building_use": _text_field(use, 1, optional=True),
        "building_area_m2": _number_field(
            areas[1] if len(areas) > 1 else None, 1
        ),
    }


# Parsing helpers are kept local because these rules are experimental and
# specific to the two evaluated public contract forms.


def _row_value(lines: list[OcrLine], label: str) -> str | None:
    anchor = _find_line(lines, label)
    if anchor is None:
        return None
    tolerance = max(28, int((anchor.box[3] - anchor.box[1]) * 0.9))
    row = [
        line
        for line in lines
        if abs(line.y_center - anchor.y_center) <= tolerance
        and line.box[2] >= anchor.box[0]
    ]
    text = " ".join(line.text for line in sorted(row, key=lambda item: item.box[0]))
    cleaned = re.sub(r"\([^)]*도로[^)]*\)", " ", text)
    cleaned = re.sub(re.escape(anchor.text), " ", cleaned, count=1)
    return _clean_text(cleaned) or None


def _nearby_choice(
    lines: list[OcrLine],
    label: str,
    choices: tuple[str, ...],
) -> str | None:
    anchor = _find_line(lines, label)
    if anchor is None:
        return None
    nearby = [
        line
        for line in lines
        if abs(line.y_center - anchor.y_center) <= 90
        and line.box[0] <= anchor.box[2] + 250
    ]
    nearby_text = _normalize(" ".join(line.text for line in nearby))
    for choice in choices:
        if _normalize(choice) in nearby_text:
            return "해당 없음" if choice == "해당없음" else choice
    return None


def _money_near(lines: list[OcrLine], label: str) -> int | None:
    anchors = _find_lines(lines, label)
    if not anchors:
        return None
    anchor = max(anchors, key=lambda line: line.box[1])
    row = [
        line
        for line in lines
        if abs(line.y_center - anchor.y_center) <= 32 and line.box[0] >= anchor.box[0]
    ]
    text = " ".join(item.text for item in sorted(row, key=lambda item: item.box[0]))
    won_match = re.search(r"[W₩]\s*([0-9OolID∞,\s]+)", text)
    if won_match:
        digits = _ocr_digits(won_match.group(1))
        if digits:
            return int(digits)
    korean = re.sub(r".*?금\s*", "", text)
    korean = re.split(r"원", korean, maxsplit=1)[0]
    return _korean_money(korean)


def _number_near(lines: list[OcrLine], label: str) -> float | None:
    anchor = _find_line(lines, label)
    if anchor is None:
        return None
    numbers = _decimal_numbers(
        " ".join(line.text for line in _near_y(lines, anchor.y_center, 32))
    )
    return numbers[-1] if numbers else None


def _numbers_on_rows(lines: list[OcrLine], label: str) -> list[float]:
    values: list[float] = []
    for anchor in _find_lines(lines, label):
        numbers = _decimal_numbers(
            " ".join(line.text for line in _near_y(lines, anchor.y_center, 30))
        )
        if numbers:
            values.append(max(numbers))
    return values


def _party_names(lines: list[OcrLine], role_labels: tuple[str, str]) -> list[str]:
    names: list[str] = []
    excluded = {"성명", "주소", "전화", "날인"}
    for role in role_labels:
        role_lines = _find_lines(lines, role)
        if not role_lines:
            continue
        role_line = max(role_lines, key=lambda line: line.box[1])
        candidates = [
            line
            for line in lines
            if abs(line.y_center - role_line.y_center) <= 80
            and re.fullmatch(r"[가-힣]{2,4}", line.text.strip())
            and _normalize(line.text) not in excluded
        ]
        if candidates:
            names.append(max(candidates, key=lambda line: line.box[0]).text.strip())
    return names


def _brokers(lines: list[OcrLine]) -> list[dict[str, Any]]:
    offices = [
        line.text.strip()
        for line in lines
        if "공인중개사사무" in _normalize(line.text)
    ]
    registrations = [
        match.group(0)
        for line in lines
        if (match := re.search(r"가상-[가-힣]+-\d{4}-\d{3}", line.text))
    ]
    excluded = {"성명", "주소", "전화", "날인"}
    names = [
        line.text.strip()
        for line in lines
        if re.fullmatch(r"[가-힣]{2,4}", line.text.strip())
        and 850 <= line.y_center <= 1150
        and _normalize(line.text) not in excluded
    ]
    count = max(len(offices), len(registrations), 1 if names else 0)
    return [
        {
            "office_name": _text_field(
                offices[index] if index < len(offices) else None, 3, optional=True
            ),
            "office_registration_number": _text_field(
                registrations[index] if index < len(registrations) else None,
                3,
                optional=True,
            ),
            "broker_in_charge": _text_field(
                names[index] if index < len(names) else None, 3, optional=True
            ),
        }
        for index in range(count)
    ]


def _lease_special_terms(lines: list[OcrLine]) -> list[str]:
    proof = _find_line(lines, "계약을증명")
    upper = proof.box[1] if proof else 380
    return [
        _clean_text(line.text)
        for line in lines
        if line.box[1] < upper
        and len(_normalize(line.text)) >= 10
        and "페이지" not in line.text
    ]


def _sale_special_terms(lines: list[OcrLine]) -> list[str]:
    start = _find_line(lines, "제7조")
    if start is None:
        return []
    return [
        _clean_text(line.text)
        for line in lines
        if start.box[3] <= line.y_center <= start.box[3] + 280
        and len(_normalize(line.text)) >= 10
    ]


def _sale_articles(lines: list[OcrLine]) -> list[str]:
    values = []
    for label in ("따로정하는사항", "따로정하는기간"):
        for anchor in _find_lines(lines, label):
            value = _row_value(lines, _normalize(anchor.text))
            if value:
                values.append(value)
    return list(dict.fromkeys(values))


def _text_blocks(
    values: Iterable[str],
    page_number: int,
    article: bool = False,
) -> list[dict[str, Any]]:
    blocks = []
    for index, value in enumerate(values, start=1):
        block: dict[str, Any] = {
            "item_order": index,
            "original_text": value,
            "source": {"page_number": page_number},
            "confidence": 0.6,
        }
        if article:
            block["clause_number"] = str(index)
        blocks.append(block)
    return blocks


def _text_field(
    value: str | None,
    page_number: int,
    optional: bool = False,
) -> dict[str, Any] | None:
    if not value:
        if optional:
            return None
        return {"value": None, "raw_text": None, "source": None, "status": "missing"}
    return {
        "value": value,
        "raw_text": value,
        "source": {"page_number": page_number},
        "confidence": 0.6,
        "status": "low_confidence",
    }


def _money_field(
    value: int | None,
    page_number: int,
    optional: bool = False,
) -> dict[str, Any] | None:
    if value is None and optional:
        return None
    if value is None:
        return {"value": None, "raw_text": None, "source": None, "status": "missing"}
    return {
        "value": value,
        "raw_text": str(value),
        "source": {"page_number": page_number},
        "confidence": 0.6,
        "status": "low_confidence",
    }


def _number_field(value: float | None, page_number: int) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "value": value,
        "raw_text": str(value),
        "source": {"page_number": page_number},
        "confidence": 0.6,
        "status": "low_confidence",
    }


def _date_field(
    value: date | None,
    page_number: int,
    optional: bool = False,
) -> dict[str, Any] | None:
    if value is None and optional:
        return None
    if value is None:
        return {"value": None, "raw_text": None, "source": None, "status": "missing"}
    return {
        "value": value.isoformat(),
        "raw_text": value.isoformat(),
        "source": {"page_number": page_number},
        "confidence": 0.6,
        "status": "low_confidence",
    }


def _monthly_payment_day(lines: list[OcrLine]) -> str | None:
    anchor = _find_line(lines, "차임월세")
    if anchor is None:
        return None
    text = " ".join(line.text for line in _near_y(lines, anchor.y_center, 35))
    match = re.search(r"매월\s*(\d{1,2})\s*일", text)
    return f"매월 {match.group(1)}일" if match else None


def _dates_near(lines: list[OcrLine], label: str) -> list[date]:
    anchor = _find_line(lines, label)
    if anchor is None:
        return []
    nearby = [line for line in lines if abs(line.y_center - anchor.y_center) <= 180]
    return _all_dates(nearby)


def _all_dates(lines: list[OcrLine]) -> list[date]:
    text = " ".join(line.text for line in lines)
    normalized = text.translate(
        str.maketrans({"O": "0", "o": "0", "l": "1", "/": "1"})
    )
    found: list[date] = []
    for match in re.finditer(
        r"(20\d{2})\s*년?\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?",
        normalized,
    ):
        try:
            value = date(*(int(part) for part in match.groups()))
        except ValueError:
            continue
        if value not in found:
            found.append(value)
    return found


def _lines_between(lines: list[OcrLine], start: str, end: str) -> list[OcrLine]:
    start_line = _find_line(lines, start)
    end_line = _find_line(lines, end)
    if start_line is None:
        return []
    upper = (
        end_line.box[1]
        if end_line
        else max((line.box[3] for line in lines), default=0)
    )
    return [line for line in lines if start_line.box[1] <= line.box[1] < upper]


def _find_line(lines: list[OcrLine], token: str) -> OcrLine | None:
    normalized = _normalize(token)
    return next((line for line in lines if normalized in _normalize(line.text)), None)


def _find_lines(lines: list[OcrLine], token: str) -> list[OcrLine]:
    normalized = _normalize(token)
    return [line for line in lines if normalized in _normalize(line.text)]


def _near_y(
    lines: list[OcrLine],
    y_center: float,
    tolerance: float,
) -> list[OcrLine]:
    return sorted(
        (line for line in lines if abs(line.y_center - y_center) <= tolerance),
        key=lambda line: line.box[0],
    )


def _normalize(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text).lower()


def _clean_text(text: str) -> str:
    return " ".join(text.replace("□", " ").split()).strip(" ,:_")


def _split_address(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    cleaned = re.sub(r"^(도로명주소)", "", value).strip()
    if "," in cleaned:
        address, detail = cleaned.split(",", maxsplit=1)
        return address.strip(), detail.strip()
    apartment = re.search(
        r"(.+?)([가-힣A-Za-z]+(?:아파트|빌)\s*\d+.*)$",
        cleaned,
    )
    if apartment:
        return apartment.group(1).strip(), apartment.group(2).strip()
    return cleaned, ""


def _split_building(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    for marker in ("아파트", "다세대주택", "연립주택"):
        if marker in value:
            return value.split(marker, maxsplit=1)[0].strip(), marker
    return value, None


def _decimal_numbers(text: str) -> list[float]:
    values = []
    for raw in re.findall(r"(?<!\d)(\d{1,5}(?:[.,]\d{1,2})?)(?!\d)", text):
        try:
            number = float(raw.replace(",", "."))
        except ValueError:
            continue
        if number >= 1:
            values.append(number)
    return values


def _ocr_digits(value: str) -> str:
    translated = value.translate(
        str.maketrans(
            {"O": "0", "o": "0", "D": "0", "I": "1", "l": "1", "∞": "00"}
        )
    )
    return re.sub(r"\D", "", translated)


def _korean_money(value: str) -> int | None:
    digits = {
        "일": 1,
        "이": 2,
        "삼": 3,
        "사": 4,
        "오": 5,
        "육": 6,
        "칠": 7,
        "팔": 8,
        "구": 9,
    }
    small_units = {"십": 10, "백": 100, "천": 1000}
    total = section = current = 0
    seen = False
    for char in re.sub(r"[^일이삼사오육칠팔구십백천만억]", "", value):
        seen = True
        if char in digits:
            current = digits[char]
        elif char in small_units:
            section += (current or 1) * small_units[char]
            current = 0
        elif char == "만":
            total += (section + current or 1) * 10_000
            section = current = 0
        elif char == "억":
            total = (total + section + current or 1) * 100_000_000
            section = current = 0
    result = total + section + current
    return result if seen and result > 0 else None
