# __init__.py
"""내용증명 생성용 상태, 포트, 노드와 LangGraph 구성을 제공한다."""

from typing import TYPE_CHECKING, Any

from ml.agent.certified_letter.normalizer import normalize_request
from ml.agent.certified_letter.state import (
    CertifiedLetterRequestData,
    ContractData,
    NormalizedCertifiedLetterRequest,
    PartyData,
    PaymentData,
    PeriodData,
    PropertyData,
)


if TYPE_CHECKING:
    from ml.agent.certified_letter.factory import create_certified_letter_workflow


def __getattr__(name: str) -> Any:
    if name == "create_certified_letter_workflow":
        from ml.agent.certified_letter.factory import create_certified_letter_workflow

        return create_certified_letter_workflow
    raise AttributeError(name)

__all__ = [
    "CertifiedLetterRequestData",
    "ContractData",
    "NormalizedCertifiedLetterRequest",
    "PartyData",
    "PaymentData",
    "PeriodData",
    "PropertyData",
    "create_certified_letter_workflow",
    "normalize_request",
]
