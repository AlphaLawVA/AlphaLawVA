# registry_document_ai_extractor.py
"""
Description: 저장된 Document AI 좌표와 텍스트에서 등기사항전부증명서의
표제부·갑구·을구 항목을 보수적으로 구조화한다.
Author: ooheunsu
Date: 2026-10-19
Before:
    - 등기사항전부증명서의 Document AI JSON이 준비된 상태.
After:
    - 권리 상태를 추측하지 않는 사용자 확인 전 RegistryExtraction이 반환됨.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from ml.document_processing.contracts.document_ai_extractor import (
    Line,
    compact,
    field,
    joined,
    load_lines,
    source,
)
from ml.document_processing.contracts.extraction_schema import RegistryExtraction


_SECTION_MARKERS = {
    "표제부": "title",
    "갑구": "gapgu",
    "을구": "eulgu",
}
_TABLE_HEADERS = ("순위번호", "표시번호", "등기목적", "권리자및기타사항")
_ENTRY_NUMBER = re.compile(r"^(\d+(?:-\d+)?)$")
_DATE = re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")


def _marker(text: str) -> str | None:
    token = compact(text).replace("[", "").replace("]", "")
    for label, section in _SECTION_MARKERS.items():
        if label in token:
            return section
    return None


def _is_ignored(line: Line) -> bool:
    token = compact(line.text)
    return any(header in token for header in _TABLE_HEADERS) or any(
        end in token for end in ("이하여백", "관할등기소", "열람용")
    )


def _split_sections(lines: list[Line]) -> dict[str, list[Line]]:
    sections = {name: [] for name in _SECTION_MARKERS.values()}
    current = None
    for line in lines:
        marker = _marker(line.text)
        if marker is not None:
            current = marker
            continue
        if current is not None and not _is_ignored(line):
            sections[current].append(line)
    return sections


def _entry_number(line: Line) -> str | None:
    if line.box[0] >= 0.2:
        return None
    match = _ENTRY_NUMBER.fullmatch(compact(line.text))
    return match[1] if match else None


def _entry_blocks(lines: list[Line]) -> list[tuple[str, list[Line]]]:
    result: list[tuple[str, list[Line]]] = []
    number = None
    active: list[Line] = []
    for line in lines:
        found = _entry_number(line)
        if found is not None:
            if number is not None:
                result.append((number, active))
            number = found
            active = [line]
        elif number is not None:
            active.append(line)
    if number is not None:
        result.append((number, active))
    return result


def _column(lines: list[Line], left: float, right: float) -> list[Line]:
    return [line for line in lines if left <= line.box[0] < right]


def _parse_date(lines: list[Line]):
    match = _DATE.search(joined(lines))
    if match is None:
        return field(None, [])
    try:
        value = date(*map(int, match.groups()))
    except ValueError:
        return field(None, [])
    return field(value, lines)


def _issued_at(lines: list[Line]):
    anchor = next((line for line in lines if "열람일시" in compact(line.text)), None)
    if anchor is None:
        return field(None, [])
    candidates = [
        line
        for line in lines
        if line.page == anchor.page and abs(line.cy - anchor.cy) < 0.02
    ]
    text = joined(candidates)
    match = re.search(
        r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*"
        r"(\d{1,2})\s*시\s*(\d{1,2})\s*분\s*(\d{1,2})\s*초",
        text,
    )
    if match is None:
        return field(None, [])
    try:
        value = datetime(*map(int, match.groups()), tzinfo=timezone(timedelta(hours=9)))
    except ValueError:
        return field(None, [])
    return field(value, candidates)


def _document_address(lines: list[Line]):
    for line in lines:
        token = line.text.strip()
        match = re.match(r"^\[?\s*건물\s*\]?\s*(.+)$", token)
        if match and match[1].strip(" -"):
            return field(match[1].strip(), [line])
    return field(None, [])


def _detail_address(lines: list[Line]):
    for index, line in enumerate(lines):
        token = line.text.strip()
        if "도로명주소" not in compact(token):
            continue
        value = re.sub(r"^\[?\s*도로명주소\s*\]?\s*", "", token).strip()
        candidates = [line]
        if not value:
            candidates.extend(
                item
                for item in lines[index + 1 :]
                if item.page == line.page
                and 0 < item.cy - line.cy < 0.08
                and item.box[0] >= line.box[0] - 0.02
            )
            value = joined(candidates[1:]).strip()
        if value:
            return field(value, candidates)
    return field(None, [])


def _unique_property_number(lines: list[Line]):
    candidates = [line for line in lines if "고유번호" in compact(line.text)]
    if not candidates:
        return field(None, [])
    nearby = [
        line
        for line in lines
        if line.page == candidates[0].page
        and abs(line.cy - candidates[0].cy) < 0.03
    ]
    match = re.search(r"\d{3,}(?:-\d+)+", joined(nearby))
    return field(match[0] if match else None, nearby if match else [])


def _property_descriptions(lines: list[Line]):
    result = []
    for order, (_, block) in enumerate(_entry_blocks(lines), 1):
        text = joined(block).strip()
        if text:
            result.append(
                {
                    "item_order": order,
                    "original_text": text,
                    "source": source(block),
                }
            )
    return result


def _registration_cause(lines: list[Line]):
    cause_lines = _column(lines, 0.44, 0.65)
    value = _DATE.sub("", joined(cause_lines)).strip() or None
    return field(value, cause_lines) if value else None


def _owner_from_block(block: list[Line]):
    rights = _column(block, 0.58, 1.0)
    rights_text = joined(rights)
    name_match = re.search(r"소유자\s*([가-힣A-Za-z][가-힣A-Za-z0-9*]{0,30})", rights_text)
    if name_match is None:
        return None
    share_match = re.search(r"(\d+\s*분의\s*\d+)", rights_text)
    receipt = _column(block, 0.30, 0.48)
    return {
        "name": field(name_match[1], rights),
        "ownership_share": field(share_match[1], rights) if share_match else None,
        "registration_date": _parse_date(receipt),
        "registration_cause": _registration_cause(block),
        "status": "unknown",
    }


def _purpose(block: list[Line]):
    purpose_lines = _column(block, 0.12, 0.35)
    value = joined(purpose_lines).strip() or None
    return field(value, purpose_lines)


def _encumbrance(section: str, number: str, block: list[Line]):
    rights = _column(block, 0.58, 1.0)
    rights_text = joined(rights)
    amount_match = re.search(r"채권최고액\s*(?:금)?\s*([0-9,]+)\s*원", rights_text)
    amount = int(amount_match[1].replace(",", "")) if amount_match else None
    rank_line = next(line for line in block if _entry_number(line) == number)
    return {
        "section": section,
        "rank_number": field(number, [rank_line]),
        "registration_purpose": _purpose(block),
        "registration_date": _parse_date(_column(block, 0.30, 0.48)),
        "maximum_claim_amount": field(amount, rights) if amount is not None else None,
        "raw_text": joined(block).strip(),
        "source": source(block),
        "status": "unknown",
    }


def extract_registry_document_ai(document: dict) -> RegistryExtraction:
    lines = load_lines(document)
    sections = _split_sections(lines)
    warnings = [
        "registry_entry_status_requires_review: 말소선과 변경 이력을 자동 확정하지 않음"
    ]
    owners = []
    encumbrances = []
    for number, block in _entry_blocks(sections["gapgu"]):
        owner = _owner_from_block(block)
        if owner is not None:
            owners.append(owner)
        else:
            encumbrances.append(_encumbrance("gapgu", number, block))
    for number, block in _entry_blocks(sections["eulgu"]):
        encumbrances.append(_encumbrance("eulgu", number, block))
    if not owners:
        warnings.append("registry_owner_missing: 갑구 소유자 확인 필요")
    if not encumbrances:
        warnings.append("registry_encumbrance_missing: 갑구·을구 권리 항목 확인 필요")
    return RegistryExtraction.model_validate(
        {
            "issued_at": _issued_at(lines),
            "unique_property_number": _unique_property_number(lines),
            "address": _document_address(lines),
            "detail_address": _detail_address(sections["title"]),
            "property_description": _property_descriptions(sections["title"]),
            "owners": owners,
            "encumbrances": encumbrances,
            "warnings": warnings,
        }
    )
