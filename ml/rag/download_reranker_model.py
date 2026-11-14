# download_reranker_model.py
"""
Description: 법령 리랭커(KURE-Reranker-nano)를 고정된 버전으로 내려받아 서비스가 찾는 경로에 두고 파일 해시를 검증한다.
Docker 이미지를 만들 때 `RUN python -m ml.rag.download_reranker_model`로 호출해 모델을 이미지에 포함하는 용도다.
Author: ooheunsu
Date: 2026-11-12
Before:
    - 리랭커 모델 파일이 저장소에 없고 서버마다 사람이 직접 복사해야 하는 상태.
After:
    - 한 줄 명령으로 같은 버전의 모델이 local_data/models/rerankers/kure-reranker-nano에 놓임.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from typing import Any, Callable

REPO_ID = "nlpai-lab/KURE-Reranker-nano"
REVISION = "8514b9e113f3b27c441ed9c2b50f81d80388b2fd"  # 2026-11-12 시험에 쓴 버전(Apache-2.0)
MODEL_FILE = "model.safetensors"
MODEL_SHA256 = "41e752090da96fcab4813a9ba82259c31c6d28453c18189322ae63d5599a30c0"
REQUIRED_FILES = ("config.json", MODEL_FILE, "tokenizer.json")
DIRECTORY_NAME = "kure-reranker-nano"  # contract_review_retriever의 기본 경로 이름과 같아야 한다.


def default_target() -> Path:
    local_data = Path(
        os.environ.get("ALPHALAWVA_LOCAL_DATA_DIR", Path(__file__).resolve().parents[2] / "local_data")
    )
    return local_data / "models" / "rerankers" / DIRECTORY_NAME


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(target: Path) -> None:
    """필수 파일이 있고 모델 가중치 해시가 고정된 버전과 같은지 확인한다."""

    missing = [name for name in REQUIRED_FILES if not (target / name).is_file()]
    if missing:
        raise RuntimeError(f"리랭커 필수 파일이 없습니다: {', '.join(missing)}")
    actual = file_sha256(target / MODEL_FILE)
    if actual != MODEL_SHA256:
        raise RuntimeError(f"리랭커 모델 해시가 고정된 버전과 다릅니다: {actual}")


def download(target: Path, *, downloader: Callable[..., Any] | None = None) -> Path:
    """고정된 버전을 target에 내려받고 검증한다. 심볼릭 링크를 쓰지 않아 Windows에서도 동작한다."""

    if downloader is None:
        from huggingface_hub import snapshot_download

        downloader = snapshot_download
    target.mkdir(parents=True, exist_ok=True)
    downloader(repo_id=REPO_ID, revision=REVISION, local_dir=str(target))
    verify(target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=None, help="저장 폴더(기본: local_data/models/rerankers/kure-reranker-nano)")
    parser.add_argument("--verify-only", action="store_true", help="내려받지 않고 이미 있는 파일만 검증한다")
    args = parser.parse_args()
    target = args.target or default_target()
    if args.verify_only:
        verify(target)
    else:
        download(target)
    print(f"리랭커 모델 준비 완료: {target}")


if __name__ == "__main__":
    main()
