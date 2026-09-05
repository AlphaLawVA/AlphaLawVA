# statute_chroma_builder.py
"""
Description: 법령 조문 청크를 SentenceTransformer 모델로 임베딩해 모델별
ChromaDB를 구축하고 원본·모델·저장 건수의 일치 여부를 검증한다.
Author: ooheunsu
Date: 2026-08-30
Before:
    - data/statutes/chunks/에 검증된 조문 청크가 생성되고 임베딩 모델 사양이 정의된 상태.

After:
    - 모델별 ChromaDB와 재현성·진행 상태를 기록한 구축 manifest가 생성.
"""

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ml.data_collection.statutes.law_api_common import (
    PROJECT_ROOT,
    read_json,
    write_json,
)
from ml.embedding.analyze_statute_chunk_tokens import load_chunks


EXPECTED_SCHEMA_VERSION = "0.2"
EXPECTED_CHUNKING_STRATEGY = "article_with_overflow_split_v01"
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data/statutes/chunks"
DEFAULT_MODEL_CACHE = PROJECT_ROOT / "data/statutes/models"
DEFAULT_MANIFEST_NAME = "build_manifest.json"
MANIFEST_VERSION = "0.1"


@dataclass(frozen=True)
class EmbeddingModelSpec:
    display_name: str
    model_name: str
    revision: str
    dimension: int
    max_tokens: int
    collection_name: str
    db_dir_name: str
    chunking_strategy: str = EXPECTED_CHUNKING_STRATEGY
    input_dir_name: str = "chunks"

    @property
    def default_db_dir(self) -> Path:
        return PROJECT_ROOT / "data/statutes/vectorstores" / self.db_dir_name

    @property
    def default_input_dir(self) -> Path:
        return PROJECT_ROOT / "data/statutes" / self.input_dir_name


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def project_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def validate_chunk_headers(
    input_dir: Path,
    expected_strategy: str = EXPECTED_CHUNKING_STRATEGY,
) -> None:
    input_files = sorted(input_dir.glob("*.json"))
    if not input_files:
        raise FileNotFoundError(f"법령 청크 JSON이 없습니다: {input_dir}")

    for input_file in input_files:
        document = read_json(input_file)
        schema_version = document.get("schema_version")
        strategy = document.get("chunking_strategy")
        if schema_version != EXPECTED_SCHEMA_VERSION:
            raise ValueError(
                f"지원하지 않는 청크 스키마: {input_file} "
                f"({schema_version})"
            )
        if strategy != expected_strategy:
            raise ValueError(
                f"지원하지 않는 청킹 방식: {input_file} ({strategy})"
            )


