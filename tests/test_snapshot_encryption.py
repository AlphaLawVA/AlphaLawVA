# test_snapshot_encryption.py
"""
Description: 확정 스냅샷 AES-256-GCM 암호화 키 검증과 복호화 실패 처리를 검증한다.
Author: ooheunsu
Date: 2026-11-02
Before:
    - 스냅샷 암호화 유틸리티의 키와 위변조 처리 검증이 없는 상태.
After:
    - 키 길이, 왕복 암호화, 위변조된 암호문 차단을 회귀 검증할 수 있음.
"""

import base64
import unittest

from api.services.snapshot_encryption import (
    ENCRYPTION_VERSION,
    SnapshotEncryptor,
)


class SnapshotEncryptionTests(unittest.TestCase):
    def setUp(self):
        self.key = base64.urlsafe_b64encode(b"a" * 32).decode("ascii")

    def test_encrypts_and_decrypts_snapshot_payload(self):
        encryptor = SnapshotEncryptor.from_base64(self.key)

        encrypted = encryptor.encrypt('{"address":"서울특별시 테스트구"}')

        self.assertNotIn("서울특별시", encrypted)
        self.assertEqual(
            encryptor.decrypt(encrypted, version=ENCRYPTION_VERSION),
            '{"address":"서울특별시 테스트구"}',
        )

    def test_rejects_non_256_bit_key(self):
        short_key = base64.urlsafe_b64encode(b"short").decode("ascii")

        with self.assertRaisesRegex(ValueError, "32바이트"):
            SnapshotEncryptor.from_base64(short_key)

    def test_rejects_tampered_ciphertext(self):
        encryptor = SnapshotEncryptor.from_base64(self.key)
        encrypted = encryptor.encrypt("sensitive")

        with self.assertRaises(ValueError):
            encryptor.decrypt(encrypted[:-1] + "A", version=ENCRYPTION_VERSION)


if __name__ == "__main__":
    unittest.main()
