# build_precedent_vectordb.py
"""
Description: 판례 A안 청크 JSONL을 읽어 임베딩 모델별 Chroma 벡터DB를 생성한다.
RunPod 같은 GPU 환경에서 KURE, BGE-M3, OpenAI 임베딩 모델을 같은 청크 기준으로 비교할 때 사용한다.
Author: choeminju
Date: 2026-09-04
Before:
    - local_data/precedents/chunks/A_reason_summary_v1/chunks.jsonl 파일이 있는 상태.

After:
    - local_data/precedents/vector_dbs/{chunking_strategy}__{provider}/ 하위에 Chroma 벡터DB가 생성.
    - 중간에 중단되어도 이미 저장된 chunk_id는 건너뛰고 이어서 생성.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DATA_ROOT = PROJECT_ROOT / "local_data"
DEFAULT_CHUNKS_PATH = (
    LOCAL_DATA_ROOT / "precedents" / "chunks" / "A_reason_summary_v1" / "chunks.jsonl"
)
DEFAULT_OUTPUT_ROOT = LOCAL_DATA_ROOT / "precedents" / "vector_dbs"
DEFAULT_BATCH_SIZE = 64

MODEL_CONFIGS = {
    "kure": {
        "provider": "sentence_transformers",
        "model": "nlpai-lab/KURE-v1",
        "collection": "precedents_a_kure",
    },
    "bge-m3": {
        "provider": "sentence_transformers",
        "model": "BAAI/bge-m3",
        "collection": "precedents_a_bge_m3",
    },
    "openai": {
        "provider": "openai",
        "model": "text-embedding-3-large",
        "collection": "precedents_a_openai_text_embedding_3_large",
    },
}


def parse_args() -> argparse.Namespace:
    """커맨드라인 옵션을 정의한다."""
    parser = argparse.ArgumentParser(description="Build precedent Chroma vector DB.")
    parser.add_argument(
        "--embedding",
        choices=sorted(MODEL_CONFIGS),
        required=True,
        help="사용할 임베딩 모델 설정 이름.",
    )
    parser.add_argument(
        "--chunks-path",
        type=Path,
        default=DEFAULT_CHUNKS_PATH,
        help="청크 JSONL 파일 경로.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="벡터DB 출력 루트 폴더.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="한 번에 임베딩할 청크 수.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="테스트용 처리 개수 제한.",
    )
    return parser.parse_args()


def now_utc_iso() -> str:
    """현재 UTC 시각을 ISO 문자열로 반환한다."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_chunks(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """청크 JSONL 파일을 읽어 벡터DB 적재 대상 목록으로 반환한다."""
    chunks: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            chunks.append(json.loads(line))
            if limit is not None and len(chunks) >= limit:
                break
    return chunks


def normalize_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool | None]:
    """Chroma에 저장 가능한 단순 타입으로 metadata 값을 정리한다."""
    normalized: dict[str, str | int | float | bool | None] = {}
    for key, value in metadata.items():
        if value is None or isinstance(value, str | int | float | bool):
            normalized[key] = value
        else:
            normalized[key] = json.dumps(value, ensure_ascii=False)
    return normalized


def build_records(chunks: list[dict[str, Any]]) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """청크 데이터에서 Chroma 저장용 ids, documents, metadatas를 만든다."""
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []

    for chunk in chunks:
        metadata = normalize_metadata(chunk.get("metadata") or {})
        metadata["chunk_id"] = chunk["chunk_id"]
        metadata["source_case_id"] = chunk["source_case_id"]
        metadata["chunk_type"] = chunk["chunk_type"]

        ids.append(chunk["chunk_id"])
        documents.append(chunk["retrieval_text"])
        metadatas.append(metadata)

    return ids, documents, metadatas


def batched_indexes(total: int, batch_size: int) -> range:
    """전체 개수를 batch_size 간격의 시작 인덱스로 순회한다."""
    return range(0, total, batch_size)


def existing_id_set(collection: Any) -> set[str]:
    """이미 벡터DB에 저장된 chunk_id 목록을 가져온다."""
    result = collection.get(include=[])
    return set(result.get("ids") or [])


def load_sentence_transformer(model_name: str) -> Any:
    """SentenceTransformers 계열 로컬 임베딩 모델을 한 번만 로드한다."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, trust_remote_code=True)


def embed_with_sentence_transformers(
    model: Any,
    texts: list[str],
    batch_size: int,
) -> list[list[float]]:
    """로드된 SentenceTransformers 모델로 텍스트를 임베딩한다."""
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vectors.tolist()


