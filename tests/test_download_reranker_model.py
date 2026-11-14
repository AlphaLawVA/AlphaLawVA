# test_download_reranker_model.py
"""
Description: 리랭커 내려받기 스크립트가 고정된 버전으로 내려받고 필수 파일·해시를 검증하는지 확인한다(네트워크 호출 없음).
Author: ooheunsu
Date: 2026-11-12
Before:
    - 리랭커 모델을 준비하는 재현 가능한 절차가 없었다.
After:
    - 누락·변조된 모델 파일은 검증에서 실패한다.
"""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml.rag import download_reranker_model as module

PAYLOAD = b"fake-weights"


def _fake_downloader(**kwargs):
    target = Path(kwargs["local_dir"])
    for name in ("config.json", "tokenizer.json"):
        (target / name).write_text("{}", encoding="utf-8")
    (target / module.MODEL_FILE).write_bytes(PAYLOAD)
    _fake_downloader.calls.append(kwargs)


_fake_downloader.calls = []


class DownloadRerankerModelTest(unittest.TestCase):
    def setUp(self):
        _fake_downloader.calls.clear()

    def test_downloads_pinned_revision_and_verifies_hash(self):
        digest = hashlib.sha256(PAYLOAD).hexdigest()
        with tempfile.TemporaryDirectory() as directory, patch.object(module, "MODEL_SHA256", digest):
            target = Path(directory) / "model"
            self.assertEqual(module.download(target, downloader=_fake_downloader), target)
        call = _fake_downloader.calls[0]
        self.assertEqual(call["repo_id"], "nlpai-lab/KURE-Reranker-nano")
        self.assertEqual(call["revision"], module.REVISION)
        self.assertEqual(len(module.REVISION), 40)

    def test_hash_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "해시"):
                module.download(Path(directory) / "model", downloader=_fake_downloader)

    def test_missing_files_fail_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "필수 파일"):
                module.verify(Path(directory))

    def test_default_target_matches_service_default_directory(self):
        from ml.rag import contract_review_retriever as retriever

        self.assertEqual(module.DIRECTORY_NAME, retriever.DEFAULT_RERANKER_NAME)
        self.assertEqual(module.default_target().name, retriever.DEFAULT_RERANKER_NAME)


if __name__ == "__main__":
    unittest.main()
