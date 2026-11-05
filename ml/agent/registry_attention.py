# registry_attention.py
"""
Description: 등본에서 확인이 필요한 항목을 규칙으로 찾아 고정 안내 문구로 만든다.
안내는 모두 확인을 요청하는 문구이며 안전·위험 판정이나 법적 결론을 내리지 않는다.
Author: ooheunsu
Date: 2026-11-04
Before:
    - 압류·경매·가등기·임차권등기·근린생활시설 신호와 특약 제안 안내가 없는 상태.
After:
    - 확정 등본과 계약서를 규칙으로 대조해 확인 필요 항목과 특약·세금 안내를 생성함.
"""

from __future__ import annotations

import re

from api.models.contracts import ContractReviewItem, ReviewItemStatus
from api.models.registry_snapshots import (
    ConfirmedRegistrySnapshot,
    RegistryEncumbrance,
    RegistryEntryStatus,
)

ATTENTION_DISCLAIMER = "이 안내는 법적 판단이 아니며, 필요하면 전문가에게 확인하세요."

_ATTENTION_CATEGORY = "registry_attention"
_CROSSCHECK_CATEGORY = "contract_registry_crosscheck"
_SUGGESTION_CATEGORY = "special_term_suggestion"

_RIGHTS_CHANGE_TERM = re.compile(
    r"권리\s*변동|(?:근?저당권|담보권)[을를]?\s*설정"
)
_GUARANTEE_TERM = re.compile(r"보증보험|보증금\s*반환\s*보증|반환보증")

_RIGHTS_CHANGE_CLAUSE = (
    "임대인은 잔금 지급일 다음 날까지 등기부등본상 저당권 설정 등 어떠한 권리 "
    "변동도 일으키지 않는다. 이를 어긴 경우 임차인은 계약을 해제할 수 있고, "
    "임대인은 임차인이 지급한 계약금, 보증금 등 일체의 금액을 반환한다."
)
_GUARANTEE_CLAUSE = (
    "임차인의 귀책 없이 전세보증금반환보증 가입이 거절된 경우, 임차인은 계약을 "
    "해제할 수 있고, 임대인은 임차인이 지급한 계약금, 보증금 등 일체의 금액을 "
    "즉시 반환한다."
)


def _won(amount: int) -> str:
    return f"{amount:,}원"


def _with_disclaimer(message: str) -> str:
    return f"{message} {ATTENTION_DISCLAIMER}"


def _item(
    item_id: str,
    category: str,
    label: str,
    message: str,
    original_text: str | None = None,
) -> ContractReviewItem:
    return ContractReviewItem(
        item_id=item_id,
        category=category,
        label=label,
        original_text=original_text,
        status=ReviewItemStatus.NEEDS_INPUT,
        message=_with_disclaimer(message),
    )


def _is_active_entry(encumbrance: RegistryEncumbrance) -> bool:
    # 말소로 확정되지 않은 본 등기만 대상으로 한다(4-1 같은 부기 등기는 제외).
    return (
        encumbrance.status != RegistryEntryStatus.CANCELLED
        and "-" not in encumbrance.rank_number
    )


def _rank_text(entries: list[RegistryEncumbrance]) -> str:
    return ", ".join(f"{entry.rank_number}순위" for entry in entries)


def _raw_text(entries: list[RegistryEncumbrance]) -> str:
    return "\n".join(entry.raw_text for entry in entries)


