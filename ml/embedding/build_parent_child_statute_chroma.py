# build_parent_child_statute_chroma.py
"""
Description: 부모-자식 법령 청크를 BGE-M3 또는 KURE-v1로 임베딩해
기준선 DB와 분리된 ChromaDB를 구축한다.
Author: ooheunsu
Date: 2026-09-05
Before:
    - data/statutes/parent_child_chunks/에 검증된 자식 검색 청크가 존재.
After:
    - 선택 모델의 부모-자식 전용 ChromaDB와 구축 manifest가 생성.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ml.embedding.build_bge_m3_statute_chroma import (
    MODEL_DIMENSION as BGE_DIMENSION,
)
from ml.embedding.build_bge_m3_statute_chroma import (
    MODEL_MAX_TOKENS as BGE_MAX_TOKENS,
)
from ml.embedding.build_bge_m3_statute_chroma import MODEL_NAME as BGE_NAME
from ml.embedding.build_bge_m3_statute_chroma import (
    MODEL_REVISION as BGE_REVISION,
)
from ml.embedding.build_kure_statute_chroma import (
    MODEL_DIMENSION as KURE_DIMENSION,
)
from ml.embedding.build_kure_statute_chroma import (
    MODEL_MAX_TOKENS as KURE_MAX_TOKENS,
)
from ml.embedding.build_kure_statute_chroma import MODEL_NAME as KURE_NAME
from ml.embedding.build_kure_statute_chroma import (
    MODEL_REVISION as KURE_REVISION,
)
from ml.embedding.statute_chroma_builder import EmbeddingModelSpec, run_build
from ml.preprocessing.statutes.create_statute_parent_child_chunks import (
    CHUNKING_STRATEGY,
)


MODEL_SPECS = {
    "bge_m3": EmbeddingModelSpec(
        display_name="BGE-M3 dense parent-child",
        model_name=BGE_NAME,
        revision=BGE_REVISION,
        dimension=BGE_DIMENSION,
        max_tokens=BGE_MAX_TOKENS,
        collection_name="statutes_bge_m3_dense_parent_child_v01",
        db_dir_name="bge_m3_parent_child",
        chunking_strategy=CHUNKING_STRATEGY,
        input_dir_name="parent_child_chunks",
    ),
    "kure_v1": EmbeddingModelSpec(
        display_name="KURE-v1 parent-child",
        model_name=KURE_NAME,
        revision=KURE_REVISION,
        dimension=KURE_DIMENSION,
        max_tokens=KURE_MAX_TOKENS,
        collection_name="statutes_kure_v1_parent_child_v01",
        db_dir_name="kure_v1_parent_child",
        chunking_strategy=CHUNKING_STRATEGY,
        input_dir_name="parent_child_chunks",
    ),
}


def parse_args() -> tuple[EmbeddingModelSpec, argparse.Namespace]:
    parser = argparse.ArgumentParser(
        description="BGE-M3 또는 KURE-v1 부모-자식 법령 DB 구축"
    )
    parser.add_argument("--model", choices=tuple(MODEL_SPECS), required=True)
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--db-dir", type=Path)
    parser.add_argument("--model-cache", type=Path)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", choices=("auto", "cuda"), default="auto")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    spec = MODEL_SPECS[args.model]
    args.input_dir = args.input_dir or spec.default_input_dir
    args.db_dir = args.db_dir or spec.default_db_dir
    args.model_cache = args.model_cache or Path("data/statutes/models")
    args.collection_name = spec.collection_name
    args.revision = spec.revision
    return spec, args


def main() -> None:
    spec, args = parse_args()
    run_build(spec, args)


if __name__ == "__main__":
    main()
