# value_normalizer.py
"""
Description: 내용증명 생성 전후의 금액·날짜 표기를 결정론적으로 통일한다.
모델 출력 누락을 감시하되 사용자 결과나 재생성 경로에는 영향을 주지 않는다.
Author: AlphaLawVA Team
Date: 2026-11-16
Before:
    - 모델이 입력·출력의 금액과 날짜 표기를 제각각 반환할 수 있는 상태.
After:
    - 금액은 쉼표 포함 원 단위, 연도가 있는 날짜는 한국어 날짜 형식으로 통일됨.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from ml.agent.certified_letter.prompts import PromptBundle
from ml.agent.certified_letter.state import LetterDraftData


_NUMBER = r"[0-9][0-9,]*(?:\.[0-9]+)?"
_INTEGER = r"[0-9][0-9,]*"
_LOWER_UNIT_COEFFICIENT = (
    rf"(?:{_INTEGER}\s*(?:천|백|십)\s*)+(?:{_INTEGER})?"
)
_MIXED_EOK_RE = re.compile(
    rf"(?<![0-9가-힣])(?P<eok>{_NUMBER})\s*억\s*"
    rf"(?P<man>{_LOWER_UNIT_COEFFICIENT})\s*만\s*원"
)
_MIXED_MAN_RE = re.compile(
    rf"(?<![0-9가-힣])(?P<coefficient>{_LOWER_UNIT_COEFFICIENT})\s*만\s*원"
)
_MIXED_WON_RE = re.compile(
    rf"(?<![0-9가-힣])(?P<coefficient>{_LOWER_UNIT_COEFFICIENT})\s*원"
)
_AMOUNT_EOK_RE = re.compile(
    r"(?<![0-9가-힣])"
    r"(?P<eok>[0-9][0-9,]*(?:\.[0-9]+)?)\s*억"
    r"(?:\s*(?P<man>[0-9][0-9,]*(?:\.[0-9]+)?)\s*만)?\s*원"
)
_AMOUNT_MAN_RE = re.compile(
    r"(?<![0-9가-힣])(?P<man>[0-9][0-9,]*(?:\.[0-9]+)?)\s*만\s*원"
)
_AMOUNT_WON_RE = re.compile(r"(?<![0-9.])(?P<won>[0-9][0-9,]*)\s*원")
_FULL_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})\s*[-.]\s*(?P<month>\d{1,2})\s*[-.]\s*(?P<day>\d{1,2})(?!\d)"
)
_KOREAN_FULL_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})년\s*(?P<month>\d{1,2})월\s*(?P<day>\d{1,2})일"
)
_PARTIAL_DATE_RE = re.compile(r"(?<!\d)(?P<month>\d{1,2})월\s*(?P<day>\d{1,2})일")
_PAYMENT_FIELDS = {
    "deposit",
    "monthly_rent",
    "sale_price",
    "down_payment",
    "interim_payment",
    "interim_payment_2",
    "balance",
}
_DATE_FIELDS = {
    "contract_date",
    "handover_date",
    "ownership_transfer_date",
    "start_date",
    "end_date",
}


@dataclass(frozen=True)
class TextNormalization:
    text: str
    transformations: int


@dataclass(frozen=True)
class DraftNormalization:
    draft: LetterDraftData
    transformations: int


def normalize_text(value: str) -> TextNormalization:
    """Normalize explicit won amounts and full dates without guessing missing years."""

    normalized, amount_count = _normalize_amounts(value)
    normalized, date_count = _normalize_full_dates(normalized)
    return TextNormalization(
        text=normalized,
        transformations=amount_count + date_count,
    )


def format_money(value: int) -> str:
    return f"{value:,}원"


def format_date(value: date) -> str:
    return f"{value.year}년 {value.month}월 {value.day}일"


def normalize_prompt_bundle(prompt: PromptBundle) -> PromptBundle:
    """Normalize only the model payload while preserving the version and system prompt."""

    try:
        payload = json.loads(prompt.user)
    except json.JSONDecodeError:
        return prompt
    if not isinstance(payload, dict):
        return prompt
    normalized = dict(payload)
    for key in ("user_request", "contract_facts"):
        value = payload.get(key)
        if isinstance(value, dict):
            normalized[key] = _normalize_payload_value(value)
    return replace(
        prompt,
        user=json.dumps(normalized, ensure_ascii=False, indent=2),
    )


def count_prompt_transformations(prompt: PromptBundle) -> int:
    """Return the deterministic count that will be applied before a model call."""

    try:
        payload = json.loads(prompt.user)
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    count = 0
    for key in ("user_request", "contract_facts"):
        value = payload.get(key)
        if isinstance(value, dict):
            _, nested_count = _normalize_payload_value_with_count(value)
            count += nested_count
    return count


def normalize_draft(draft: LetterDraftData) -> DraftNormalization:
    body = normalize_text(draft.body)
    strategy_summary = normalize_text(draft.strategy_summary)
    actions = tuple(normalize_text(item) for item in draft.follow_up_actions)
    warnings = tuple(normalize_text(item) for item in draft.warnings)
    return DraftNormalization(
        draft=replace(
            draft,
            body=body.text,
            strategy_summary=strategy_summary.text,
            follow_up_actions=tuple(item.text for item in actions),
            warnings=tuple(item.text for item in warnings),
        ),
        transformations=(
            body.transformations
            + strategy_summary.transformations
            + sum(item.transformations for item in actions)
            + sum(item.transformations for item in warnings)
        ),
    )


def extract_fact_values(value: str) -> tuple[str, ...]:
    """Extract normalized money and date values from facts for log-only omission checks."""

    normalized = normalize_text(value).text
    values: list[str] = []
    values.extend(match.group(0) for match in _AMOUNT_WON_RE.finditer(normalized))
    full_date_matches = tuple(_KOREAN_FULL_DATE_RE.finditer(normalized))
    for match in full_date_matches:
        values.append(match.group(0))
    for match in _PARTIAL_DATE_RE.finditer(normalized):
        if not any(
            full_date.start() <= match.start() < full_date.end()
            for full_date in full_date_matches
        ):
            values.append(match.group(0))
    return tuple(dict.fromkeys(values))


def missing_fact_values(*, facts: str, body: str) -> tuple[str, ...]:
    normalized_body = normalize_text(body).text
    return tuple(value for value in extract_fact_values(facts) if value not in normalized_body)


def _normalize_payload_value(value: Any) -> Any:
    normalized, _ = _normalize_payload_value_with_count(value)
    return normalized


def _normalize_payload_value_with_count(value: Any) -> tuple[Any, int]:
    if isinstance(value, list):
        normalized_items = [_normalize_payload_value_with_count(item) for item in value]
        return (
            [item for item, _count in normalized_items],
            sum(count for _item, count in normalized_items),
        )
    if not isinstance(value, dict):
        return value, 0

    normalized: dict[str, Any] = {}
    transformations = 0
    for key, item in value.items():
        if key in _PAYMENT_FIELDS and isinstance(item, int) and not isinstance(item, bool):
            normalized[key] = format_money(item)
            transformations += 1
        elif key in _DATE_FIELDS and isinstance(item, str):
            item_normalization = normalize_text(item)
            normalized[key] = item_normalization.text
            transformations += item_normalization.transformations
        elif isinstance(item, str):
            item_normalization = normalize_text(item)
            normalized[key] = item_normalization.text
            transformations += item_normalization.transformations
        elif isinstance(item, (dict, list)):
            normalized[key], nested_count = _normalize_payload_value_with_count(item)
            transformations += nested_count
        else:
            normalized[key] = item
    return normalized, transformations


def _normalize_amounts(value: str) -> tuple[str, int]:
    transformations = 0

    def replace_mixed_eok(match: re.Match[str]) -> str:
        nonlocal transformations
        eok = _parse_unit_value(match.group("eok"), 100_000_000)
        man_coefficient = _parse_lower_unit_coefficient(match.group("man"))
        if eok is None or man_coefficient is None:
            return match.group(0)
        normalized = format_money(eok + man_coefficient * 10_000)
        if normalized != match.group(0):
            transformations += 1
        return normalized

    def replace_mixed_man(match: re.Match[str]) -> str:
        nonlocal transformations
        coefficient = _parse_lower_unit_coefficient(match.group("coefficient"))
        if coefficient is None:
            return match.group(0)
        normalized = format_money(coefficient * 10_000)
        if normalized != match.group(0):
            transformations += 1
        return normalized

    def replace_mixed_won(match: re.Match[str]) -> str:
        nonlocal transformations
        coefficient = _parse_lower_unit_coefficient(match.group("coefficient"))
        if coefficient is None:
            return match.group(0)
        normalized = format_money(coefficient)
        if normalized != match.group(0):
            transformations += 1
        return normalized

    def replace_eok(match: re.Match[str]) -> str:
        nonlocal transformations
        eok = _parse_unit_value(match.group("eok"), 100_000_000)
        man = _parse_unit_value(match.group("man"), 10_000) if match.group("man") else 0
        if eok is None or man is None:
            return match.group(0)
        normalized = format_money(eok + man)
        if normalized != match.group(0):
            transformations += 1
        return normalized

    def replace_man(match: re.Match[str]) -> str:
        nonlocal transformations
        amount = _parse_unit_value(match.group("man"), 10_000)
        if amount is None:
            return match.group(0)
        normalized = format_money(amount)
        if normalized != match.group(0):
            transformations += 1
        return normalized

    def replace_won(match: re.Match[str]) -> str:
        nonlocal transformations
        number = match.group("won").replace(",", "")
        normalized = format_money(int(number))
        if normalized != match.group(0):
            transformations += 1
        return normalized

    normalized = _MIXED_EOK_RE.sub(replace_mixed_eok, value)
    normalized = _MIXED_MAN_RE.sub(replace_mixed_man, normalized)
    normalized = _MIXED_WON_RE.sub(replace_mixed_won, normalized)
    normalized = _AMOUNT_EOK_RE.sub(replace_eok, normalized)
    normalized = _AMOUNT_MAN_RE.sub(replace_man, normalized)
    normalized = _AMOUNT_WON_RE.sub(replace_won, normalized)
    return normalized, transformations


def _parse_unit_value(value: str | None, unit: int) -> int | None:
    if value is None:
        return None
    digits = value.replace(",", "")
    whole, separator, fraction = digits.partition(".")
    if not whole.isdigit() or (separator and (not fraction or not fraction.isdigit())):
        return None
    if not separator:
        return int(whole) * unit
    scale = 10 ** len(fraction)
    if unit % scale:
        return None
    return int(whole) * unit + int(fraction) * (unit // scale)


def _parse_lower_unit_coefficient(value: str) -> int | None:
    compact = re.sub(r"\s+", "", value).replace(",", "")
    position = 0
    total = 0
    previous_unit = 10_001
    for match in re.finditer(r"(?P<number>\d+)(?P<unit>천|백|십)", compact):
        if match.start() != position:
            return None
        unit = {"천": 1_000, "백": 100, "십": 10}[match.group("unit")]
        if unit >= previous_unit:
            return None
        total += int(match.group("number")) * unit
        previous_unit = unit
        position = match.end()
    if position == 0:
        return None
    if position < len(compact):
        remainder = compact[position:]
        if not remainder.isdigit() or previous_unit <= 1:
            return None
        total += int(remainder)
    return total


def _normalize_full_dates(value: str) -> tuple[str, int]:
    transformations = 0

    def replace_date(match: re.Match[str]) -> str:
        nonlocal transformations
        try:
            normalized = format_date(
                date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                )
            )
        except ValueError:
            return match.group(0)
        if normalized != match.group(0):
            transformations += 1
        return normalized

    return _FULL_DATE_RE.sub(replace_date, value), transformations