def build_registry_attention_items(
    contract_snapshot,
    registry: ConfirmedRegistrySnapshot,
) -> list[ContractReviewItem]:
    """등본 신호와 계약서 교차 안내를 확인 필요 항목으로 만든다."""

    contract = contract_snapshot.contract
    is_lease = contract.contract_type.value != "sale"
    deposit = contract.lease_deposit.amount if is_lease else None
    active = [entry for entry in registry.encumbrances if _is_active_entry(entry)]
    items: list[ContractReviewItem] = []

    def matching(keyword: str) -> list[RegistryEncumbrance]:
        return [entry for entry in active if keyword in entry.registration_purpose]

    seizures = matching("압류")
    if seizures:
        items.append(
            _item(
                "attention_seizure",
                _ATTENTION_CATEGORY,
                f"압류·가압류 ({_rank_text(seizures)})",
                "압류 또는 가압류가 등기되어 있습니다. 말소 여부와 채권 내용을 "
                "확인해 주세요.",
                _raw_text(seizures),
            )
        )

    auctions = matching("경매")
    if auctions:
        items.append(
            _item(
                "attention_auction",
                _ATTENTION_CATEGORY,
                f"경매개시결정 ({_rank_text(auctions)})",
                "경매개시결정이 등기되어 있습니다. 경매 진행 상황을 확인해 주세요.",
                _raw_text(auctions),
            )
        )

    provisional = matching("가등기")
    if provisional:
        items.append(
            _item(
                "attention_provisional_registration",
                _ATTENTION_CATEGORY,
                f"가등기 ({_rank_text(provisional)})",
                "가등기가 있습니다. 가등기의 종류, 순위, 본등기 여부를 확인해 주세요.",
                _raw_text(provisional),
            )
        )

    lease_registrations = matching("임차권")
    if lease_registrations:
        items.append(
            _item(
                "attention_lease_registration",
                _ATTENTION_CATEGORY,
                f"임차권등기 ({_rank_text(lease_registrations)})",
                "임차권등기가 있습니다. 이전 임차인과 관련된 등기이므로 말소·정산 "
                "여부를 확인해 주세요.",
                _raw_text(lease_registrations),
            )
        )

    mortgages = matching("근저당")
    for mortgage in mortgages:
        amount_text = (
            f"채권최고액 {_won(mortgage.maximum_claim_amount)}"
            if mortgage.maximum_claim_amount is not None
            else "채권최고액은 원문에서 확인"
        )
        deposit_text = f", 보증금 {_won(deposit)}" if deposit is not None else ""
        items.append(
            _item(
                f"attention_mortgage_{mortgage.rank_number}",
                _ATTENTION_CATEGORY,
                f"근저당 ({mortgage.rank_number}순위)",
                f"이 집을 담보로 한 근저당이 설정되어 있습니다({amount_text}"
                f"{deposit_text}). 내용을 확인해 주세요.",
                mortgage.raw_text,
            )
        )

    commercial = any(
        "근린생활시설" in description.original_text
        for description in registry.property_description
    )
    if commercial:
        message = (
            "등기부상 용도가 근린생활시설입니다. 실제 주거용으로 사용할 수 있는지, "
            "전입신고가 가능한지 확인해 주세요."
        )
        if is_lease:
            message += (
                " 보증기관마다 대상 주택과 가입 요건이 다를 수 있으므로, 해당 "
                "주택의 전세보증금반환보증 가입 가능 여부를 기관에 확인해 주세요."
            )
        items.append(
            _item(
                "attention_commercial_use",
                _ATTENTION_CATEGORY,
                "건물 용도(근린생활시설)",
                message,
            )
        )
        if is_lease:
            items.append(
                _item(
                    "commercial_use_with_residential_lease",
                    _CROSSCHECK_CATEGORY,
                    "계약 용도·등본 건물 용도",
                    "계약서는 주택 임대차인데 등기부상 건물 용도는 근린생활시설입니다. "
                    "실제 주거 이용과 전입신고 가능 여부, 관련 보호 제도의 적용 요건을 "
                    "관계 기관이나 전문가에게 확인해 주세요.",
                )
            )

    term_texts = [term.original_text for term in contract.special_terms]
    if contract.disclosures.rights_change_restriction:
        term_texts.append(contract.disclosures.rights_change_restriction)
    has_restriction_term = any(_RIGHTS_CHANGE_TERM.search(text) for text in term_texts)
    if contract.disclosures.guarantee_insurance_term:
        term_texts.append(contract.disclosures.guarantee_insurance_term)
    has_guarantee_term = any(_GUARANTEE_TERM.search(text) for text in term_texts)

    if mortgages and not has_restriction_term:
        items.append(
            _item(
                "mortgage_without_restriction_term",
                _CROSSCHECK_CATEGORY,
                "근저당·권리변동 금지 특약",
                "근저당이 설정되어 있는데 특약에 권리변동 금지 내용이 보이지 않습니다. "
                "권리변동 금지 특약 추가를 검토해 보세요.",
            )
        )

    if is_lease:
        if not has_restriction_term:
            items.append(
                _item(
                    "suggest_rights_change_restriction",
                    _SUGGESTION_CATEGORY,
                    "권리변동 금지 특약 제안",
                    "계약일이나 잔금일 전후로 등기 내용이 바뀔 수 있으니, 잔금 지급 "
                    "다음 날까지 권리 변동이 없도록 하는 특약 추가를 검토해 보세요. "
                    f"특약 예시: \"{_RIGHTS_CHANGE_CLAUSE}\" 특약은 집행까지 시간이 "
                    "걸릴 수 있어 전세보증보험 가입도 함께 검토해 보세요.",
                )
            )
        if not has_guarantee_term:
            items.append(
                _item(
                    "suggest_guarantee_insurance",
                    _SUGGESTION_CATEGORY,
                    "보증보험 특약 제안",
                    "보증보험 가입이 되지 않는 집인데 계약을 권하는 경우를 대비해 "
                    "아래 특약 추가를 검토해 보세요. "
                    f"특약 예시: \"{_GUARANTEE_CLAUSE}\"",
                )
            )
        items.append(
            _item(
                "tax_arrears_check",
                _ATTENTION_CATEGORY,
                "국세·지방세 미납 확인",
                "일부 조세채권은 보증금 회수 순위에 영향을 줄 수 있고 등기부만으로 "
                "확인되지 않을 수 있습니다. 계약 전에 임대인의 미납국세·지방세와 "
                "우선순위를 관계 기관이나 전문가에게 확인해 주세요.",
            )
        )
    return items
