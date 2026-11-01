# contract_snapshots.py
"""
Description: 사용자 확인이 끝난 전세·월세·매매 계약 정보의 공통 스냅샷을 정의한다.
OCR 감사 정보와 기능별로 재사용할 정규화 계약 값을 분리해 검증한다.
Author: ooheunsu
Date: 2026-10-25
Before:
    - OCR 결과와 계약서 검토·내용증명 입력 사이의 공통 데이터 계약이 없는 상태.
After:
    - 세 계약 유형의 확인 완료 스냅샷과 기능별 입력 참조를 타입으로 검증할 수 있음.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SnapshotModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


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
    UNKNOWN = "unknown"


class ConfirmationResolution(str, Enum):
    USER_CONFIRMED = "user_confirmed"
    USER_CORRECTED = "user_corrected"
    CONFIRMED_BLANK = "confirmed_blank"


class SourceLocation(SnapshotModel):
    page_number: int = Field(ge=1)
    bounding_box: tuple[float, float, float, float] | None = None

    @model_validator(mode="after")
    def validate_bounding_box(self) -> SourceLocation:
        if self.bounding_box is not None and any(
            coordinate < 0 or coordinate > 1 for coordinate in self.bounding_box
        ):
            raise ValueError("bounding_box 좌표는 0과 1 사이여야 합니다.")
        return self


class FieldConfirmation(SnapshotModel):
    field_path: str = Field(min_length=1, max_length=300)
    raw_text: str | None = Field(default=None, max_length=5000)
    source: SourceLocation | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    resolution: ConfirmationResolution


class MoneyValue(SnapshotModel):
    amount: int = Field(ge=0, description="원 단위 정규화 금액")
    korean_text: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="계약서에 적힌 한글 금액 원문",
    )


class PaymentInstallment(SnapshotModel):
    label: str = Field(min_length=1, max_length=100)
    amount: MoneyValue
    due_date: date | None = None


class MonthlyRentPayment(SnapshotModel):
    amount: MoneyValue
    payment_day: int | None = Field(default=None, ge=1, le=31)
    timing: PaymentTiming = PaymentTiming.UNKNOWN
    account_masked: str | None = Field(default=None, min_length=1, max_length=100)


class ManagementFeeItem(SnapshotModel):
    name: str = Field(min_length=1, max_length=100)
    amount: MoneyValue | None = None


class ManagementFee(SnapshotModel):
    total: MoneyValue | None = None
    items: list[ManagementFeeItem] = Field(default_factory=list, max_length=20)
    calculation_method: str | None = Field(default=None, min_length=1, max_length=500)


class PropertyData(SnapshotModel):
    address: str = Field(min_length=2, max_length=500)
    detail_address: str | None = Field(default=None, min_length=1, max_length=300)
    property_type: str | None = Field(default=None, min_length=1, max_length=100)
    parcel_number: str | None = Field(default=None, min_length=1, max_length=100)
    building_name_unit: str | None = Field(default=None, min_length=1, max_length=300)
    land_category: str | None = Field(default=None, min_length=1, max_length=100)
    land_area_m2: float | None = Field(default=None, ge=0)
    building_structure: str | None = Field(default=None, min_length=1, max_length=200)
    building_use: str | None = Field(default=None, min_length=1, max_length=200)
    building_area_m2: float | None = Field(default=None, ge=0)
    leased_portion: str | None = Field(default=None, min_length=1, max_length=500)
    leased_area_m2: float | None = Field(default=None, ge=0)
    land_right_type: str | None = Field(default=None, min_length=1, max_length=100)
    land_right_ratio: str | None = Field(default=None, min_length=1, max_length=100)


class ContractParty(SnapshotModel):
    role: PartyRole
    name: str = Field(min_length=1, max_length=100)
    address: str | None = Field(default=None, min_length=1, max_length=500)
    detail_address: str | None = Field(default=None, min_length=1, max_length=300)
    agent_name: str | None = Field(default=None, min_length=1, max_length=100)


class BrokerData(SnapshotModel):
    office_name: str = Field(min_length=1, max_length=300)
    office_registration_number: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    office_address: str | None = Field(default=None, min_length=1, max_length=500)
    representative_name: str | None = Field(default=None, min_length=1, max_length=100)
    broker_in_charge: str | None = Field(default=None, min_length=1, max_length=100)
    signature_present: bool | None = None


class ContractClause(SnapshotModel):
    clause_id: str = Field(min_length=1, max_length=100)
    order: int = Field(ge=1)
    clause_number: str | None = Field(default=None, min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    original_text: str = Field(min_length=1, max_length=20000)
    source: SourceLocation


class SpecialTerm(SnapshotModel):
    term_id: str = Field(min_length=1, max_length=100)
    order: int = Field(ge=1)
    original_text: str = Field(min_length=1, max_length=10000)
    source: SourceLocation


class ContractDisclosures(SnapshotModel):
    tax_arrears: str | None = Field(default=None, min_length=1, max_length=500)
    senior_deposit_status: str | None = Field(default=None, min_length=1, max_length=500)
    registry_checked: bool | None = None
    rights_change_restriction: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
    )
    guarantee_insurance_term: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
    )


class SignatureStatus(SnapshotModel):
    role: PartyRole
    present: bool | None = None


class CommonContractData(SnapshotModel):
    document_title: str | None = Field(default=None, min_length=1, max_length=300)
    page_count: int | None = Field(default=None, ge=1, le=100)
    contract_kind: str | None = Field(default=None, min_length=1, max_length=100)
    contract_date: date | None = None
    property: PropertyData
    parties: list[ContractParty] = Field(min_length=2, max_length=20)
    brokers: list[BrokerData] = Field(default_factory=list, max_length=10)
    clauses: list[ContractClause] = Field(default_factory=list, max_length=100)
    special_terms: list[SpecialTerm] = Field(default_factory=list, max_length=50)
    disclosures: ContractDisclosures = Field(default_factory=ContractDisclosures)
    signatures: list[SignatureStatus] = Field(default_factory=list, max_length=20)
    attachment_names: list[str] = Field(default_factory=list, max_length=30)

    def require_party_roles(self, required_roles: set[PartyRole]) -> None:
        actual_roles = {party.role for party in self.parties}
        missing_roles = required_roles - actual_roles
        if missing_roles:
            missing = ", ".join(sorted(role.value for role in missing_roles))
            raise ValueError(f"필수 계약 당사자 역할이 없습니다: {missing}")


class JeonseContractData(CommonContractData):
    contract_type: Literal[ContractType.JEONSE] = ContractType.JEONSE
    lease_deposit: MoneyValue
    down_payment: MoneyValue | None = None
    intermediate_payments: list[PaymentInstallment] = Field(
        default_factory=list,
        max_length=20,
    )
    remaining_balance: MoneyValue | None = None
    remaining_balance_date: date | None = None
    start_date: date
    end_date: date
    handover_date: date | None = None
    management_fee: ManagementFee | None = None

    @model_validator(mode="after")
    def validate_lease_contract(self) -> JeonseContractData:
        self.require_party_roles({PartyRole.LESSOR, PartyRole.LESSEE})
        if self.start_date >= self.end_date:
            raise ValueError("임대차 종료일은 시작일보다 늦어야 합니다.")
        return self


class MonthlyRentContractData(JeonseContractData):
    contract_type: Literal[ContractType.MONTHLY_RENT] = ContractType.MONTHLY_RENT
    monthly_rent: MonthlyRentPayment


class ExistingOccupancy(SnapshotModel):
    description: str = Field(min_length=1, max_length=3000)
    lease_deposit: MoneyValue | None = None
    monthly_rent: MoneyValue | None = None
    succession_condition: str | None = Field(default=None, min_length=1, max_length=2000)


class SaleContractData(CommonContractData):
    contract_type: Literal[ContractType.SALE] = ContractType.SALE
    sale_price: MoneyValue
    down_payment: MoneyValue | None = None
    intermediate_payments: list[PaymentInstallment] = Field(
        default_factory=list,
        max_length=20,
    )
    remaining_balance: MoneyValue | None = None
    remaining_balance_date: date | None = None
    handover_date: date | None = None
    ownership_transfer_date: date | None = None
    encumbrance_handling: str | None = Field(default=None, min_length=1, max_length=3000)
    loan_assumption: str | None = Field(default=None, min_length=1, max_length=3000)
    existing_occupancies: list[ExistingOccupancy] = Field(
        default_factory=list,
        max_length=20,
    )

    @model_validator(mode="after")
    def validate_sale_contract(self) -> SaleContractData:
        self.require_party_roles({PartyRole.SELLER, PartyRole.BUYER})
        return self


ConfirmedContractData = Annotated[
    JeonseContractData | MonthlyRentContractData | SaleContractData,
    Field(discriminator="contract_type"),
]


class SnapshotConfirmation(SnapshotModel):
    confirmed_by_user: Literal[True]
    revision: int = Field(ge=1)
    confirmed_at: datetime


class ConfirmedContractSnapshot(SnapshotModel):
    schema_version: Literal["confirmed_contract_snapshot.v0.1"] = (
        "confirmed_contract_snapshot.v0.1"
    )
    snapshot_id: UUID
    document_id: UUID
    confirmation: SnapshotConfirmation
    contract: ConfirmedContractData
    field_confirmations: list[FieldConfirmation] = Field(
        default_factory=list,
        description="프론트 원문 대조와 감사용이며 LLM 입력에서는 제외",
    )
    warnings: list[str] = Field(default_factory=list, max_length=100)


class ContractReviewWorkflowInput(SnapshotModel):
    contract_snapshot_id: UUID
    registry_snapshot_id: UUID | None = None


class DocumentParty(SnapshotModel):
    name: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=2, max_length=500)
    detail_address: str | None = Field(default=None, min_length=1, max_length=300)


class CertifiedLetterWorkflowInput(SnapshotModel):
    contract_snapshot_id: UUID
    purpose: str = Field(
        min_length=2,
        max_length=300,
        description="내용증명으로 달성하려는 요구·통지 목적",
    )
    facts: str = Field(
        min_length=10,
        max_length=10000,
        description="사용자가 작성한 시간순 사실관계",
    )
    sender: DocumentParty
    receiver: DocumentParty
    requested_deadline: date | None = None
    attachment_names: list[str] = Field(default_factory=list, max_length=30)
