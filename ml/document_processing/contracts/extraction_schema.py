# extraction_schema.py
"""
Description: 계약서·등기부·중개대상물 확인설명서 OCR 결과와 최종 확인
스냅샷의 버전형 JSON 구조를 정의하고 개인정보 저장을 차단한다.
Author: ooheunsu
Date: 2026-09-20
Before:
    - OCR 결과를 저장할 ERD JSON 컬럼은 있지만 내부 필드 규격이 없는 상태.
After:
    - 세 문서의 추출 결과와 결합 스냅샷을 검증하는 Pydantic 스키마가 제공됨.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


CONTRACT_SCHEMA_VERSION = "contract_extraction.v0.1"
REGISTRY_SCHEMA_VERSION = "registry_extraction.v0.1"
EXPLANATION_SCHEMA_VERSION = "explanation_sheet_extraction.v0.1"
SNAPSHOT_SCHEMA_VERSION = "review_extraction_snapshot.v0.1"

RESIDENT_REGISTRATION_NUMBER = re.compile(
    r"(?<!\d)\d{6}(?:\s*-\s*|\s*)\d{7}(?!\d)"
)

NonNegativeInt = Annotated[int, Field(ge=0)]
NonNegativeFloat = Annotated[float, Field(ge=0)]
Confidence = Annotated[float, Field(ge=0, le=1)]


class ExtractionStatus(str, Enum):
    EXTRACTED = "extracted"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    LOW_CONFIDENCE = "low_confidence"
    USER_CONFIRMED = "user_confirmed"
    USER_CORRECTED = "user_corrected"
    NOT_APPLICABLE = "not_applicable"


class ContractType(str, Enum):
    JEONSE = "jeonse"
    MONTHLY_RENT = "monthly_rent"
    SALE = "sale"


class PartyRole(str, Enum):
    LESSOR = "lessor"
    LESSEE = "lessee"
    SELLER = "seller"
    BUYER = "buyer"


class PaymentTiming(str, Enum):
    PREPAID = "prepaid"
    POSTPAID = "postpaid"
    UNSPECIFIED = "unspecified"


class RegistrySection(str, Enum):
    TITLE = "title"
    GAPGU = "gapgu"
    EULGU = "eulgu"


class RegistryEntryStatus(str, Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class PrivacySafeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def reject_resident_registration_numbers(cls, value: Any) -> Any:
        offending_path = _find_resident_registration_number(value)
        if offending_path is not None:
            raise ValueError(
                "주민등록번호는 추출 결과에 저장할 수 없습니다: "
                f"{offending_path}"
            )
        return value


class NormalizedBoundingBox(PrivacySafeModel):
    x_min: Annotated[float, Field(ge=0, le=1)]
    y_min: Annotated[float, Field(ge=0, le=1)]
    x_max: Annotated[float, Field(ge=0, le=1)]
    y_max: Annotated[float, Field(ge=0, le=1)]

    @model_validator(mode="after")
    def validate_order(self) -> "NormalizedBoundingBox":
        if self.x_min > self.x_max or self.y_min > self.y_max:
            raise ValueError("bounding box의 최소 좌표가 최대 좌표보다 클 수 없습니다.")
        return self


class SourceLocation(PrivacySafeModel):
    page_number: Annotated[int, Field(ge=1)]
    bounding_box: NormalizedBoundingBox | None = None


ValueT = TypeVar("ValueT")


class ExtractedField(PrivacySafeModel, Generic[ValueT]):
    value: ValueT | None = None
    raw_text: str | None = None
    source: SourceLocation | None = None
    confidence: Confidence | None = None
    status: ExtractionStatus

    @model_validator(mode="after")
    def validate_status_value_pair(self) -> "ExtractedField[ValueT]":
        empty_statuses = {
            ExtractionStatus.MISSING,
            ExtractionStatus.NOT_APPLICABLE,
        }
        value_required_statuses = {
            ExtractionStatus.EXTRACTED,
            ExtractionStatus.USER_CONFIRMED,
            ExtractionStatus.USER_CORRECTED,
        }
        if self.status in empty_statuses and self.value is not None:
            raise ValueError(f"{self.status.value} 상태에는 value를 저장할 수 없습니다.")
        if self.status in value_required_statuses and self.value is None:
            raise ValueError(f"{self.status.value} 상태에는 value가 필요합니다.")
        if self.status in {
            ExtractionStatus.EXTRACTED,
            ExtractionStatus.LOW_CONFIDENCE,
        } and self.source is None:
            raise ValueError(f"{self.status.value} 상태에는 원문 위치가 필요합니다.")
        return self


class PropertyExtraction(PrivacySafeModel):
    address: ExtractedField[str]
    detail_address: ExtractedField[str]
    leased_part_description: ExtractedField[str] | None = None
    leased_area_m2: ExtractedField[NonNegativeFloat] | None = None
    land_category: ExtractedField[str] | None = None
    land_area_m2: ExtractedField[NonNegativeFloat] | None = None
    land_right_ratio: ExtractedField[str] | None = None
    building_structure: ExtractedField[str] | None = None
    building_use: ExtractedField[str] | None = None
    building_area_m2: ExtractedField[NonNegativeFloat] | None = None


class ContractPartyExtraction(PrivacySafeModel):
    role: PartyRole
    name: ExtractedField[str]
    ownership_share: ExtractedField[str] | None = None
    agent_present: ExtractedField[bool] | None = None
    agent_name: ExtractedField[str] | None = None
    signature_present: ExtractedField[bool] | None = None


class BrokerExtraction(PrivacySafeModel):
    office_name: ExtractedField[str] | None = None
    office_registration_number: ExtractedField[str] | None = None
    broker_in_charge: ExtractedField[str] | None = None
    signature_present: ExtractedField[bool] | None = None


class PaymentInstallmentExtraction(PrivacySafeModel):
    label: str
    amount: ExtractedField[NonNegativeInt]
    due_date: ExtractedField[date] | None = None


class ContractPaymentExtraction(PrivacySafeModel):
    sale_price: ExtractedField[NonNegativeInt] | None = None
    deposit: ExtractedField[NonNegativeInt] | None = None
    monthly_rent: ExtractedField[NonNegativeInt] | None = None
    maintenance_fee: ExtractedField[NonNegativeInt] | None = None
    down_payment: ExtractedField[NonNegativeInt] | None = None
    intermediate_payments: list[PaymentInstallmentExtraction] = Field(
        default_factory=list
    )
    remaining_balance: ExtractedField[NonNegativeInt] | None = None
    remaining_balance_date: ExtractedField[date] | None = None
    monthly_rent_payment_day: ExtractedField[str] | None = None
    payment_timing: ExtractedField[PaymentTiming] | None = None


class TextBlockExtraction(PrivacySafeModel):
    item_order: Annotated[int, Field(ge=1)]
    original_text: str = Field(min_length=1)
    source: SourceLocation
    confidence: Confidence | None = None


class ContractClauseExtraction(TextBlockExtraction):
    clause_number: str | None = None
    clause_title: str | None = None


class ContractDisclosures(PrivacySafeModel):
    tax_arrears: ExtractedField[str] | None = None
    senior_deposit_status: ExtractedField[str] | None = None
    registry_checked: ExtractedField[bool] | None = None
    existing_encumbrances: list[TextBlockExtraction] = Field(default_factory=list)
    rights_change_restriction: ExtractedField[str] | None = None
    guarantee_insurance_term: ExtractedField[str] | None = None


class ContractExtraction(PrivacySafeModel):
    schema_version: Literal[CONTRACT_SCHEMA_VERSION] = CONTRACT_SCHEMA_VERSION
    document_type: Literal["contract"] = "contract"
    contract_type: ExtractedField[ContractType]
    contract_date: ExtractedField[date]
    start_date: ExtractedField[date] | None = None
    end_date: ExtractedField[date] | None = None
    delivery_date: ExtractedField[date] | None = None
    ownership_transfer_date: ExtractedField[date] | None = None
    property: PropertyExtraction
    parties: list[ContractPartyExtraction]
    payment: ContractPaymentExtraction
    articles: list[ContractClauseExtraction] = Field(default_factory=list)
    special_terms: list[TextBlockExtraction] = Field(default_factory=list)
    disclosures: ContractDisclosures | None = None
    brokers: list[BrokerExtraction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_contract_specific_payment(self) -> "ContractExtraction":
        contract_type = self.contract_type.value
        if contract_type is None:
            return self
        if contract_type is ContractType.SALE and self.payment.sale_price is None:
            raise ValueError("매매 계약에는 sale_price 필드가 필요합니다.")
        if contract_type in {
            ContractType.JEONSE,
            ContractType.MONTHLY_RENT,
        } and self.payment.deposit is None:
            raise ValueError("임대차 계약에는 deposit 필드가 필요합니다.")
        if (
            contract_type is ContractType.MONTHLY_RENT
            and self.payment.monthly_rent is None
        ):
            raise ValueError("월세 계약에는 monthly_rent 필드가 필요합니다.")
        return self


class RegistryOwnerExtraction(PrivacySafeModel):
    name: ExtractedField[str]
    ownership_share: ExtractedField[str] | None = None
    registration_date: ExtractedField[date] | None = None
    registration_cause: ExtractedField[str] | None = None
    status: RegistryEntryStatus


class RegistryEncumbranceExtraction(PrivacySafeModel):
    section: RegistrySection
    rank_number: ExtractedField[str]
    registration_purpose: ExtractedField[str]
    registration_date: ExtractedField[date] | None = None
    holder: ExtractedField[str] | None = None
    debtor: ExtractedField[str] | None = None
    maximum_claim_amount: ExtractedField[NonNegativeInt] | None = None
    raw_text: str = Field(min_length=1)
    source: SourceLocation
    status: RegistryEntryStatus


class RegistryExtraction(PrivacySafeModel):
    schema_version: Literal[REGISTRY_SCHEMA_VERSION] = REGISTRY_SCHEMA_VERSION
    document_type: Literal["registry"] = "registry"
    issued_at: ExtractedField[datetime]
    unique_property_number: ExtractedField[str] | None = None
    address: ExtractedField[str]
    detail_address: ExtractedField[str]
    property_description: list[TextBlockExtraction] = Field(default_factory=list)
    owners: list[RegistryOwnerExtraction] = Field(default_factory=list)
    encumbrances: list[RegistryEncumbranceExtraction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExplanationSheetExtraction(PrivacySafeModel):
    schema_version: Literal[EXPLANATION_SCHEMA_VERSION] = EXPLANATION_SCHEMA_VERSION
    document_type: Literal["explanation_sheet"] = "explanation_sheet"
    contract_type: ExtractedField[ContractType]
    property: PropertyExtraction
    evidence_documents: list[TextBlockExtraction] = Field(default_factory=list)
    rights_and_relationships: list[TextBlockExtraction] = Field(default_factory=list)
    property_condition_notes: list[TextBlockExtraction] = Field(default_factory=list)
    management_fee: ExtractedField[NonNegativeInt] | None = None
    brokerage_fee: ExtractedField[NonNegativeInt] | None = None
    brokers: list[BrokerExtraction] = Field(default_factory=list)
    signatures_present: ExtractedField[bool] | None = None
    warnings: list[str] = Field(default_factory=list)


class ReviewExtractionSnapshot(PrivacySafeModel):
    schema_version: Literal[SNAPSHOT_SCHEMA_VERSION] = SNAPSHOT_SCHEMA_VERSION
    contract: ContractExtraction
    registry: RegistryExtraction | None = None
    explanation_sheet: ExplanationSheetExtraction | None = None
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_document_contract_types(self) -> "ReviewExtractionSnapshot":
        if self.explanation_sheet is None:
            return self
        contract_type = self.contract.contract_type.value
        explanation_type = self.explanation_sheet.contract_type.value
        if contract_type is not None and explanation_type is not None:
            if contract_type is not explanation_type:
                raise ValueError(
                    "계약서와 중개대상물 확인설명서의 계약 유형이 다릅니다."
                )
        return self


def extraction_json_schemas() -> dict[str, dict[str, Any]]:
    return {
        "contract_extraction_v01.json": ContractExtraction.model_json_schema(),
        "registry_extraction_v01.json": RegistryExtraction.model_json_schema(),
        "explanation_sheet_extraction_v01.json": (
            ExplanationSheetExtraction.model_json_schema()
        ),
        "review_extraction_snapshot_v01.json": (
            ReviewExtractionSnapshot.model_json_schema()
        ),
    }


def _find_resident_registration_number(
    value: Any,
    path: str = "$",
) -> str | None:
    if isinstance(value, str):
        return path if RESIDENT_REGISTRATION_NUMBER.search(value) else None
    if isinstance(value, dict):
        for key, nested_value in value.items():
            found = _find_resident_registration_number(
                nested_value,
                f"{path}.{key}",
            )
            if found is not None:
                return found
    if isinstance(value, (list, tuple)):
        for index, nested_value in enumerate(value):
            found = _find_resident_registration_number(
                nested_value,
                f"{path}[{index}]",
            )
            if found is not None:
                return found
    return None
