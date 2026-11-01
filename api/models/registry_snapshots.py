# registry_snapshots.py
"""
Description: 사용자 확인이 끝난 등기사항전부증명서의 공통 스냅샷을 정의한다.
계약서 교차검증에 필요한 소유권과 권리부담 정보를 원문 추적 정보와 함께 검증한다.
Author: ooheunsu
Date: 2026-10-25
Before:
    - 등본 OCR 결과를 계약서 검토 기능이 재사용할 확인 완료 데이터 계약이 없는 상태.
After:
    - 확인 완료 등본을 불변 스냅샷으로 저장하고 계약서 검토에서 참조할 수 있음.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import Field

from api.models.contract_snapshots import (
    FieldConfirmation,
    SnapshotConfirmation,
    SnapshotModel,
    SourceLocation,
)


class RegistrySection(str, Enum):
    TITLE = "title"
    GAPGU = "gapgu"
    EULGU = "eulgu"


class RegistryEntryStatus(str, Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class RegistryPropertyDescription(SnapshotModel):
    order: int = Field(ge=1)
    original_text: str = Field(min_length=1, max_length=10000)
    source: SourceLocation


class RegistryOwner(SnapshotModel):
    name: str = Field(min_length=1, max_length=100)
    ownership_share: str | None = Field(default=None, min_length=1, max_length=100)
    registration_date: date | None = None
    registration_cause: str | None = Field(default=None, min_length=1, max_length=300)
    status: RegistryEntryStatus


class RegistryEncumbrance(SnapshotModel):
    section: RegistrySection
    rank_number: str = Field(min_length=1, max_length=50)
    registration_purpose: str = Field(min_length=1, max_length=300)
    registration_date: date | None = None
    holder: str | None = Field(default=None, min_length=1, max_length=300)
    debtor: str | None = Field(default=None, min_length=1, max_length=300)
    maximum_claim_amount: int | None = Field(default=None, ge=0)
    raw_text: str = Field(min_length=1, max_length=10000)
    source: SourceLocation
    status: RegistryEntryStatus


class ConfirmedRegistrySnapshot(SnapshotModel):
    schema_version: Literal["confirmed_registry_snapshot.v0.1"] = (
        "confirmed_registry_snapshot.v0.1"
    )
    snapshot_id: UUID
    document_id: UUID
    confirmation: SnapshotConfirmation
    issued_at: datetime
    unique_property_number_masked: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    address: str = Field(min_length=2, max_length=500)
    detail_address: str | None = Field(default=None, min_length=1, max_length=300)
    property_description: list[RegistryPropertyDescription] = Field(
        default_factory=list,
        max_length=100,
    )
    owners: list[RegistryOwner] = Field(default_factory=list, max_length=100)
    encumbrances: list[RegistryEncumbrance] = Field(default_factory=list, max_length=300)
    field_confirmations: list[FieldConfirmation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list, max_length=100)
