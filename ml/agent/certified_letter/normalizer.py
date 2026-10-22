# normalizer.py
"""
Description: 검증된 내용증명 요청을 생성·검색에서 사용할 내부 값으로 정규화한다.
기한이 없을 때 역사적 사실과 구분되는 상대 기한 제안을 명시적으로 기록한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 공개 요청을 프롬프트에 바로 전달할 수밖에 없는 상태.
After:
    - 공백, 선택값과 기한 정책이 일관된 내부 요청으로 변환됨.
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


DEFAULT_RELATIVE_DEADLINE = "이 내용증명을 받은 날부터 7일 이내"
SUGGESTED_DEADLINE_WARNING = (
    "사용자가 이행 기한을 지정하지 않아 '수령 후 7일 이내'를 전략적 제안으로 "
    "사용했습니다. 발송 전 적절한 기한인지 확인해 주세요."
)


def normalize_request(
    request: CertifiedLetterRequestData,
) -> NormalizedCertifiedLetterRequest:
    contract = _normalize_contract(request.contract)
    purpose = _normalize_multiline(request.purpose)
    facts = _normalize_multiline(request.facts)

    if request.requested_deadline is None:
        return NormalizedCertifiedLetterRequest(
            purpose=purpose,
            facts=facts,
            requested_deadline=None,
            deadline_text=DEFAULT_RELATIVE_DEADLINE,
            is_deadline_suggested=True,
            contract=contract,
            warnings=(SUGGESTED_DEADLINE_WARNING,),
        )

    return NormalizedCertifiedLetterRequest(
        purpose=purpose,
        facts=facts,
        requested_deadline=request.requested_deadline,
        deadline_text=request.requested_deadline.isoformat(),
        is_deadline_suggested=False,
        contract=contract,
    )


def _normalize_contract(contract: ContractData) -> ContractData:
    return replace(
        contract,
        contract_type=contract.contract_type.strip(),
        property=_normalize_property(contract.property),
        lessor=_normalize_party(contract.lessor),
        lessee=_normalize_party(contract.lessee),
    )


def _normalize_property(value: PropertyData) -> PropertyData:
    return replace(
        value,
        address=_normalize_single_line(value.address),
        detail_address=_normalize_optional(value.detail_address),
        building_type=_normalize_optional(value.building_type),
    )


def _normalize_party(value: PartyData) -> PartyData:
    return replace(
        value,
        name=_normalize_single_line(value.name),
        address=_normalize_single_line(value.address),
        detail_address=_normalize_optional(value.detail_address),
        phone_number=_normalize_optional(value.phone_number),
    )


def _normalize_multiline(value: str) -> str:
    lines = [" ".join(line.split()) for line in value.replace("\r\n", "\n").split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _normalize_single_line(value: str) -> str:
    return " ".join(value.split())


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _normalize_single_line(value)
    return normalized or None