def corpus_sha256(chunks: list[dict]) -> str:
    digest = hashlib.sha256()
    for chunk in sorted(chunks, key=lambda item: item["chunk_id"]):
        record = {
            "chunk_id": chunk["chunk_id"],
            "source_node_id": chunk.get("source_node_id"),
            "chunk_type": chunk.get("chunk_type"),
            "retrieval_text": chunk["retrieval_text"],
            "metadata": chunk.get("metadata", {}),
        }
        digest.update(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def chroma_metadata(chunk: dict) -> dict[str, str | int | bool | float]:
    source = chunk.get("metadata", {})
    result: dict[str, str | int | bool | float] = {
        "source_node_id": chunk["source_node_id"],
        "chunk_type": chunk["chunk_type"],
        "law_id": source["law_id"],
        "law_name": source["law_name"],
        "article_label": source["article_label"],
        "effective_date": source["effective_date"],
        "contains_excluded_image": source["contains_excluded_image"],
        "heading_path": " > ".join(source.get("heading_path", [])),
    }
    for key in ("article_title", "part_index", "part_count"):
        value = source.get(key)
        if isinstance(value, (str, int, bool, float)):
            result[key] = value
    for key in (
        "parent_article_id",
        "paragraph_order",
        "paragraph_label",
    ):
        value = source.get(key)
        if isinstance(value, (str, int, bool, float)):
            result[key] = value
    paragraph_orders = source.get("paragraph_orders")
    if isinstance(paragraph_orders, list):
        result["paragraph_orders"] = ",".join(
            str(value) for value in paragraph_orders
        )
    return result


def expected_collection_metadata(
    source_digest: str,
    chunk_count: int,
    revision: str,
    model_spec: EmbeddingModelSpec,
) -> dict[str, str | int | bool]:
    return {
        "hnsw:space": "cosine",
        "model_name": model_spec.model_name,
        "model_revision": revision,
        "embedding_dimension": model_spec.dimension,
        "normalize_embeddings": True,
        "chunk_schema_version": EXPECTED_SCHEMA_VERSION,
        "chunking_strategy": model_spec.chunking_strategy,
        "source_sha256": source_digest,
        "source_chunk_count": chunk_count,
    }


def validate_collection_metadata(
    actual: dict[str, Any] | None,
    expected: dict[str, Any],
) -> None:
    actual = actual or {}
    mismatches = {
        key: {"expected": value, "actual": actual.get(key)}
        for key, value in expected.items()
        if actual.get(key) != value
    }
    if mismatches:
        raise ValueError(
            "기존 컬렉션이 현재 모델 또는 청크와 다릅니다. "
            "새 DB 경로를 사용하거나 --rebuild를 명시하세요: "
            f"{mismatches}"
        )


def is_out_of_memory(error: RuntimeError) -> bool:
    message = str(error).lower()
    return "out of memory" in message or "cuda error: memory" in message


def clear_cuda_cache() -> None:
    try:
        import torch
    except ImportError:
        return
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def encode_with_oom_split(model: Any, texts: list[str]) -> list[Any]:
    try:
        encoded = model.encode(
            texts,
            batch_size=len(texts),
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return list(encoded)
    except RuntimeError as error:
        if not is_out_of_memory(error) or len(texts) == 1:
            raise
        clear_cuda_cache()
        midpoint = len(texts) // 2
        return encode_with_oom_split(
            model, texts[:midpoint]
        ) + encode_with_oom_split(model, texts[midpoint:])


def embedding_rows(
    embeddings: list[Any],
    model_spec: EmbeddingModelSpec,
) -> list[list[float]]:
    rows = []
    for embedding in embeddings:
        row = embedding.tolist() if hasattr(embedding, "tolist") else embedding
        if not isinstance(row, list) or len(row) != model_spec.dimension:
            length = len(row) if isinstance(row, list) else None
            raise ValueError(
                f"{model_spec.display_name} 임베딩 차원이 "
                f"{model_spec.dimension}이 아닙니다: {length}"
            )
        rows.append(row)
    return rows


def batched(values: list[dict], size: int) -> list[list[dict]]:
    if size < 1:
        raise ValueError("batch_size는 1 이상이어야 합니다.")
    return [values[index : index + size] for index in range(0, len(values), size)]


def existing_collection_ids(collection: Any) -> set[str]:
    if collection.count() == 0:
        return set()
    result = collection.get(include=[])
    return set(result.get("ids", []))


def package_versions() -> dict[str, str | None]:
    versions = {}
    for package in ("chromadb", "sentence-transformers", "transformers", "torch"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def previous_maximum_token_count(manifest_path: Path) -> int | None:
    if not manifest_path.exists():
        return None
    value = read_json(manifest_path).get("maximum_token_count")
    return value if isinstance(value, int) else None


def disable_unavailable_hf_transfer() -> bool:
    enabled = os.environ.get("HF_HUB_ENABLE_HF_TRANSFER", "").upper()
    if enabled not in {"1", "ON", "YES", "TRUE"}:
        return False
    if importlib.util.find_spec("hf_transfer") is not None:
        return False
    os.environ.pop("HF_HUB_ENABLE_HF_TRANSFER", None)
    return True


def load_sentence_transformer_model(
    model_spec: EmbeddingModelSpec,
    revision: str,
    cache_dir: Path,
    device: str,
) -> Any:
    disable_unavailable_hf_transfer()
    try:
        import torch
        from sentence_transformers import SentenceTransformer
    except ImportError as error:
        raise RuntimeError(
            "임베딩 실행 패키지가 없습니다. RunPod에서 "
            "pip install -r requirements-embedding.txt 를 실행하세요."
        ) from error

    selected_device = device
    if device == "auto":
        selected_device = "cuda" if torch.cuda.is_available() else "cpu"
    if selected_device != "cuda":
        raise RuntimeError(
            f"전체 임베딩은 CUDA GPU에서 실행하세요: {selected_device}"
        )

    model = SentenceTransformer(
        model_spec.model_name,
        revision=revision,
        cache_folder=str(cache_dir),
        device=selected_device,
    )
    if model.get_sentence_embedding_dimension() != model_spec.dimension:
        raise ValueError(
            f"{model_spec.display_name} 임베딩 차원이 예상값과 다릅니다."
        )
    if model.max_seq_length < model_spec.max_tokens:
        raise ValueError(
            f"{model_spec.display_name} 입력 한도가 예상보다 짧습니다: "
            f"{model.max_seq_length}"
        )
    model.half()
    return model


def maximum_token_count(model: Any, chunks: list[dict]) -> int:
    maximum = 0
    for chunk in chunks:
        token_ids = model.tokenizer.encode(
            chunk["retrieval_text"],
            add_special_tokens=True,
            truncation=False,
        )
        maximum = max(maximum, len(token_ids))
    if maximum > model.max_seq_length:
        raise ValueError(
            f"모델 입력 한도를 넘는 청크가 있습니다: {maximum} > "
            f"{model.max_seq_length}"
        )
    return maximum


def get_or_create_collection(
    db_dir: Path,
    collection_name: str,
    metadata: dict[str, Any],
    rebuild: bool,
) -> tuple[Any, Any]:
    try:
        import chromadb
    except ImportError as error:
        raise RuntimeError(
            "ChromaDB가 없습니다. RunPod에서 "
            "pip install -r requirements-embedding.txt 를 실행하세요."
        ) from error

    db_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(db_dir))
    if rebuild:
        try:
            client.delete_collection(collection_name)
        except Exception as error:
            if "does not exist" not in str(error).lower():
                raise
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata=metadata,
    )
    validate_collection_metadata(collection.metadata, metadata)
    return client, collection


def build_manifest(
    *,
    args: argparse.Namespace,
    model_spec: EmbeddingModelSpec,
    source_digest: str,
    source_chunk_count: int,
    stored_count: int,
    pending_count: int,
    maximum_tokens: int | None,
    status: str,
    started_at: str,
    elapsed_seconds: float,
    error: str | None = None,
) -> dict:
    return {
        "manifest_version": MANIFEST_VERSION,
        "status": status,
        "started_at": started_at,
        "updated_at": utc_timestamp(),
        "elapsed_seconds": round(elapsed_seconds, 2),
        "input_dir": project_path(args.input_dir),
        "db_dir": project_path(args.db_dir),
        "collection_name": args.collection_name,
        "model_name": model_spec.model_name,
        "model_revision": args.revision,
        "embedding_dimension": model_spec.dimension,
        "normalize_embeddings": True,
        "chunk_schema_version": EXPECTED_SCHEMA_VERSION,
        "chunking_strategy": model_spec.chunking_strategy,
        "source_sha256": source_digest,
        "source_chunk_count": source_chunk_count,
        "stored_count": stored_count,
        "pending_count": pending_count,
        "maximum_token_count": maximum_tokens,
        "batch_size": args.batch_size,
        "device": args.device,
        "package_versions": package_versions(),
        "error": error,
    }


def parse_args(
    model_spec: EmbeddingModelSpec,
    argv: list[str] | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            f"{model_spec.display_name} 법령 임베딩 ChromaDB를 구축합니다."
        )
    )
    parser.add_argument(
        "--input-dir", type=Path, default=model_spec.default_input_dir
    )
    parser.add_argument("--db-dir", type=Path, default=model_spec.default_db_dir)
    parser.add_argument("--model-cache", type=Path, default=DEFAULT_MODEL_CACHE)
    parser.add_argument("--collection-name", default=model_spec.collection_name)
    parser.add_argument("--revision", default=model_spec.revision)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", choices=("auto", "cuda"), default="auto")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args(argv)


