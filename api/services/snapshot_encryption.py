# snapshot_encryption.py
"""
Description: MySQL에 저장하는 확인 완료 스냅샷 JSON을 AES-256-GCM으로 암호화한다.
환경변수 키가 없거나 손상되면 복호화를 시도하지 않고 저장소 시작을 중단한다.
Author: ooheunsu
Date: 2026-11-02
Before:
    - 확인 완료 스냅샷 JSON이 MySQL에 평문으로 저장되는 상태.
After:
    - 앱 전용 32바이트 키로 암호화한 스냅샷만 MySQL에 저장하고 복호화할 수 있음.
"""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


ENCRYPTION_VERSION = "aes-256-gcm.v1"
_NONCE_BYTES = 12


@dataclass(frozen=True)
class SnapshotEncryptor:
    """버전이 포함된 AES-256-GCM 암호문을 생성하고 복호화한다."""

    key: bytes

    @classmethod
    def from_base64(cls, encoded_key: str) -> SnapshotEncryptor:
        """URL-safe base64 형식의 32바이트 AES 키를 읽는다."""

        try:
            key = base64.urlsafe_b64decode(encoded_key.encode("ascii"))
        except (UnicodeEncodeError, binascii.Error, ValueError) as error:
            raise ValueError(
                "SNAPSHOT_ENCRYPTION_KEY는 URL-safe base64 형식이어야 합니다."
            ) from error
        if len(key) != 32:
            raise ValueError(
                "SNAPSHOT_ENCRYPTION_KEY는 base64로 인코딩한 32바이트 키여야 합니다."
            )
        return cls(key=key)

    def encrypt(self, plaintext: str) -> str:
        """nonce와 암호문을 하나의 URL-safe base64 값으로 반환한다."""

        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(self.key).encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, ciphertext: str, *, version: str | None) -> str:
        """알 수 없는 포맷이나 위변조된 암호문은 복호화하지 않는다."""

        if version != ENCRYPTION_VERSION:
            raise ValueError("지원하지 않는 스냅샷 암호화 버전입니다.")
        try:
            payload = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
            plaintext = AESGCM(self.key).decrypt(
                payload[:_NONCE_BYTES], payload[_NONCE_BYTES:], None
            )
            return plaintext.decode("utf-8")
        except (UnicodeEncodeError, binascii.Error, ValueError) as error:
            raise ValueError("스냅샷 암호문 형식이 올바르지 않습니다.") from error
        except Exception as error:
            raise ValueError("스냅샷 복호화에 실패했습니다.") from error
