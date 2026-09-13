# build_openai_statute_chroma.py
"""
Description: 법령 조문 청크의 OpenAI 임베딩 비용을 사전 계산하고 승인 후
text-embedding-3-large로 별도 ChromaDB를 재시작 가능하게 구축한다.
Author: ooheunsu
Date: 2026-08-31
Before:
    - data/statutes/chunks/에 후보 모델 공통 청크가 있고 실행 시 OPENAI_API_KEY가 설정된 상태.

After:
    - 비용 보고서와 data/statutes/vectorstores/text_embedding_3_large_3072/의 DB·manifest가 생성.
"""

import argparse
import importlib.metadata
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ml.data_collection.statutes.law_api_common import (
    PROJECT_ROOT,
    read_json,
    write_json,
)
from ml.embedding.analyze_statute_chunk_tokens import load_chunks
from ml.embedding.statute_chroma_builder import (
    EmbeddingModelSpec,
    chroma_metadata,
    corpus_sha256,
    existing_collection_ids,
    expected_collection_metadata,
    get_or_create_collection,
    validate_chunk_headers,
)


MODEL_NAME = "text-embedding-3-large"
MODEL_REVISION = "text-embedding-3-large"
MODEL_DIMENSION = 3072
MODEL_MAX_TOKENS = 8191
PRICE_USD_PER_MILLION_TOKENS = 0.13
MAX_API_INPUTS = 2048
MAX_API_TOKENS = 300_000
COLLECTION_NAME = "statutes_text_embedding_3_large_3072_article_v01"
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data/statutes/chunks"
DEFAULT_DB_DIR = (
    PROJECT_ROOT
    / "data/statutes/vectorstores/text_embedding_3_large_3072"
)
DEFAULT_REPORT = (
    PROJECT_ROOT
    / "data/statutes/reports/openai_embedding_cost_estimate_v01.json"
)
DEFAULT_TIKTOKEN_CACHE = (
    PROJECT_ROOT / "data/statutes/reports/tokenizer_cache/tiktoken"
)
DEFAULT_MANIFEST_NAME = "build_manifest.json"
MANIFEST_VERSION = "0.1"
MODEL_SPEC = EmbeddingModelSpec(
    display_name="text-embedding-3-large",
    model_name=MODEL_NAME,
    revision=MODEL_REVISION,
    dimension=MODEL_DIMENSION,
    max_tokens=MODEL_MAX_TOKENS,
    collection_name=COLLECTION_NAME,
    db_dir_name="text_embedding_3_large_3072",
)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def project_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def package_versions() -> dict[str, str | None]:
    versions = {}
    for package in ("chromadb", "openai", "tiktoken"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def load_token_encoder(cache_dir: Path) -> Any:
    try:
        import tiktoken
    except ImportError as error:
        raise RuntimeError(
            "비용 계산에는 tiktoken이 필요합니다. "
            "pip install -r requirements-openai-embedding.txt 를 실행하세요."
        ) from error

    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["TIKTOKEN_CACHE_DIR"] = str(cache_dir)
    return tiktoken.get_encoding("cl100k_base")


def token_counts(chunks: list[dict], encoder: Any) -> dict[str, int]:
    counts = {}
    for chunk in chunks:
        count = len(
            encoder.encode(
                chunk["retrieval_text"],
                disallowed_special=(),
            )
        )
        if count > MODEL_MAX_TOKENS:
            raise ValueError(
                f"OpenAI 입력 한도 초과: {chunk['chunk_id']} "
                f"({count}/{MODEL_MAX_TOKENS})"
            )
        counts[chunk["chunk_id"]] = count
    return counts


def request_batches(
    chunks: list[dict],
    counts: dict[str, int],
    max_items: int,
    max_tokens: int,
) -> list[list[dict]]:
    if not 1 <= max_items <= MAX_API_INPUTS:
        raise ValueError(f"max_batch_items는 1~{MAX_API_INPUTS}이어야 합니다.")
    if not 1 <= max_tokens <= MAX_API_TOKENS:
        raise ValueError(
            f"max_batch_tokens는 1~{MAX_API_TOKENS}이어야 합니다."
        )

    batches: list[list[dict]] = []
    current: list[dict] = []
    current_tokens = 0
    for chunk in chunks:
        count = counts[chunk["chunk_id"]]
        if count > max_tokens:
            raise ValueError(
                f"청크 하나가 요청 토큰 제한을 초과합니다: "
                f"{chunk['chunk_id']} ({count}/{max_tokens})"
            )
        if current and (
            len(current) >= max_items or current_tokens + count > max_tokens
        ):
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(chunk)
        current_tokens += count
    if current:
        batches.append(current)
    return batches


def estimated_cost_usd(token_total: int) -> float:
    return round(
        token_total / 1_000_000 * PRICE_USD_PER_MILLION_TOKENS,
        6,
    )


def build_cost_report(
    chunks: list[dict],
    counts: dict[str, int],
    source_digest: str,
    request_count: int,
) -> dict:
    values = list(counts.values())
    token_total = sum(values)
    return {
        "report_version": "0.1",
        "generated_at": utc_timestamp(),
        "model_name": MODEL_NAME,
        "embedding_dimension": MODEL_DIMENSION,
        "source_sha256": source_digest,
        "chunk_count": len(chunks),
        "total_input_tokens": token_total,
        "maximum_input_tokens": max(values, default=0),
        "max_input_exceeded": sum(
            count > MODEL_MAX_TOKENS for count in values
        ),
        "estimated_request_count": request_count,
        "price_usd_per_million_tokens": PRICE_USD_PER_MILLION_TOKENS,
        "estimated_cost_usd": estimated_cost_usd(token_total),
        "pricing_source": (
            "https://developers.openai.com/api/docs/models/"
            "text-embedding-3-large"
        ),
    }


def load_openai_client() -> Any:
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError as error:
        raise RuntimeError(
            "환경변수 로딩에는 python-dotenv가 필요합니다. "
            "pip install -r requirements-openai-embedding.txt 를 실행하세요."
        ) from error

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(".env에 OPENAI_API_KEY를 설정하세요.")

    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError(
            "OpenAI SDK가 없습니다. "
            "pip install -r requirements-openai-embedding.txt 를 실행하세요."
        ) from error
    return OpenAI(api_key=api_key, max_retries=6, timeout=120.0)


def response_embeddings(response: Any, expected_count: int) -> list[list[float]]:
    ordered = sorted(response.data, key=lambda item: item.index)
    if [item.index for item in ordered] != list(range(expected_count)):
        raise ValueError("OpenAI 응답 인덱스가 요청 순서와 다릅니다.")
    rows = [item.embedding for item in ordered]
    for row in rows:
        if len(row) != MODEL_DIMENSION:
            raise ValueError(
                f"OpenAI 임베딩 차원이 {MODEL_DIMENSION}이 아닙니다: "
                f"{len(row)}"
            )
    return rows


def previous_usage(manifest_path: Path) -> tuple[int, int]:
    if not manifest_path.exists():
        return 0, 0
    manifest = read_json(manifest_path)
    tokens = manifest.get("actual_input_tokens", 0)
    requests = manifest.get("request_count", 0)
    return (
        tokens if isinstance(tokens, int) else 0,
        requests if isinstance(requests, int) else 0,
    )


def build_manifest(
    *,
    args: argparse.Namespace,
    source_digest: str,
    source_chunk_count: int,
    stored_count: int,
    pending_count: int,
    estimated_input_tokens: int,
    actual_input_tokens: int,
    request_count: int,
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
        "collection_name": COLLECTION_NAME,
        "model_name": MODEL_NAME,
        "model_revision": MODEL_REVISION,
        "embedding_dimension": MODEL_DIMENSION,
        "source_sha256": source_digest,
        "source_chunk_count": source_chunk_count,
        "stored_count": stored_count,
        "pending_count": pending_count,
        "estimated_input_tokens": estimated_input_tokens,
        "actual_input_tokens": actual_input_tokens,
        "request_count": request_count,
        "price_usd_per_million_tokens": PRICE_USD_PER_MILLION_TOKENS,
        "estimated_cost_usd": estimated_cost_usd(estimated_input_tokens),
        "actual_cost_usd": estimated_cost_usd(actual_input_tokens),
        "max_batch_items": args.max_batch_items,
        "max_batch_tokens": args.max_batch_tokens,
        "package_versions": package_versions(),
        "error": error,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "text-embedding-3-large 비용을 계산하고 선택적으로 DB를 구축합니다."
        )
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--tiktoken-cache",
        type=Path,
        default=DEFAULT_TIKTOKEN_CACHE,
    )
    parser.add_argument("--max-batch-items", type=int, default=64)
    parser.add_argument("--max-batch-tokens", type=int, default=250_000)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.input_dir = args.input_dir.resolve()
    args.db_dir = args.db_dir.resolve()
    args.report = args.report.resolve()
    args.tiktoken_cache = args.tiktoken_cache.resolve()

    validate_chunk_headers(args.input_dir)
    chunks = load_chunks(args.input_dir)
    source_digest = corpus_sha256(chunks)
    encoder = load_token_encoder(args.tiktoken_cache)
    counts = token_counts(chunks, encoder)
    all_batches = request_batches(
        chunks,
        counts,
        args.max_batch_items,
        args.max_batch_tokens,
    )
    report = build_cost_report(
        chunks,
        counts,
        source_digest,
        len(all_batches),
    )
    write_json(args.report, report)
    print(
        f"비용 계산 완료: {len(chunks)}개, "
        f"{report['total_input_tokens']}토큰, "
        f"예상 ${report['estimated_cost_usd']:.6f}"
    )
    print(f"비용 보고서: {args.report}")
    if not args.execute:
        print("API는 호출하지 않았습니다. 실행하려면 --execute를 추가하세요.")
        return

    client = load_openai_client()
    metadata = expected_collection_metadata(
        source_digest,
        len(chunks),
        MODEL_REVISION,
        MODEL_SPEC,
    )
    _, collection = get_or_create_collection(
        args.db_dir,
        COLLECTION_NAME,
        metadata,
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
    pending_batches = request_batches(
        pending,
        counts,
        args.max_batch_items,
        args.max_batch_tokens,
    )
    print(f"기존 {len(existing_ids)}개, 신규 {len(pending)}개")

    manifest_path = args.db_dir / DEFAULT_MANIFEST_NAME
    if existing_ids and not manifest_path.exists():
        raise ValueError(
            "기존 OpenAI 컬렉션에 구축 manifest가 없습니다. "
            "다른 DB 경로를 사용하거나 --rebuild를 명시하세요."
        )
    previous_tokens, previous_requests = (
        (0, 0) if args.rebuild else previous_usage(manifest_path)
    )
    actual_tokens = previous_tokens
    request_count = previous_requests
    started_at = utc_timestamp()
    start_time = time.perf_counter()
    try:
        for batch in pending_batches:
            texts = [chunk["retrieval_text"] for chunk in batch]
            response = client.embeddings.create(
                model=MODEL_NAME,
                input=texts,
                dimensions=MODEL_DIMENSION,
                encoding_format="float",
            )
            embeddings = response_embeddings(response, len(batch))
            collection.upsert(
                ids=[chunk["chunk_id"] for chunk in batch],
                embeddings=embeddings,
                documents=texts,
                metadatas=[chroma_metadata(chunk) for chunk in batch],
            )
            actual_tokens += int(response.usage.prompt_tokens)
            request_count += 1
            stored_count = collection.count()
            write_json(
                manifest_path,
                build_manifest(
                    args=args,
                    source_digest=source_digest,
                    source_chunk_count=len(chunks),
                    stored_count=stored_count,
                    pending_count=len(chunks) - stored_count,
                    estimated_input_tokens=report["total_input_tokens"],
                    actual_input_tokens=actual_tokens,
                    request_count=request_count,
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
                source_digest=source_digest,
                source_chunk_count=len(chunks),
                stored_count=stored_count,
                pending_count=0,
                estimated_input_tokens=report["total_input_tokens"],
                actual_input_tokens=actual_tokens,
                request_count=request_count,
                status="complete",
                started_at=started_at,
                elapsed_seconds=time.perf_counter() - start_time,
            ),
        )
        print(f"text-embedding-3-large ChromaDB 구축 완료: {stored_count}개")
        print(f"DB 경로: {args.db_dir}")
        print(f"Manifest: {manifest_path}")
    except Exception as error:
        write_json(
            manifest_path,
            build_manifest(
                args=args,
                source_digest=source_digest,
                source_chunk_count=len(chunks),
                stored_count=collection.count(),
                pending_count=max(0, len(chunks) - collection.count()),
                estimated_input_tokens=report["total_input_tokens"],
                actual_input_tokens=actual_tokens,
                request_count=request_count,
                status="failed",
                started_at=started_at,
                elapsed_seconds=time.perf_counter() - start_time,
                error=f"{type(error).__name__}: {error}",
            ),
        )
        raise


if __name__ == "__main__":
    main()