def run_build(
    model_spec: EmbeddingModelSpec,
    args: argparse.Namespace | None = None,
) -> None:
    args = args or parse_args(model_spec)
    args.input_dir = args.input_dir.resolve()
    args.db_dir = args.db_dir.resolve()
    args.model_cache = args.model_cache.resolve()
    if args.batch_size < 1:
        raise ValueError("batch_size는 1 이상이어야 합니다.")

    validate_chunk_headers(args.input_dir, model_spec.chunking_strategy)
    chunks = load_chunks(args.input_dir)
    source_digest = corpus_sha256(chunks)
    print(f"청크 검증 완료: {len(chunks)}개, SHA-256={source_digest}")
    if args.validate_only:
        return

    started_at = utc_timestamp()
    start_time = time.perf_counter()
    manifest_path = args.db_dir / DEFAULT_MANIFEST_NAME
    collection_metadata = expected_collection_metadata(
        source_digest, len(chunks), args.revision, model_spec
    )
    _, collection = get_or_create_collection(
        args.db_dir,
        args.collection_name,
        collection_metadata,
        args.rebuild,
    )
    existing_ids = existing_collection_ids(collection)
    source_ids = {chunk["chunk_id"] for chunk in chunks}
    unknown_ids = existing_ids - source_ids
    if unknown_ids:
        raise ValueError(
            f"현재 원본에 없는 ID가 기존 컬렉션에 있습니다: {len(unknown_ids)}개"
        )
    pending = [chunk for chunk in chunks if chunk["chunk_id"] not in existing_ids]
    print(f"기존 {len(existing_ids)}개, 신규 {len(pending)}개")

    model = None
    maximum_tokens = previous_maximum_token_count(manifest_path)
    try:
        if pending:
            model = load_sentence_transformer_model(
                model_spec,
                args.revision,
                args.model_cache,
                args.device,
            )
            maximum_tokens = maximum_token_count(model, chunks)
            print(f"최대 토큰 수: {maximum_tokens}/{model.max_seq_length}")

        processed = 0
        for batch in batched(pending, args.batch_size):
            texts = [chunk["retrieval_text"] for chunk in batch]
            embeddings = embedding_rows(
                encode_with_oom_split(model, texts), model_spec
            )
            collection.upsert(
                ids=[chunk["chunk_id"] for chunk in batch],
                embeddings=embeddings,
                documents=texts,
                metadatas=[chroma_metadata(chunk) for chunk in batch],
            )
            processed += len(batch)
            stored_count = collection.count()
            write_json(
                manifest_path,
                build_manifest(
                    args=args,
                    model_spec=model_spec,
                    source_digest=source_digest,
                    source_chunk_count=len(chunks),
                    stored_count=stored_count,
                    pending_count=len(pending) - processed,
                    maximum_tokens=maximum_tokens,
                    status="running",
                    started_at=started_at,
                    elapsed_seconds=time.perf_counter() - start_time,
                ),
            )
            print(f"저장 진행: {stored_count}/{len(chunks)}")

        stored_count = collection.count()
        if stored_count != len(chunks):
            raise ValueError(
                f"ChromaDB 저장 건수가 원본과 다릅니다: "
                f"{stored_count}/{len(chunks)}"
            )
        if existing_collection_ids(collection) != source_ids:
            raise ValueError("ChromaDB ID 집합이 원본 청크와 다릅니다.")

        write_json(
            manifest_path,
            build_manifest(
                args=args,
                model_spec=model_spec,
                source_digest=source_digest,
                source_chunk_count=len(chunks),
                stored_count=stored_count,
                pending_count=0,
                maximum_tokens=maximum_tokens,
                status="complete",
                started_at=started_at,
                elapsed_seconds=time.perf_counter() - start_time,
            ),
        )
        print(f"{model_spec.display_name} ChromaDB 구축 완료: {stored_count}개")
        print(f"DB 경로: {args.db_dir}")
        print(f"Manifest: {manifest_path}")
    except Exception as error:
        write_json(
            manifest_path,
            build_manifest(
                args=args,
                model_spec=model_spec,
                source_digest=source_digest,
                source_chunk_count=len(chunks),
                stored_count=collection.count(),
                pending_count=max(0, len(chunks) - collection.count()),
                maximum_tokens=maximum_tokens,
                status="failed",
                started_at=started_at,
                elapsed_seconds=time.perf_counter() - start_time,
                error=f"{type(error).__name__}: {error}",
            ),
        )
        raise
