# precedent_retriever.py
"""
Description: BGE-M3 질의 임베딩으로 판례 ChromaDB를 검색하고
청크 결과를 판례일련번호 단위로 묶어 순위 결과를 반환한다.
Author: choeminju
Date: 2026-09-14
Before:
    - A_reason_summary_v1 청킹과 BGE-M3 임베딩으로 만든 판례 ChromaDB가 존재.
After:
    - 서비스와 RAG 도구에서 재사용할 수 있는 판례 단위 지연 로딩 검색기가 제공됨.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Sequence

from ml.data_collection.statutes.law_api_common import PROJECT_ROOT


BGE_M3_MODEL_NAME = "BAAI/bge-m3"
BGE_M3_DIMENSION = 1024
DEFAULT_COLLECTION_NAME = "precedents_a_bge_m3"
DEFAULT_CHUNKING_STRATEGY = "A_reason_summary_v1"
DEFAULT_MODEL_CACHE = PROJECT_ROOT / "data" / "statutes" / "models"
DEFAULT_DB_DIR = (
    PROJECT_ROOT
    / "local_data"
    / "precedents"
    / "vector_dbs"
    / "precedents_bge-m3_v1"
)
DEFAULT_TOP_K = 10
DEFAULT_CANDIDATE_MULTIPLIER = 5


@dataclass(frozen=True)
class PrecedentChunkMatch:
    rank: int
    chunk_id: str
    distance: float
    similarity: float
    text: str
    chunk_type: str | None
    section: str | None
    section_chunk_index: int | None
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PrecedentSearchResult:
    rank: int
    precedent_id: str
    distance: float
    similarity: float
    text: str
    case_no: str | None
    case_no_list: str | None
    case_name: str | None
    court_name: str | None
    decision_date: str | None
    decision_year: int | None
    case_type: str | None
    judgment_type: str | None
    source_path: str | None
    matched_chunks: list[PrecedentChunkMatch]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"판례 벡터DB manifest가 없습니다: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_precedent_bge_store(
    collection: Any,
    manifest: dict[str, Any],
) -> None:
    required_expected = {
        "schema_version": "precedent_vector_db_manifest.v1",
        "embedding": "bge-m3",
        "embedding_provider": "sentence_transformers",
        "embedding_model": BGE_M3_MODEL_NAME,
        "collection": DEFAULT_COLLECTION_NAME,
    }
    mismatches = {
        key: {"expected": value, "actual": manifest.get(key)}
        for key, value in required_expected.items()
        if manifest.get(key) != value
    }
    if (
        "chunking_strategy" in manifest
        and manifest.get("chunking_strategy") != DEFAULT_CHUNKING_STRATEGY
    ):
        mismatches["chunking_strategy"] = {
            "expected": DEFAULT_CHUNKING_STRATEGY,
            "actual": manifest.get("chunking_strategy"),
        }

    metadata = collection.metadata or {}
    metadata_expected = {
        "chunking_strategy": DEFAULT_CHUNKING_STRATEGY,
        "embedding_model": BGE_M3_MODEL_NAME,
    }
    for key, value in metadata_expected.items():
        if key in metadata and metadata.get(key) != value:
            mismatches[f"collection.{key}"] = {
                "expected": value,
                "actual": metadata.get(key),
            }

    stored_chunks = manifest.get("stored_chunks")
    if isinstance(stored_chunks, int) and collection.count() != stored_chunks:
        mismatches["collection.count"] = {
            "expected": stored_chunks,
            "actual": collection.count(),
        }
    if mismatches:
        raise ValueError(f"BGE-M3 판례 DB 설정이 일치하지 않습니다: {mismatches}")


def load_precedent_bge_collection(
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
        raise FileNotFoundError(f"BGE-M3 판례 DB가 없습니다: {resolved}")
    manifest = read_manifest(resolved / "manifest.json")
    chroma_dir = resolved / "chroma"
    client_path = chroma_dir if chroma_dir.is_dir() else resolved
    client = chromadb.PersistentClient(path=str(client_path))
    collection = client.get_collection(manifest.get("collection", DEFAULT_COLLECTION_NAME))
    validate_precedent_bge_store(collection, manifest)
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
        BGE_M3_MODEL_NAME,
        cache_folder=str(model_cache.resolve()),
        device=selected_device,
        local_files_only=True,
    )
    dimension = model.get_sentence_embedding_dimension()
    if dimension != BGE_M3_DIMENSION:
        raise ValueError(
            "BGE-M3 질의 임베딩 차원이 다릅니다: "
            f"{dimension} != {BGE_M3_DIMENSION}"
        )
    return model


class BgeM3PrecedentRetriever:
    def __init__(
        self,
        *,
        db_dir: Path = DEFAULT_DB_DIR,
        model_cache: Path = DEFAULT_MODEL_CACHE,
        device: str = "auto",
        candidate_multiplier: int = DEFAULT_CANDIDATE_MULTIPLIER,
        model_loader: Callable[[Path, str], Any] = load_bge_model,
        collection_loader: Callable[
            [Path], tuple[Any, Any, dict[str, Any]]
        ] = load_precedent_bge_collection,
    ) -> None:
        if device not in {"auto", "cpu", "cuda"}:
            raise ValueError(f"지원하지 않는 device: {device}")
        if candidate_multiplier < 1:
            raise ValueError("candidate_multiplier는 1 이상이어야 합니다.")
        self.db_dir = db_dir
        self.model_cache = model_cache
        self.device = device
        self.candidate_multiplier = candidate_multiplier
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
    ) -> list[PrecedentSearchResult]:
        return self.search_many([query], top_k=top_k)[0]

    def search_many(
        self,
        queries: Sequence[str],
        *,
        top_k: int = DEFAULT_TOP_K,
    ) -> list[list[PrecedentSearchResult]]:
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
            if any(len(row) != BGE_M3_DIMENSION for row in embeddings):
                raise ValueError("BGE-M3 질의 임베딩 차원이 다릅니다.")
            n_results = min(
                max(top_k * self.candidate_multiplier, top_k),
                self._collection.count(),
            )
            result = self._collection.query(
                query_embeddings=embeddings,
                n_results=n_results,
                include=["documents", "metadatas", "distances"],
            )
        return self._parse_results(result, len(normalized), top_k)

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
        top_k: int,
    ) -> list[list[PrecedentSearchResult]]:
        fields = ("ids", "documents", "metadatas", "distances")
        if any(len(result.get(field, [])) != query_count for field in fields):
            raise ValueError("ChromaDB 검색 결과의 질문 수가 일치하지 않습니다.")

        parsed = []
        for index in range(query_count):
            grouped: dict[str, dict[str, Any]] = {}
            values = [result[field][index] for field in fields]
            for chunk_rank, (chunk_id, document, metadata, distance) in enumerate(
                zip(*values, strict=True),
                start=1,
            ):
                metadata = metadata or {}
                precedent_id = (
                    metadata.get("precedent_id")
                    or metadata.get("source_case_id")
                    or BgeM3PrecedentRetriever._precedent_id_from_chunk_id(chunk_id)
                )
                if not precedent_id:
                    continue
                precedent_id = str(precedent_id)
                numeric_distance = float(distance)
                chunk = PrecedentChunkMatch(
                    rank=chunk_rank,
                    chunk_id=chunk_id,
                    distance=numeric_distance,
                    similarity=1.0 - numeric_distance,
                    text=document,
                    chunk_type=metadata.get("chunk_type"),
                    section=metadata.get("section"),
                    section_chunk_index=BgeM3PrecedentRetriever._optional_int(
                        metadata.get("section_chunk_index")
                    ),
                    metadata=dict(metadata),
                )
                group = grouped.setdefault(
                    precedent_id,
                    {
                        "best_distance": numeric_distance,
                        "best_text": document,
                        "best_metadata": dict(metadata),
                        "chunks": [],
                    },
                )
                group["chunks"].append(chunk)
                if numeric_distance < group["best_distance"]:
                    group["best_distance"] = numeric_distance
                    group["best_text"] = document
                    group["best_metadata"] = dict(metadata)

            rows = []
            sorted_groups = sorted(
                grouped.items(),
                key=lambda item: item[1]["best_distance"],
            )
            for rank, (precedent_id, group) in enumerate(
                sorted_groups[:top_k],
                start=1,
            ):
                metadata = group["best_metadata"]
                best_distance = group["best_distance"]
                rows.append(
                    PrecedentSearchResult(
                        rank=rank,
                        precedent_id=precedent_id,
                        distance=best_distance,
                        similarity=1.0 - best_distance,
                        text=group["best_text"],
                        case_no=metadata.get("case_no"),
                        case_no_list=metadata.get("case_no_list"),
                        case_name=metadata.get("case_name"),
                        court_name=metadata.get("court_name"),
                        decision_date=metadata.get("decision_date"),
                        decision_year=BgeM3PrecedentRetriever._optional_int(
                            metadata.get("decision_year")
                        ),
                        case_type=metadata.get("case_type"),
                        judgment_type=metadata.get("judgment_type"),
                        source_path=metadata.get("source_path"),
                        matched_chunks=group["chunks"],
                        metadata=dict(metadata),
                    )
                )
            parsed.append(rows)
        return parsed

    @staticmethod
    def _precedent_id_from_chunk_id(chunk_id: str) -> str | None:
        parts = chunk_id.split(":")
        if len(parts) >= 3 and parts[0] == "precedent":
            return parts[1]
        return None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
