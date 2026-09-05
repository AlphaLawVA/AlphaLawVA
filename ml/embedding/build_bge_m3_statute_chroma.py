# build_bge_m3_statute_chroma.py
"""
Description: 조문 청크 전체를 BGE-M3의 dense embedding으로 변환해
독립 ChromaDB 컬렉션을 구축하고 원본·모델·저장 건수를 검증한다.
Author: ooheunsu
Date: 2026-08-30
Before:
    - data/statutes/chunks/에 KURE-v1 실험과 동일한 검증 청크 8,933개가 존재.

After:
    - data/statutes/vectorstores/bge_m3/에 ChromaDB와 구축 manifest가 생성.
"""

from ml.data_collection.statutes.law_api_common import PROJECT_ROOT
from ml.embedding.statute_chroma_builder import EmbeddingModelSpec, run_build


MODEL_NAME = "BAAI/bge-m3"
MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
MODEL_DIMENSION = 1024
MODEL_MAX_TOKENS = 8192
COLLECTION_NAME = "statutes_bge_m3_dense_article_v01"
DEFAULT_DB_DIR = PROJECT_ROOT / "data/statutes/vectorstores/bge_m3"
MODEL_SPEC = EmbeddingModelSpec(
    display_name="BGE-M3 dense",
    model_name=MODEL_NAME,
    revision=MODEL_REVISION,
    dimension=MODEL_DIMENSION,
    max_tokens=MODEL_MAX_TOKENS,
    collection_name=COLLECTION_NAME,
    db_dir_name="bge_m3",
)


def main() -> None:
    run_build(MODEL_SPEC)


if __name__ == "__main__":
    main()
