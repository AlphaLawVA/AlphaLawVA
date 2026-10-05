# document_ai_extractor.py
"""
Description: 저장된 Document AI 좌표와 텍스트로 표준 계약 필드를 추출한다.
Author: ooheunsu
Date: 2026-10-05
Before:
    - 합성 계약서의 Document AI JSON이 준비됨.
After:
    - 기존 스키마의 확인 전 추출 결과와 불일치 경고가 반환됨.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ml.document_processing.contracts.extraction_schema import ContractExtraction
from ml.document_processing.contracts.privacy import mask_resident_registration_numbers


@dataclass
class Line:
    text: str
    page: int
    box: tuple[float, float, float, float]

    @property
    def cy(self):
        return (self.box[1] + self.box[3]) / 2


def compact(text):
    return re.sub(r"\s+", "", text)


def load_lines(document, level="lines"):
    text = document["text"]
    result = []
    for index, page in enumerate(document["pages"], 1):
        for item in page.get(level, []):
            layout = item["layout"]
            raw = "".join(
                text[int(s.get("startIndex", 0)) : int(s["endIndex"])]
                for s in layout["textAnchor"]["textSegments"]
            )
            poly = layout["boundingPoly"]
            vertices = poly.get("normalizedVertices")
            if vertices is None:
                vertices = [
                    {
                        "x": v.get("x", 0) / page["dimension"]["width"],
                        "y": v.get("y", 0) / page["dimension"]["height"],
                    }
                    for v in poly["vertices"]
                ]
            xs = [v.get("x", 0) for v in vertices]
            ys = [v.get("y", 0) for v in vertices]
            result.append(
                Line(
                    mask_resident_registration_numbers(raw.strip()),
                    index,
                    (min(xs), min(ys), max(xs), max(ys)),
                )
            )
    return sorted(result, key=lambda line: (line.page, line.cy, line.box[0]))


def row(lines, anchor, left=0, right=1):
    return sorted(
        [
            line
            for line in lines
            if line.page == anchor.page
            and abs(line.cy - anchor.cy) < 0.009
            and left <= line.box[0] < right
        ],
        key=lambda line: line.box[0],
    )


def joined(lines):
    return " ".join(line.text for line in lines)


def field(value, lines):
    if value is None:
        return {"value": None, "status": "missing"}
    return {
        "value": value,
        "raw_text": joined(lines),
        "status": "low_confidence",
        "source": source(lines),
    }


def source(lines):
    return {
        "page_number": lines[0].page,
        "bounding_box": {
            "x_min": min(item.box[0] for item in lines),
            "y_min": min(item.box[1] for item in lines),
            "x_max": max(item.box[2] for item in lines),
            "y_max": max(item.box[3] for item in lines),
        },
    }


def dates(lines):
    found = []
    # Reconstruct each physical row before parsing dates split into OCR fragments.
    pending = list(lines)
    rows = []
    while pending:
        group = row(pending, pending[0])
        rows.append(joined(group))
        pending = [item for item in pending if item not in group]
    for match in re.finditer(
        r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", " ".join(rows)
    ):
        try:
            found.append(date(*map(int, match.groups())).isoformat())
        except ValueError:
            pass
    return found


def korean_amount(text):
    token = compact(text)
    if not re.fullmatch(r"[일이삼사오육칠팔구십백천만억0-9]+", token):
        return None
    digits = dict(zip("일이삼사오육칠팔구", range(1, 10)))
    total = section = current = 0
    for char in token:
        if char.isdigit():
            current = current * 10 + int(char)
        elif char in digits:
            current = digits[char]
        elif char in "십백천":
            section += (current or 1) * {"십": 10, "백": 100, "천": 1000}[char]
            current = 0
        else:
            total += (section + current or 1) * {"만": 10000, "억": 100000000}[char]
            section = current = 0
    return total + section + current


def money(lines, label, warnings):
    text = compact(joined(lines))
    text = text.split(compact(label), 1)[-1]
    numeral = re.search(r"[₩W]([0-9,]+)", text)
    numeric = int(numeral[1].replace(",", "")) if numeral else None
    hangul = re.search(r"(?:금)?([일이삼사오육칠팔구십백천만억0-9]+)원", text)
    written = korean_amount(hangul[1]) if hangul else None
    if numeric is not None and written is not None and numeric != written:
        warnings.append(
            f"money_conflict:{label}: 숫자 {numeric}, 한글 {written}; 확인 필요"
        )
    return field(numeric if numeric is not None else written, lines)


def special_terms(lines, sale, warnings, tokens=None):
    if sale:
        start = next((item for item in lines if "제7조" in item.text), None)
        lines = [item for item in lines if start and item.cy > start.cy]
    else:
        end = next((item for item in lines if "계약을증명" in compact(item.text)), None)
        lines = [item for item in lines if end and item.cy < end.cy]
    if not sale and tokens:
        right = [
            item
            for item in lines
            if item.box[0] > 0.45 and re.match(r"^[1-9][.]", item.text)
        ]
        if right:
            boundary = min(item.box[0] for item in right)
            column_start = min(item.box[1] for item in right)
            columns = [[], []]
            for line in lines:
                contained = [
                    token
                    for token in tokens
                    if token.page == line.page
                    and line.box[0] - 0.002 <= token.box[0]
                    and token.box[2] <= line.box[2] + 0.002
                    and line.box[1] - 0.002 <= token.cy <= line.box[3] + 0.002
                ]
                if (
                    line.box[0] < boundary < line.box[2]
                    and contained
                    and line.cy >= column_start
                ):
                    for side in (0, 1):
                        group = [
                            token
                            for token in contained
                            if (token.box[0] >= boundary) == bool(side)
                        ]
                        if group:
                            columns[side].append(
                                combine(sorted(group, key=lambda t: t.box[0]))
                            )
                else:
                    columns[int(line.box[0] >= boundary)].append(line)
            lines = columns[0] + columns[1]
            warnings.append(
                "special_term_columns: 단어 좌표로 좌우 영역 분리; 경계 확인 필요"
            )
    # Read horizontally aligned number/body fragments as a row, not by top edge.
    pending = list(lines)
    physical_rows = []
    while pending:
        group = row(pending, pending[0])
        bounds = source(group)["bounding_box"]
        physical_rows.append(
            Line(
                joined(group),
                group[0].page,
                tuple(bounds[k] for k in ("x_min", "y_min", "x_max", "y_max")),
            )
        )
        pending = [item for item in pending if item not in group]
    lines = physical_rows if sale else lines
    blocks = []
    active = []
    number = None
    for line in lines:
        if dates([line]):
            break
        match = re.match(
            r"^\s*(?:([1-9])[.．)]|([①②③④⑤⑥⑦⑧⑨])|([1-9])\s+)\s*", line.text
        )
        if match:
            if active:
                blocks.append((number, active))
            number = (
                int(match[1] or match[3])
                if not match[2]
                else ord(match[2]) - ord("①") + 1
            )
            active = [Line(line.text[match.end() :], line.page, line.box)]
        elif active:
            if re.match(r"^[가-힣]{1,2}\s*[.]\s+\S", line.text):
                warnings.append(
                    "special_term_unassigned: 번호 판독 불가 원문=" + line.text
                )
                blocks.append((number, active))
                active = []
                number = None
                continue
            active.append(line)
    if active:
        blocks.append((number, active))
    if [n for n, _ in blocks] != list(range(1, len(blocks) + 1)):
        warnings.append(
            "special_term_numbering: 특약 번호 누락/순서 이상; 원문 확인 필요"
        )
    return [
        {"item_order": n, "original_text": joined(ls).strip(), "source": source(ls)}
        for n, ls in blocks
        if joined(ls).strip()
    ]


def combine(lines):
    bounds = source(lines)["bounding_box"]
    return Line(
        joined(lines),
        lines[0].page,
        tuple(bounds[k] for k in ("x_min", "y_min", "x_max", "y_max")),
    )


def brokers_from_lines(lines):
    brokers = []
    for office in [item for item in lines if "공인중개사사무소" in compact(item.text)]:
        left, right = (0, 0.475) if office.box[0] < 0.475 else (0.475, 1)
        candidates = [
            item
            for item in lines
            if left <= item.box[0] < right and office.cy <= item.cy < office.cy + 0.09
        ]
        name = re.search(r"([가-힣]+\s*공인\s*중개사\s*사무소)", office.text)
        broker = {"office_name": field(compact(name[1]) if name else None, [office])}
        registration = next((item for item in candidates if "가상" in item.text), None)
        if registration:
            group = [registration]
            match = re.search(r"가상[가-힣0-9\-]*", compact(registration.text))
            value = match[0].split("전화")[0] if match else None
            if value and not re.search(r"\d", value):
                continuation = next(
                    (
                        item
                        for item in candidates
                        if item.cy > registration.cy
                        and re.fullmatch(r"\d{4}-\d+", compact(item.text))
                    ),
                    None,
                )
                if continuation:
                    value += compact(continuation.text)
                    group.append(continuation)
            broker["office_registration_number"] = field(value, group)
        representative = next(
            (
                item
                for item in candidates
                if "서명" in item.text and "날인" in item.text
            ),
            None,
        )
        if representative:
            match = re.search(r"날인\s+([가-힣]{2,4})(?:\s|$)", representative.text)
            if match:
                broker["broker_in_charge"] = field(match[1], [representative])
        brokers.append(broker)
    return sorted(
        brokers, key=lambda b: b["office_name"]["source"]["bounding_box"]["x_min"]
    )


def sale_articles(lines):
    result = []
    headings = [item for item in lines if re.match(r"제\d+조", item.text)]
    for anchor in [
        item
        for item in lines
        if "따로정하는" in compact(item.text) and "[" in item.text
    ]:
        preceding = [item for item in headings if item.cy < anchor.cy]
        if not preceding:
            continue
        group = row(lines, anchor)
        text = re.sub(r"\[따로\s*정하는\s*(?:사항|기간)\]", "", joined(group)).strip()
        if text:
            result.append(
                {
                    "item_order": len(result) + 1,
                    "clause_number": re.match(r"제\d+조", preceding[-1].text)[0],
                    "original_text": text,
                    "source": source(group),
                }
            )
    return result


def extract_document_ai(document, contract_type):
    """Contract type is user-supplied metadata, never read from the gold answers."""
    if contract_type not in {"jeonse", "monthly_rent", "sale"}:
        raise ValueError("Unsupported contract type")
    lines = load_lines(document)
    first = [item for item in lines if item.page == 1]
    third = [item for item in lines if item.page == 3]
    warnings = [
        "실험용 표준 양식 추출; 모든 값은 사용자 확인 전 상태입니다.",
        "계약 유형은 사용자 제공 메타데이터이며 OCR 분류 성능이 아닙니다.",
    ]
    sale = contract_type == "sale"

    def find(label, pool=first):
        return next(
            (item for item in pool if compact(label) in compact(item.text)), None
        )

    def date_field(pool, index=0):
        values = dates(pool)
        return field(values[index] if len(values) > index else None, pool)

    address_line = next(
        (
            item
            for item in first
            if re.search(r"(?:특별시|광역시|\S+도).*?(?:로|길)", item.text)
        ),
        None,
    )
    address = detail = None
    if address_line:
        text = re.sub(
            r"^.*?((?:서울|부산|대구|인천|광주|대전|울산|세종|제주|경기|강원|충청|전라|경상).*)",
            r"\1",
            address_line.text,
        )
        parts = text.split(",", 1)
        address = parts[0].strip()
        detail = parts[1].strip() if len(parts) > 1 else None
    prop = {
        "address": field(address, [address_line]),
        "detail_address": field(detail, [address_line]),
    }
    areas = []
    for anchor in [item for item in first if "면적" in item.text]:
        group = row(first, anchor, anchor.box[0])
        text = joined(group).split("면적", 1)[-1]
        match = re.search(r"\d+(?:[.,]\s*\d+)?", text)
        value = None
        if match:
            raw = compact(match[0])
            if "," in raw:
                warnings.append("area_separator: 면적의 쉼표/소수점 확인 필요")
            else:
                value = float(raw)
                if value > 1000:
                    warnings.append("area_scale: 큰 면적 값; 단위 및 소수점 확인 필요")
        areas.append(field(value, group))
    if sale:
        prop["building_area_m2"] = areas[0] if areas else field(None, [])
    else:
        for key, value in zip(
            ("land_area_m2", "building_area_m2", "leased_area_m2"), areas
        ):
            prop[key] = value
        structure = find("구조")
        if structure:
            match = re.search(
                r"(철근콘크리트\S*)\s*(아파트|다세대주택|연립주택)", structure.text
            )
            if match:
                prop["building_structure"] = field(match[1], [structure])
                prop["building_use"] = field(match[2], [structure])
        leased = find("임차할부분")
        if leased:
            group = row(first, leased)
            match = re.search(r"(?:\d+층\s*)?(?:\d+동\s*)?\d+호\s*전부", joined(group))
            prop["leased_part_description"] = field(match[0] if match else None, group)
        land = find("토지")
        if land:
            group = row(first, land)
            prop["land_category"] = field(
                "대" if any(item.text.strip() == "대" for item in group) else None,
                group,
            )

    start = find("제1조")
    end = find("제2조")
    payment_lines = [
        item for item in first if start and end and start.cy < item.cy < end.cy
    ]
    labels = (
        {
            "sale_price": "매매대금",
            "down_payment": "계약금",
            "remaining_balance": "잔금",
        }
        if sale
        else {
            "deposit": "보증금",
            "down_payment": "계약금",
            "remaining_balance": "잔금",
            "maintenance_fee": "정액인경우",
        }
    )
    if contract_type == "monthly_rent":
        labels["monthly_rent"] = "차임(월세)"
    payment = {"intermediate_payments": []}

    def payment_row(label):
        candidates = [
            item for item in payment_lines if compact(label) in compact(item.text)
        ]
        anchor = candidates[-1] if candidates else None
        return row(payment_lines, anchor) if anchor else []

    for key, label in labels.items():
        group = payment_row(label)
        payment[key] = money(group, label, warnings)
    for label in ("1차중도금", "2차중도금") if sale else ("중도금",):
        group = payment_row(label)
        payment["intermediate_payments"].append(
            {
                "label": label,
                "amount": money(group, label, warnings),
                "due_date": date_field(group),
            }
        )
    payment["remaining_balance_date"] = date_field(payment_row("잔금"))
    if contract_type == "monthly_rent":
        group = payment_row("차임(월세)")
        match = re.search(r"매월\s*(\d+)\s*일", joined(group))
        payment["monthly_rent_payment_day"] = field(
            f"매월 {match[1]}일" if match else None, group
        )
        payment["payment_timing"] = field("unspecified", group)

    parties = []
    for label, role in (
        (("매도인", "seller"), ("매수인", "buyer"))
        if sale
        else (("임대인", "lessor"), ("임차인", "lessee"))
    ):
        name = None
        anchor = find(label)
        group = row(first, anchor) if anchor else []
        if sale:
            candidates = [
                item for item in group if re.fullmatch(r"[가-힣]{2,4}", item.text)
            ]
            name = candidates[-1].text if candidates else None
        else:
            match = re.search(label + r"\s*\(\s*([가-힣]{2,4})", joined(group))
            name = match[1] if match else None
        parties.append({"role": role, "name": field(name, group)})

    result = {
        "contract_type": field(contract_type, first[:1]),
        "contract_date": date_field(third),
        "property": prop,
        "parties": parties,
        "payment": payment,
        "articles": [],
        "special_terms": special_terms(
            third, sale, warnings, load_lines(document, "tokens")
        ),
        "brokers": [] if sale else brokers_from_lines(third),
        "disclosures": {},
        "warnings": warnings,
    }
    if sale:
        second = [item for item in lines if item.page == 2]
        result["articles"] = sale_articles(second)
        registry = find("등기부등본", second)
        if registry and re.search(r"[✓✔☑]", registry.text):
            result["disclosures"]["registry_checked"] = field(True, [registry])
        for key, label in (
            ("ownership_transfer_date", "서류를주는날"),
            ("delivery_date", "넘겨주는날"),
        ):
            anchor = find(label, second)
            result[key] = date_field(row(second, anchor) if anchor else [])
    else:
        after = find("제3조")
        period = [
            item
            for item in first
            if end and after and end.cy - 0.009 <= item.cy < after.cy - 0.009
        ]
        # A missing start date must not shift the end date into the start field.
        divider = next((item for item in period if "인도일로부터" in item.text), None)
        before = [item for item in period if divider and item.cy < divider.cy - 0.009]
        after_lines = [
            item for item in period if divider and item.cy >= divider.cy - 0.009
        ]
        result.update(
            start_date=date_field(before),
            delivery_date=date_field(before),
            end_date=date_field(after_lines),
        )
    warnings.append(
        "checkbox_review: 명시적 체크 기호 없는 선택 및 서명 여부는 자동 판정하지 않음"
    )
    validate_amounts(result)
    return ContractExtraction.model_validate(result)


def validate_amounts(result):
    payment = result["payment"]
    total = (payment.get("sale_price") or payment.get("deposit") or {}).get("value")
    parts = [
        (payment.get(k) or {}).get("value")
        for k in ("down_payment", "remaining_balance")
    ]
    parts.extend(p["amount"].get("value") for p in payment["intermediate_payments"])
    if total is not None and all(p is not None for p in parts) and sum(parts) != total:
        result["warnings"].append(
            f"payment_sum: 지급액 합계 {sum(parts)} != 총액 {total}; 자동 보정하지 않음"
        )
