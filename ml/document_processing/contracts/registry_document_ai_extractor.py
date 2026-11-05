# registry_document_ai_extractor.py
"""
Description: 저장된 Document AI 좌표와 텍스트에서 등기사항전부증명서의
표제부·갑구·을구 항목을 단어(토큰) 좌표 기준으로 보수적으로 구조화한다.
Author: ooheunsu
Date: 2026-10-19
Before:
    - 줄 단위 + 고정 열 가정으로, 실제 등본처럼 OCR이 표의 가로 줄을
      열을 넘어 묶는 문서에서는 소유자·을구가 누락되거나 섞였음.
After:
    - 표 헤더와 열 경계 기준으로 단어를 열에 배정해 순위번호별 항목을 만들고,
      권리 상태는 추측하지 않는 사용자 확인 전 RegistryExtraction이 반환됨.
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
from ml.document_processing.contracts.extraction_schema import (
    RESIDENT_REGISTRATION_NUMBER,
    RegistryExtraction,
)

_MASKED_REGISTRATION_NUMBER = "[등록번호 마스킹]"
_SECTION_MARKERS = {"표제부": "title", "갑구": "gapgu", "을구": "eulgu"}
_ENTRY_NUMBER = re.compile(r"\d+(?:-\d+)?")
_DATE = re.compile(r"((?:19|20)\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_FOOTER_WORDS = (
    "이하여백",
    "관할등기소",
    "열람일시",
    "실선으로",
    "증명서는컬러",
    "기록사항없는",
    "본등기사항증명서",
)
_HEADER_WORDS = ("순위번호", "표시번호")
_WATERMARK = {"열람용", "열", "람", "용", "여"}
# 갑구·을구 표: 순위번호 | 등기목적 | 접수 | 등기원인 | 권리자 및 기타사항
_RIGHTS_COLUMNS = (0.145, 0.305, 0.436, 0.575)
# 일반 건물 표제부: 표시번호 | 접수 | 소재지번 | 건물내역 | 등기원인 및 기타사항
_TITLE_COLUMNS = (0.143, 0.285, 0.515, 0.74)
_AMENDMENT_PURPOSE = re.compile(r"변경|말소|소멸|경정|이전")
_OWNERSHIP_EXCLUDED = re.compile(r"압류|가처분|가등기|경매|예고|환매")
_HOLDER_LABEL = re.compile(
    r"^(근저당권자|전세권자|임차권자|질권자|채권자|가압류권자|가등기권자|지상권자|권리자)\s*(.*)$"
)


def _clean(text: str) -> str:
    return RESIDENT_REGISTRATION_NUMBER.sub(_MASKED_REGISTRATION_NUMBER, text)


def _bands(tokens: list[Line]) -> list[tuple[float, list[Line]]]:
    bands: list[list] = []
    for token in sorted(tokens, key=lambda item: (item.cy, item.box[0])):
        if bands and abs(token.cy - bands[-1][0]) < 0.008:
            bands[-1][1].append(token)
        else:
            bands.append([token.cy, [token]])
    return [(cy, sorted(items, key=lambda item: item.box[0])) for cy, items in bands]


def _row_text(items: list[Line]) -> str:
    # OCR이 한 단어를 조각으로 나누므로, 조각 사이 간격이 좁으면 붙여 쓴다.
    text = ""
    for index, item in enumerate(items):
        if index and item.box[0] - items[index - 1].box[2] > 0.007:
            text += " "
        text += item.text
    return _clean(text)


def _rows(tokens: list[Line]) -> list[str]:
    return [_row_text(items) for _, items in _bands(tokens)]


def _column_index(token: Line, dividers: tuple[float, ...]) -> int:
    center = (token.box[0] + token.box[2]) / 2
    return sum(center >= divider for divider in dividers)


def _column(tokens: list[Line], dividers: tuple[float, ...], index: int) -> list[Line]:
    return [token for token in tokens if _column_index(token, dividers) == index]


def _field(value, tokens: list[Line]):
    if value is None or not tokens:
        return field(None, [])
    ordered = sorted(tokens, key=lambda item: (item.page, item.cy, item.box[0]))
    box = (
        min(item.box[0] for item in ordered),
        min(item.box[1] for item in ordered),
        max(item.box[2] for item in ordered),
        max(item.box[3] for item in ordered),
    )
    text = _clean(" ".join(item.text for item in ordered))
    return field(value, [Line(text, ordered[0].page, box)])


def _marker(items: list[Line]) -> str | None:
    text = compact(" ".join(item.text for item in items))
    if not text or text[0] not in "【[":
        return None
    head = text[:8]
    for label, section in _SECTION_MARKERS.items():
        if label in head:
            return section
    return None


def _is_footer(items: list[Line]) -> bool:
    text = compact(" ".join(item.text for item in items))
    return any(word in text for word in _FOOTER_WORDS)


def _entry_blocks(
    tokens: list[Line], dividers: tuple[float, ...]
) -> list[tuple[str, list[Line]]]:
    numbers = sorted(
        (
            token
            for token in tokens
            if _column_index(token, dividers) == 0
            and _ENTRY_NUMBER.fullmatch(compact(token.text))
        ),
        key=lambda item: item.cy,
    )
    blocks = []
    for index, anchor in enumerate(numbers):
        top = anchor.cy - 0.010
        bottom = numbers[index + 1].cy - 0.010 if index + 1 < len(numbers) else 2.0
        block = [token for token in tokens if top <= token.cy < bottom]
        blocks.append((compact(anchor.text), block))
    return blocks


def _split_tables(lines_tokens: list[Line]) -> dict[str, list[tuple[str, list[Line]]]]:
    tables: dict[str, list[tuple[str, list[Line]]]] = {
        "title": [],
        "gapgu": [],
        "eulgu": [],
    }
    current = None
    for page in sorted({token.page for token in lines_tokens}):
        page_tokens = [
            token
            for token in lines_tokens
            if token.page == page and compact(token.text) not in _WATERMARK
        ]
        bands = _bands(page_tokens)
        footer_y = min((cy for cy, items in bands if _is_footer(items)), default=2.0)
        markers = [(cy, _marker(items)) for cy, items in bands if _marker(items)]
        points = [(0.0, current)] + markers
        for index, (start, section) in enumerate(points):
            if section is None:
                continue
            end = points[index + 1][0] if index + 1 < len(points) else footer_y
            end = min(end, footer_y)
            scoped = [token for token in page_tokens if start <= token.cy < end]
            header_ys = [
                cy
                for cy, items in _bands(scoped)
                if any(word in compact(" ".join(i.text for i in items)) for word in _HEADER_WORDS)
            ]
            body_start = (max(header_ys) + 0.012) if header_ys else start + 0.014
            body = [token for token in scoped if token.cy > body_start]
            dividers = _TITLE_COLUMNS if section == "title" else _RIGHTS_COLUMNS
            tables[section].extend(_entry_blocks(body, dividers))
        if markers:
            current = markers[-1][1]
    return tables


def _parse_date(tokens: list[Line]):
    # OCR 조각 순서가 아니라 화면 위치(줄, 좌우) 순서로 읽어 날짜 조각이 섞이지 않게 한다.
    match = _DATE.search(" ".join(_rows(tokens)))
    if match is None:
        return field(None, [])
    try:
        value = date(*map(int, match.groups()))
    except ValueError:
        return field(None, [])
    return _field(value, tokens)


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
        match = re.match(r"^\[?\s*(?:집합)?건물\s*\]?\s*(.+)$", token)
        if match and match[1].strip(" -"):
            return field(match[1].strip(), [line])
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


def _detail_address(blocks: list[tuple[str, list[Line]]]):
    """표제부 소재지번 열의 [도로명주소] 표기 값을 읽는다."""

    for _, block in blocks:
        # 소재지번 열(2)과 건물내역 열(3)만 본다. 등기원인 열의 "도로명주소"는 제외한다.
        for column_index in (2, 3):
            bands = _bands(_column(block, _TITLE_COLUMNS, column_index))
            for index, (cy, items) in enumerate(bands):
                row = _row_text(items)
                if "도로명주소" not in compact(row):
                    continue
                value = re.sub(r"^\[?\s*도로명\s*주소\s*\]?\s*", "", row).strip()
                used = list(items)
                if not value:
                    following = [
                        (next_cy, next_items)
                        for next_cy, next_items in bands[index + 1 :]
                        if 0 < next_cy - cy < 0.08
                    ]
                    value = " ".join(_row_text(next_items) for _, next_items in following).strip()
                    used += [item for _, next_items in following for item in next_items]
                if value:
                    return _field(value, used)
    return field(None, [])


def _property_descriptions(blocks: list[tuple[str, list[Line]]]):
    result = []
    for order, (_, block) in enumerate(blocks, 1):
        # 열 경계를 넘어 한 줄로 이어 붙지 않도록 열 단위로 위에서 아래로 읽는다.
        columns = (
            "\n".join(_rows(_column(block, _TITLE_COLUMNS, index)))
            for index in range(len(_TITLE_COLUMNS) + 1)
        )
        text = "\n\n".join(column for column in columns if column.strip()).strip()
        if text:
            result.append(
                {
                    "item_order": order,
                    "original_text": text,
                    "source": source(block),
                }
            )
    return result


def _purpose(block: list[Line]):
    tokens = _column(block, _RIGHTS_COLUMNS, 1)
    value = "".join(_rows(tokens)).replace(" ", "").strip() or None
    return _field(value, tokens)


def _cause(block: list[Line]):
    tokens = _column(block, _RIGHTS_COLUMNS, 3)
    value = _DATE.sub("", " ".join(token.text for token in tokens)).strip() or None
    return _field(value, tokens) if value else None


def _name_after_label(row: str, labels: str) -> str | None:
    match = re.search(rf"(?:{labels})\s*(.*)", row)
    if match is None:
        return None
    words = []
    for word in match[1].split():
        # OCR이 이름과 주민번호를 붙여 읽을 수 있어 단어 앞의 글자 부분만 이름으로 본다.
        letters = re.match(r"[가-힣A-Za-z]+", word)
        if letters is None:
            break
        words.append(letters[0])
        if letters[0] != word:
            break
    return " ".join(words) or None


def _owner_from_block(block: list[Line]):
    rights = _column(block, _RIGHTS_COLUMNS, 4)
    rows = _rows(rights)
    row = next((item for item in rows if re.search(r"소유자|공유자", item)), None)
    if row is None or _OWNERSHIP_EXCLUDED.search("".join(_rows(_column(block, _RIGHTS_COLUMNS, 1)))):
        return None
    name = _name_after_label(row, "소유자|공유자")
    share_match = re.search(r"(\d+\s*분의\s*\d+)", " ".join(rows))
    return {
        "name": _field(name, rights) if name else field(None, []),
        "ownership_share": _field(share_match[1], rights) if share_match else None,
        "registration_date": _parse_date(_column(block, _RIGHTS_COLUMNS, 2)),
        "registration_cause": _cause(block),
        "status": "unknown",
    }


def _encumbrance(section: str, number: str, block: list[Line]):
    rights = _column(block, _RIGHTS_COLUMNS, 4)
    rows = _rows(rights)
    amount_match = re.search(r"채권최고액(?:금)?([0-9,]+)원", compact(" ".join(rows)))
    amount = int(amount_match[1].replace(",", "")) if amount_match else None
    holder = debtor = None
    for row in rows:
        label = _HOLDER_LABEL.match(row)
        if label and holder is None:
            value = re.sub(r"\[등록번호 마스킹\]|[\d-]+$", "", label[2]).strip()
            holder = value or None
        if row.startswith("채무자") and debtor is None:
            debtor = _name_after_label(row, "채무자")
    rank_token = next(token for token in block if compact(token.text) == number)
    return {
        "section": section,
        "rank_number": _field(number, [rank_token]),
        "registration_purpose": _purpose(block),
        "registration_date": _parse_date(_column(block, _RIGHTS_COLUMNS, 2)),
        "holder": _field(holder, rights) if holder else None,
        "debtor": _field(debtor, rights) if debtor else None,
        "maximum_claim_amount": _field(amount, rights) if amount is not None else None,
        "raw_text": "\n".join(_rows(block)).strip(),
        "source": source(block),
        "status": "unknown",
    }


def extract_registry_document_ai(document: dict) -> RegistryExtraction:
    lines = load_lines(document)
    has_tokens = any(page.get("tokens") for page in document.get("pages", []))
    tokens = load_lines(document, "tokens") if has_tokens else lines
    for item in [*lines, *tokens]:
        item.text = _clean(item.text)
    tables = _split_tables(tokens)
    warnings = [
        "registry_entry_status_requires_review: 말소선과 변경 이력을 자동 확정하지 않음"
    ]
    owners = []
    encumbrances = []
    for number, block in tables["gapgu"]:
        owner = _owner_from_block(block)
        if owner is not None:
            owners.append(owner)
        else:
            encumbrances.append(_encumbrance("gapgu", number, block))
    for number, block in tables["eulgu"]:
        encumbrances.append(_encumbrance("eulgu", number, block))
    amended = {
        item["rank_number"]["value"].split("-")[0]
        for item in encumbrances
        if "-" in (item["rank_number"]["value"] or "")
        and _AMENDMENT_PURPOSE.search(item["registration_purpose"]["value"] or "")
    }
    if amended:
        ranks = ", ".join(sorted(amended, key=int))
        warnings.append(
            f"registry_amendment_requires_review: {ranks}번 권리에 변경·말소 부기가 있어 "
            "본 등기의 최고액·채무자 등이 달라졌을 수 있음"
        )
    if "현재유효사항" not in compact(" ".join(line.text for line in lines)):
        warnings.append(
            "registry_scope_not_current: 제목에서 '현재 유효사항'을 읽지 못해 "
            "소유권·권리 상태를 확정할 수 없음"
        )
    if not owners:
        warnings.append("registry_owner_missing: 갑구 소유자 확인 필요")
    if not encumbrances:
        warnings.append("registry_encumbrance_missing: 갑구·을구 권리 항목 확인 필요")
    return RegistryExtraction.model_validate(
        {
            "issued_at": _issued_at(lines),
            "unique_property_number": _unique_property_number(lines),
            "address": _document_address(lines),
            "detail_address": _detail_address(tables["title"]),
            "property_description": _property_descriptions(tables["title"]),
            "owners": owners,
            "encumbrances": encumbrances,
            "warnings": warnings,
        }
    )
