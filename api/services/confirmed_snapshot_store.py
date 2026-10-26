# confirmed_snapshot_store.py
"""
Description: 사용자 확인이 끝난 계약서와 등본 스냅샷을 임시로 보관·조회한다.
영구 DB 도입 전 API 흐름 검증을 위해 프로세스 메모리 저장소를 제공한다.
Author: ooheunsu
Date: 2026-10-26
Before:
    - 확인 완료 스냅샷을 계약서 검토 API가 참조할 저장소가 없는 상태.
After:
    - 계약서·등본 스냅샷을 ID로 저장하고 같은 프로세스 안에서 조회할 수 있음.
"""

from __future__ import annotations

from threading import RLock
from uuid import UUID

from api.models.contract_snapshots import ConfirmedContractSnapshot
from api.models.registry_snapshots import ConfirmedRegistrySnapshot


class SnapshotNotFoundError(Exception):
    """요청한 사용자 확인 스냅샷이 현재 저장소에 없을 때 발생한다."""


class ConfirmedSnapshotStore:
    """영구 DB 연결 전 확인 완료 스냅샷을 프로세스 메모리에 보관한다."""

    def __init__(self) -> None:
        self._contract_snapshots: dict[UUID, ConfirmedContractSnapshot] = {}
        self._registry_snapshots: dict[UUID, ConfirmedRegistrySnapshot] = {}
        self._lock = RLock()

    def save_contract(
        self,
        snapshot: ConfirmedContractSnapshot,
    ) -> ConfirmedContractSnapshot:
        with self._lock:
            self._contract_snapshots[snapshot.snapshot_id] = snapshot
        return snapshot

    def save_registry(
        self,
        snapshot: ConfirmedRegistrySnapshot,
    ) -> ConfirmedRegistrySnapshot:
        with self._lock:
            self._registry_snapshots[snapshot.snapshot_id] = snapshot
        return snapshot

    def get_contract(self, snapshot_id: UUID) -> ConfirmedContractSnapshot:
        with self._lock:
            snapshot = self._contract_snapshots.get(snapshot_id)
        if snapshot is None:
            raise SnapshotNotFoundError(f"계약서 스냅샷이 없습니다: {snapshot_id}")
        return snapshot

    def get_registry(self, snapshot_id: UUID) -> ConfirmedRegistrySnapshot:
        with self._lock:
            snapshot = self._registry_snapshots.get(snapshot_id)
        if snapshot is None:
            raise SnapshotNotFoundError(f"등본 스냅샷이 없습니다: {snapshot_id}")
        return snapshot
