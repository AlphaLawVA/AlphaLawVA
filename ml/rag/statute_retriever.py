# statute_retriever.py
"""
Description: BGE-M3 질의 임베딩으로 승인된 법령 ChromaDB를 검색하고
승인된 조문 단위 청킹과 출처 메타데이터를 검증해 순위 결과를 반환한다.
Author: ooheunsu
Date: 2026-09-05
Before:
    - 완성된 BGE-M3 모델 캐시와 법령 ChromaDB 및 구축 manifest가 존재.

After:
    - 서비스와 RAG 도구에서 재사용할 수 있는 지연 로딩 검색기가 제공됨.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Sequence

from ml.data_collection.statutes.law_api_common import PROJECT_ROOT
from ml.embedding.build_bge_m3_statute_chroma import MODEL_SPEC


DEFAULT_MODEL_CACHE = PROJECT_ROOT / "data/statutes/models"
DEFAULT_DB_DIR = MODEL_SPEC.default_db_dir
DEFAULT_MANIFEST = DEFAULT_DB_DIR / "build_manifest.json"
DEFAULT_TOP_K = 10


@dataclass(frozen=True)
class StatuteSearchResult:
    rank: int
    chunk_id: str
    distance: float
    similarity: float
    text: str
    law_id: str | None
    law_name: str | None
    article_label: str | None
    article_title: str | None
    effective_date: str | None
    heading_path: str | None
    source_node_id: str | None
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"BGE-M3 구축 manifest가 없습니다: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_bge_store(
    collection: Any,
    manifest: dict[str, Any],
) -> None:
    expected = {
        "model_name": MODEL_SPEC.model_name,
        "model_revision": MODEL_SPEC.revision,
        "embedding_dimension": MODEL_SPEC.dimension,
        "collection_name": MODEL_SPEC.collection_name,
        "normalize_embeddings": True,
        "chunk_schema_version": "0.2",
        "chunking_strategy": MODEL_SPEC.chunking_strategy,
    }
    if manifest.get("status") != "complete":
        raise ValueError(
            "BGE-M3 법령 DB 구축이 완료 상태가 아닙니다: "
            f"{manifest.get('status')!r}"
        )
    manifest_keys = (
        "model_name",
        "model_revision",
        "embedding_dimension",
        "collection_name",
        "normalize_embeddings",
    )
    mismatches = {
        key: {"expected": value, "actual": manifest.get(key)}
        for key in manifest_keys
        if manifest.get(key) != (value := expected[key])
    }

    metadata = collection.metadata or {}
    for key in (
        "model_name",
        "model_revision",
        "embedding_dimension",
        "normalize_embeddings",
        "chunk_schema_version",
        "chunking_strategy",
    ):
        if metadata.get(key) != expected[key]:
            mismatches[f"collection.{key}"] = {
                "expected": expected[key],
                "actual": metadata.get(key),
            }
    if metadata.get("hnsw:space") != "cosine":
        mismatches["collection.hnsw:space"] = {
            "expected": "cosine",
            "actual": metadata.get("hnsw:space"),
        }
    if metadata.get("source_sha256") != manifest.get("source_sha256"):
        mismatches["collection.source_sha256"] = {
            "expected": manifest.get("source_sha256"),
            "actual": metadata.get("source_sha256"),
        }

    source_count = manifest.get("source_chunk_count")
    if metadata.get("source_chunk_count") != source_count:
        mismatches["collection.source_chunk_count"] = {
            "expected": source_count,
            "actual": metadata.get("source_chunk_count"),
        }

    stored_count = manifest.get("stored_count")
    if collection.count() != stored_count:
        mismatches["collection.count"] = {
            "expected": stored_count,
            "actual": collection.count(),
        }
    if mismatches:
        raise ValueError(f"BGE-M3 법령 DB 설정이 일치하지 않습니다: {mismatches}")


def load_bge_collection(
    db_dir: Path,
) -> tuple[Any, Any, dict[str, Any]]:
    try:
        import chromadb
    except ImportError as error:
        raise RuntimeError(
            "ChromaDB가 없습니다. "
            "pip install -r requirements-embedding.txt 를 실행하세요."
        ) from error

    resolved = db_dir.resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(f"BGE-M3 법령 DB가 없습니다: {resolved}")
    manifest = read_manifest(resolved / "build_manifest.json")
    client = chromadb.PersistentClient(path=str(resolved))
    collection = client.get_collection(MODEL_SPEC.collection_name)
    validate_bge_store(collection, manifest)
    return client, collection, manifest


def load_bge_model(model_cache: Path, device: str) -> Any:
    try:
        import torch
        from sentence_transformers import SentenceTransformer
    except ImportError as error:
        raise RuntimeError(
            "BGE-M3 실행 패키지가 없습니다. "
            "pip install -r requirements-embedding.txt 를 실행하세요."
        ) from error

    selected_device = device
    if selected_device == "auto":
        selected_device = "cuda" if torch.cuda.is_available() else "cpu"
    model = SentenceTransformer(
        MODEL_SPEC.model_name,
        revision=MODEL_SPEC.revision,
        cache_folder=str(model_cache.resolve()),
        device=selected_device,
        local_files_only=True,
    )
    dimension = model.get_sentence_embedding_dimension()
    if dimension != MODEL_SPEC.dimension:
        raise ValueError(
            "BGE-M3 질의 임베딩 차원이 다릅니다: "
            f"{dimension} != {MODEL_SPEC.dimension}"
        )
    return model


class BgeM3StatuteRetriever:
    def __init__(
        self,
        *,
        db_dir: Path = DEFAULT_DB_DIR,
        model_cache: Path = DEFAULT_MODEL_CACHE,
        device: str = "auto",
        model_loader: Callable[[Path, str], Any] = load_bge_model,
        collection_loader: Callable[
            [Path], tuple[Any, Any, dict[str, Any]]
        ] = load_bge_collection,
    ) -> None:
        if device not in {"auto", "cpu", "cuda"}:
            raise ValueError(f"지원하지 않는 device: {device}")
        self.db_dir = db_dir
        self.model_cache = model_cache
        self.device = device
        self._model_loader = model_loader
        self._collection_loader = collection_loader
        self._model: Any | None = None
        self._client: Any | None = None
        self._collection: Any | None = None
        self._manifest: dict[str, Any] | None = None
        self._lock = RLock()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None and self._collection is not None

    def load(self) -> None:
        if self.is_loaded:
            return
        with self._lock:
            if self.is_loaded:
                return
            client, collection, manifest = self._collection_loader(self.db_dir)
            model = self._model_loader(self.model_cache, self.device)
            self._client = client
            self._collection = collection
            self._manifest = manifest
            self._model = model

    def search(
        self,
        query: str,
        *,
        top_k: int = DEFAULT_TOP_K,
    ) -> list[StatuteSearchResult]:
        return self.search_many([query], top_k=top_k)[0]

    def search_many(
        self,
        queries: Sequence[str],
        *,
        top_k: int = DEFAULT_TOP_K,
    ) -> list[list[StatuteSearchResult]]:
        normalized = self._validate_queries(queries, top_k)
        self.load()
        assert self._model is not None
        assert self._collection is not None

        with self._lock:
            self._validate_query_lengths(normalized)
            encoded = self._model.encode(
                normalized,
                batch_size=min(32, len(normalized)),
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            embeddings = encoded.tolist() if hasattr(encoded, "tolist") else encoded
            if any(len(row) != MODEL_SPEC.dimension for row in embeddings):
                raise ValueError("BGE-M3 질의 임베딩 차원이 다릅니다.")
            result = self._collection.query(
                query_embeddings=embeddings,
                n_results=min(top_k, self._collection.count()),
                include=["documents", "metadatas", "distances"],
            )
        return self._parse_results(result, len(normalized))

    @staticmethod
    def _validate_queries(queries: Sequence[str], top_k: int) -> list[str]:
        if top_k < 1:
            raise ValueError("top_k는 1 이상이어야 합니다.")
        if not queries:
            raise ValueError("검색 질문이 없습니다.")
        normalized = []
        for query in queries:
            if not isinstance(query, str) or not query.strip():
                raise ValueError("검색 질문은 비어 있지 않은 문자열이어야 합니다.")
            normalized.append(query.strip())
        return normalized

    def _validate_query_lengths(self, queries: list[str]) -> None:
        tokenizer = getattr(self._model, "tokenizer", None)
        max_length = getattr(self._model, "max_seq_length", None)
        if tokenizer is None or not isinstance(max_length, int):
            return
        for query in queries:
            token_ids = tokenizer.encode(
                query,
                add_special_tokens=True,
                truncation=False,
            )
            if len(token_ids) > max_length:
                raise ValueError(
                    "검색 질문이 BGE-M3 입력 한도를 넘습니다: "
                    f"{len(token_ids)} > {max_length}"
                )

    @staticmethod
    def _parse_results(
        result: dict[str, Any],
        query_count: int,
    ) -> list[list[StatuteSearchResult]]:
        fields = ("ids", "documents", "metadatas", "distances")
        if any(len(result.get(field, [])) != query_count for field in fields):
            raise ValueError("ChromaDB 검색 결과의 질문 수가 일치하지 않습니다.")

        parsed = []
        for index in range(query_count):
            rows = []
            values = [result[field][index] for field in fields]
            for rank, (chunk_id, document, metadata, distance) in enumerate(
                zip(*values, strict=True),
                start=1,
            ):
                metadata = metadata or {}
                numeric_distance = float(distance)
                rows.append(
                    StatuteSearchResult(
                        rank=rank,
                        chunk_id=chunk_id,
                        distance=numeric_distance,
                        similarity=1.0 - numeric_distance,
                        text=document,
                        law_id=metadata.get("law_id"),
                        law_name=metadata.get("law_name"),
                        article_label=metadata.get("article_label"),
                        article_title=metadata.get("article_title"),
                        effective_date=metadata.get("effective_date"),
                        heading_path=metadata.get("heading_path"),
                        source_node_id=metadata.get("source_node_id"),
                        metadata=dict(metadata),
                    )
                )
            parsed.append(rows)
        return parsed
