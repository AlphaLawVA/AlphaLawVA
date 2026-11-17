# normalizer.py
"""
Description: 검증된 내용증명 요청을 생성·검색에서 사용할 내부 값으로 정규화한다.
사용자가 지정한 기한만 보존하고 기한이 없으면 명시적으로 비워 둔다.
Author: choeminju
Date: 2026-10-22
Before:
    - 공개 요청을 프롬프트에 바로 전달할 수밖에 없는 상태.
After:
    - 공백, 선택값과 사용자 지정 기한이 일관된 내부 요청으로 변환됨.
"""

from __future__ import annotations

from dataclasses import replace

from ml.agent.certified_letter.state import (
    CertifiedLetterRequestData,
    ContractData,
    NormalizedCertifiedLetterRequest,
    PartyData,
    PropertyData,
)
from ml.agent.certified_letter.value_normalizer import normalize_text


def normalize_request(
    request: CertifiedLetterRequestData,
) -> NormalizedCertifiedLetterRequest:
    contract = _normalize_contract(request.contract)
    purpose = _normalize_text_value(request.purpose)
    facts = _normalize_text_value(request.facts)

    return NormalizedCertifiedLetterRequest(
        sender_role=request.sender_role,
        purpose=purpose,
        facts=facts,
        requested_deadline=request.requested_deadline,
        deadline_text=(
            request.requested_deadline.isoformat()
            if request.requested_deadline is not None
            else None
        ),
        contract=contract,
    )


def _normalize_contract(contract: ContractData) -> ContractData:
    return replace(
        contract,
        contract_type=contract.contract_type.strip(),
        property=_normalize_property(contract.property),
        lessor=_normalize_party(contract.lessor),
        lessee=_normalize_party(contract.lessee),
        seller=_normalize_party(contract.seller),
        buyer=_normalize_party(contract.buyer),
        special_terms=tuple(
            normalized
            for term in contract.special_terms
            if (normalized := _normalize_text_optional(term)) is not None
        ),
    )


def _normalize_property(value: PropertyData) -> PropertyData:
    return replace(
        value,
        address=_normalize_single_line(value.address),
        detail_address=_normalize_optional(value.detail_address),
        building_type=_normalize_optional(value.building_type),
    )


def _normalize_party(value: PartyData | None) -> PartyData | None:
    if value is None:
        return None
    return replace(
        value,
        name=_normalize_single_line(value.name),
        address=_normalize_optional(value.address),
        detail_address=_normalize_optional(value.detail_address),
        phone_number=_normalize_optional(value.phone_number),
    )


def _normalize_multiline(value: str) -> str:
    lines = [" ".join(line.split()) for line in value.replace("\r\n", "\n").split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _normalize_text_value(value: str) -> str:
    return normalize_text(_normalize_multiline(value)).text


def _normalize_text_optional(value: str | None) -> str | None:
    normalized = _normalize_optional(value)
    return normalize_text(normalized).text if normalized is not None else None


def _normalize_single_line(value: str) -> str:
    return " ".join(value.split())


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _normalize_single_line(value)
    return normalized or None
