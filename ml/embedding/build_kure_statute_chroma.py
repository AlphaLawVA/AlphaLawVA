# build_kure_statute_chroma.py
"""
Description: 조문 청크 전체를 KURE-v1로 임베딩하고 모델별 ChromaDB
컬렉션을 생성하며 원본 일치 여부와 저장 건수를 검증한다.
Author: ooheunsu
Date: 2026-08-30
Before:
    - data/statutes/chunks/에 검증된 조문 청크 8,933개가 생성된 상태.

After:
    - data/statutes/vectorstores/kure_v1/에 ChromaDB와 구축 manifest가 생성.
"""

from pathlib import Path
from typing import Any

from ml.data_collection.statutes.law_api_common import PROJECT_ROOT
from ml.embedding.statute_chroma_builder import (
    EmbeddingModelSpec,
    chroma_metadata,
    corpus_sha256,
    embedding_rows as shared_embedding_rows,
    encode_with_oom_split,
    expected_collection_metadata as shared_collection_metadata,
    parse_args as shared_parse_args,
    previous_maximum_token_count,
    run_build,
    validate_collection_metadata,
)


__all__ = (
    "chroma_metadata",
    "corpus_sha256",
    "encode_with_oom_split",
    "previous_maximum_token_count",
    "validate_collection_metadata",
)


MODEL_NAME = "nlpai-lab/KURE-v1"
MODEL_REVISION = "4ed4540949c70b7da2c74004a915e1f2d5e46e4f"
MODEL_DIMENSION = 1024
MODEL_MAX_TOKENS = 8192
COLLECTION_NAME = "statutes_kure_v1_article_v01"
DEFAULT_DB_DIR = PROJECT_ROOT / "data/statutes/vectorstores/kure_v1"
MODEL_SPEC = EmbeddingModelSpec(
    display_name="KURE-v1",
    model_name=MODEL_NAME,
    revision=MODEL_REVISION,
    dimension=MODEL_DIMENSION,
    max_tokens=MODEL_MAX_TOKENS,
    collection_name=COLLECTION_NAME,
    db_dir_name="kure_v1",
)


def embedding_rows(embeddings: list[Any]) -> list[list[float]]:
    return shared_embedding_rows(embeddings, MODEL_SPEC)


def expected_collection_metadata(
    source_digest: str,
    chunk_count: int,
    revision: str,
) -> dict[str, str | int | bool]:
    return shared_collection_metadata(
        source_digest,
        chunk_count,
        revision,
        MODEL_SPEC,
    )


def load_kure_model(revision: str, cache_dir: Path, device: str) -> Any:
    from ml.embedding.statute_chroma_builder import (
        load_sentence_transformer_model,
    )

    return load_sentence_transformer_model(
        MODEL_SPEC,
        revision,
        cache_dir,
        device,
    )


def parse_args():
    return shared_parse_args(MODEL_SPEC)


def main() -> None:
    run_build(MODEL_SPEC)


if __name__ == "__main__":
    main()