def load_openai_client() -> Any:
    """OpenAI 임베딩 API 클라이언트를 한 번만 생성한다."""
    from openai import OpenAI

    return OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


def embed_with_openai(client: Any, model_name: str, texts: list[str]) -> list[list[float]]:
    """생성된 OpenAI 클라이언트로 텍스트를 임베딩한다."""
    response = client.embeddings.create(model=model_name, input=texts)
    return [item.embedding for item in response.data]


def write_manifest(
    output_dir: Path,
    args: argparse.Namespace,
    config: dict[str, str],
    total_chunks: int,
    collection_count: int,
    elapsed_seconds: float,
) -> None:
    """벡터DB 생성 설정과 결과 통계를 manifest로 저장한다."""
    manifest = {
        "schema_version": "precedent_vector_db_manifest.v1",
        "created_at": now_utc_iso(),
        "chunks_path": str(args.chunks_path),
        "output_dir": str(output_dir),
        "embedding": args.embedding,
        "embedding_provider": config["provider"],
        "embedding_model": config["model"],
        "collection": config["collection"],
        "total_input_chunks": total_chunks,
        "stored_chunks": collection_count,
        "batch_size": args.batch_size,
        "elapsed_seconds": round(elapsed_seconds, 2),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """청크 JSONL을 선택한 임베딩 모델로 변환해 Chroma 벡터DB에 저장한다."""
    import chromadb

    args = parse_args()
    config = MODEL_CONFIGS[args.embedding]
    output_dir = args.output_root / f"A_reason_summary_v1__{args.embedding}"
    output_dir.mkdir(parents=True, exist_ok=True)

    chunks = read_chunks(args.chunks_path, args.limit)
    ids, documents, metadatas = build_records(chunks)

    client = chromadb.PersistentClient(path=str(output_dir / "chroma"))
    collection = client.get_or_create_collection(
        name=config["collection"],
        metadata={
            "chunking_strategy": "A_reason_summary_v1",
            "embedding_model": config["model"],
        },
    )
    existing_ids = existing_id_set(collection)

    print(f"벡터DB 생성 시작: embedding={args.embedding} model={config['model']}")
    print(f"입력 청크 {len(ids)}개, 이미 저장된 청크 {len(existing_ids)}개")

    embedding_client: Any
    if config["provider"] == "sentence_transformers":
        embedding_client = load_sentence_transformer(config["model"])
    elif config["provider"] == "openai":
        embedding_client = load_openai_client()
    else:
        raise ValueError(f"지원하지 않는 provider입니다: {config['provider']}")

    start_time = time.time()
    completed = 0
    skipped = 0

    for start in batched_indexes(len(ids), args.batch_size):
        end = min(start + args.batch_size, len(ids))
        batch_ids = ids[start:end]
        pending_indexes = [
            offset for offset, chunk_id in enumerate(batch_ids) if chunk_id not in existing_ids
        ]

        if not pending_indexes:
            skipped += len(batch_ids)
            continue

        pending_ids = [batch_ids[offset] for offset in pending_indexes]
        pending_documents = [documents[start + offset] for offset in pending_indexes]
        pending_metadatas = [metadatas[start + offset] for offset in pending_indexes]

        batch_start = time.time()
        if config["provider"] == "sentence_transformers":
            embeddings = embed_with_sentence_transformers(
                embedding_client,
                pending_documents,
                args.batch_size,
            )
        elif config["provider"] == "openai":
            embeddings = embed_with_openai(embedding_client, config["model"], pending_documents)

        collection.add(
            ids=pending_ids,
            documents=pending_documents,
            metadatas=pending_metadatas,
            embeddings=embeddings,
        )

        completed += len(pending_ids)
        elapsed = time.time() - start_time
        avg = elapsed / max(completed, 1)
        remaining = max(len(ids) - start - len(batch_ids), 0)
        eta = remaining * avg
        print(
            f"진행 {end}/{len(ids)} 저장 {completed}건 스킵 {skipped}건 "
            f"배치 {time.time() - batch_start:.1f}초 경과 {elapsed:.1f}초 예상남음 {eta:.1f}초",
            flush=True,
        )

    total_elapsed = time.time() - start_time
    collection_count = collection.count()
    write_manifest(output_dir, args, config, len(ids), collection_count, total_elapsed)
    print(f"완료: {output_dir}")
    print(f"저장 청크 수: {collection_count}")


if __name__ == "__main__":
    main()
