# run_statute_retrieval_pool.py
"""
Description: 법령 검색 평가 질문을 후보 임베딩 모델별 ChromaDB에서
조회하고 모델 출처를 숨긴 Top-K 합집합 검수표를 생성한다.
Author: ooheunsu
Date: 2026-08-31
Before:
    - 모델별 법령 ChromaDB와 draft 이상의 검색 평가 질문이 존재.

After:
    - 모델별 원본 순위와 블라인드 관련도 검수용 JSONL·CSV가 생성.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ml.data_collection.statutes.law_api_common import PROJECT_ROOT
from ml.embedding.build_bge_m3_statute_chroma import (
    COLLECTION_NAME as BGE_COLLECTION_NAME,
)
from ml.embedding.build_bge_m3_statute_chroma import MODEL_SPEC as BGE_SPEC
from ml.embedding.build_kure_statute_chroma import (
    COLLECTION_NAME as KURE_COLLECTION_NAME,
)
from ml.embedding.build_kure_statute_chroma import MODEL_SPEC as KURE_SPEC
from ml.embedding.build_openai_statute_chroma import (
    COLLECTION_NAME as OPENAI_COLLECTION_NAME,
)
from ml.embedding.build_openai_statute_chroma import (
    MODEL_DIMENSION as OPENAI_DIMENSION,
)
from ml.embedding.build_openai_statute_chroma import MODEL_NAME as OPENAI_MODEL
from ml.embedding.build_openai_statute_chroma import load_openai_client
from ml.embedding.build_openai_statute_chroma import response_embeddings
from ml.embedding.statute_chroma_builder import disable_unavailable_hf_transfer


DEFAULT_DATASET = (
    PROJECT_ROOT / "data/statutes/evaluation/datasets/pilot_v01.jsonl"
)
DEFAULT_MODEL_CACHE = PROJECT_ROOT / "data/statutes/models"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data/statutes/evaluation/runs"
DEFAULT_REVIEW_ROOT = PROJECT_ROOT / "data/statutes/evaluation/reviews"
DEFAULT_TOP_K = 10
SUPPORTED_MODELS = ("kure_v1", "bge_m3", "openai_3_large")


@dataclass(frozen=True)
class RetrievalModel:
    key: str
    display_name: str
    provider: str
    model_name: str
    revision: str
    dimension: int
    db_dir: Path
    collection_name: str


MODEL_CONFIGS = {
    "kure_v1": RetrievalModel(
        key="kure_v1",
        display_name="KURE-v1",
        provider="sentence_transformers",
        model_name=KURE_SPEC.model_name,
        revision=KURE_SPEC.revision,
        dimension=KURE_SPEC.dimension,
        db_dir=KURE_SPEC.default_db_dir,
        collection_name=KURE_COLLECTION_NAME,
    ),
    "bge_m3": RetrievalModel(
        key="bge_m3",
        display_name="BGE-M3 dense",
        provider="sentence_transformers",
        model_name=BGE_SPEC.model_name,
        revision=BGE_SPEC.revision,
        dimension=BGE_SPEC.dimension,
        db_dir=BGE_SPEC.default_db_dir,
        collection_name=BGE_COLLECTION_NAME,
    ),
    "openai_3_large": RetrievalModel(
        key="openai_3_large",
        display_name="text-embedding-3-large",
        provider="openai",
        model_name=OPENAI_MODEL,
        revision=OPENAI_MODEL,
        dimension=OPENAI_DIMENSION,
        db_dir=(
            PROJECT_ROOT
            / "data/statutes/vectorstores/text_embedding_3_large_3072"
        ),
        collection_name=OPENAI_COLLECTION_NAME,
    ),
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        for row in rows
    )
    path.write_text(content + "\n", encoding="utf-8")


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = []
    seen_ids = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"JSONL {line_number}행이 올바르지 않습니다.") from error
        query_id = case.get("query_id")
        question = case.get("question")
        if not isinstance(query_id, str) or not isinstance(question, str):
            raise ValueError(f"JSONL {line_number}행에 query_id/question이 없습니다.")
        if query_id in seen_ids:
            raise ValueError(f"중복 query_id: {query_id}")
        seen_ids.add(query_id)
        cases.append(case)
    if not cases:
        raise ValueError("평가 문항이 없습니다.")
    return cases


def selected_configs(model_keys: list[str]) -> list[RetrievalModel]:
    invalid = sorted(set(model_keys) - set(MODEL_CONFIGS))
    if invalid:
        raise ValueError(f"지원하지 않는 모델: {', '.join(invalid)}")
    return [MODEL_CONFIGS[key] for key in model_keys]


def open_collection(config: RetrievalModel) -> Any:
    try:
        import chromadb
    except ImportError as error:
        raise RuntimeError("chromadb가 설치되지 않았습니다.") from error
    if not config.db_dir.exists():
        raise FileNotFoundError(f"{config.display_name} DB가 없습니다: {config.db_dir}")
    client = chromadb.PersistentClient(path=str(config.db_dir))
    collection = client.get_collection(config.collection_name)
    if collection.count() == 0:
        raise ValueError(f"{config.display_name} 컬렉션이 비어 있습니다.")
    return collection


def validate_collections(
    configs: list[RetrievalModel],
    cases: list[dict[str, Any]],
    collection_loader: Callable[[RetrievalModel], Any] = open_collection,
) -> dict[str, Any]:
    source_hashes = {
        case["dataset_snapshot"]["source_sha256"] for case in cases
    }
    if len(source_hashes) != 1:
        raise ValueError("평가 문항의 법령 source hash가 서로 다릅니다.")
    expected_hash = next(iter(source_hashes))
    collections = {}
    for config in configs:
        collection = collection_loader(config)
        metadata = collection.metadata or {}
        checks = {
            "model_name": config.model_name,
            "model_revision": config.revision,
            "embedding_dimension": config.dimension,
            "source_sha256": expected_hash,
        }
        for key, expected in checks.items():
            if metadata.get(key) != expected:
                raise ValueError(
                    f"{config.display_name} 컬렉션 {key} 불일치: "
                    f"{metadata.get(key)!r} != {expected!r}"
                )
        collections[config.key] = collection
    return collections


def load_sentence_model(config: RetrievalModel, cache_dir: Path, device: str) -> Any:
    disable_unavailable_hf_transfer()
    try:
        import torch
        from sentence_transformers import SentenceTransformer
    except ImportError as error:
        raise RuntimeError(
            "로컬 질의 임베딩 패키지가 없습니다. "
            "pip install -r requirements-embedding.txt 를 실행하세요."
        ) from error

    selected_device = device
    if selected_device == "auto":
        selected_device = "cuda" if torch.cuda.is_available() else "cpu"
    return SentenceTransformer(
        config.model_name,
        revision=config.revision,
        cache_folder=str(cache_dir),
        device=selected_device,
    )


def sentence_embeddings(
    config: RetrievalModel,
    questions: list[str],
    cache_dir: Path,
    device: str,
) -> list[list[float]]:
    model = load_sentence_model(config, cache_dir, device)
    encoded = model.encode(
        questions,
        batch_size=min(32, len(questions)),
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    rows = encoded.tolist()
    if any(len(row) != config.dimension for row in rows):
        raise ValueError(f"{config.display_name} 질의 임베딩 차원이 다릅니다.")
    return rows


def openai_embeddings(questions: list[str]) -> tuple[list[list[float]], int]:
    client = load_openai_client()
    response = client.embeddings.create(
        model=OPENAI_MODEL,
        input=questions,
        dimensions=OPENAI_DIMENSION,
        encoding_format="float",
    )
    token_count = getattr(getattr(response, "usage", None), "prompt_tokens", 0)
    return response_embeddings(response, len(questions)), int(token_count or 0)


def query_collection(
    collection: Any,
    embeddings: list[list[float]],
    query_ids: list[str],
    top_k: int,
) -> dict[str, list[dict[str, Any]]]:
    result = collection.query(
        query_embeddings=embeddings,
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    rankings = {}
    for index, query_id in enumerate(query_ids):
        rows = []
        ids = result["ids"][index]
        documents = result["documents"][index]
        metadatas = result["metadatas"][index]
        distances = result["distances"][index]
        for rank, (chunk_id, document, metadata, distance) in enumerate(
            zip(ids, documents, metadatas, distances, strict=True),
            start=1,
        ):
            rows.append(
                {
                    "rank": rank,
                    "chunk_id": chunk_id,
                    "distance": float(distance),
                    "document": document,
                    "metadata": metadata or {},
                }
            )
        rankings[query_id] = rows
    return rankings


def blind_sort_key(query_id: str, chunk_id: str) -> str:
    return hashlib.sha256(f"{query_id}\0{chunk_id}".encode()).hexdigest()


def build_blind_pool(
    cases: list[dict[str, Any]],
    rankings: dict[str, dict[str, list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    blind_rows = []
    for case in cases:
        query_id = case["query_id"]
        pooled = {}
        for model_rankings in rankings.values():
            for candidate in model_rankings[query_id]:
                pooled.setdefault(candidate["chunk_id"], candidate)
        ordered = sorted(
            pooled.values(),
            key=lambda row: blind_sort_key(query_id, row["chunk_id"]),
        )
        candidates = []
        for index, candidate in enumerate(ordered, start=1):
            metadata = candidate["metadata"]
            candidates.append(
                {
                    "candidate_id": f"C{index:02d}",
                    "chunk_id": candidate["chunk_id"],
                    "law_name": metadata.get("law_name", ""),
                    "article_label": metadata.get("article_label", ""),
                    "article_title": metadata.get("article_title", ""),
                    "retrieval_text": candidate["document"],
                    "relevance": None,
                    "reason": "",
                }
            )
        blind_rows.append(
            {
                "query_id": query_id,
                "question": case["question"],
                "purpose": case["purpose"],
                "category": case["category"],
                "critical": case["critical"],
                "candidates": candidates,
            }
        )
    return blind_rows


def write_review_csv(path: Path, blind_rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "query_id",
        "question",
        "purpose",
        "category",
        "critical",
        "candidate_id",
        "chunk_id",
        "law_name",
        "article_label",
        "article_title",
        "retrieval_text",
        "relevance",
        "reason",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for case in blind_rows:
            shared = {key: case[key] for key in fields[:5]}
            for candidate in case["candidates"]:
                writer.writerow({**shared, **candidate})


def package_versions() -> dict[str, str | None]:
    versions = {}
    for package in (
        "chromadb",
        "sentence-transformers",
        "transformers",
        "torch",
        "huggingface-hub",
        "hf-xet",
        "openai",
    ):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="법령 검색 모델별 Top-K와 블라인드 후보 검수표를 생성합니다."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--review-root", type=Path, default=DEFAULT_REVIEW_ROOT)
    parser.add_argument("--model-cache", type=Path, default=DEFAULT_MODEL_CACHE)
    parser.add_argument("--models", nargs="+", default=list(SUPPORTED_MODELS))
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--run-id", default=default_run_id())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.top_k < 1:
        raise ValueError("top-k는 1 이상이어야 합니다.")
    dataset = args.dataset.resolve()
    configs = selected_configs(args.models)
    cases = load_cases(dataset)
    collections = validate_collections(configs, cases)
    questions = [case["question"] for case in cases]
    query_ids = [case["query_id"] for case in cases]

    rankings = {}
    openai_tokens = 0
    for config in configs:
        print(f"질의 임베딩 및 검색: {config.display_name}")
        if config.provider == "openai":
            embeddings, openai_tokens = openai_embeddings(questions)
        else:
            embeddings = sentence_embeddings(
                config,
                questions,
                args.model_cache.resolve(),
                args.device,
            )
        rankings[config.key] = query_collection(
            collections[config.key],
            embeddings,
            query_ids,
            args.top_k,
        )

    run_dir = args.output_root.resolve() / args.run_id
    review_root = args.review_root.resolve()
    blind_rows = build_blind_pool(cases, rankings)
    write_json(run_dir / "rankings.json", rankings)
    write_json(
        run_dir / "run_manifest.json",
        {
            "run_id": args.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dataset": str(dataset.relative_to(PROJECT_ROOT)),
            "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
            "query_count": len(cases),
            "top_k": args.top_k,
            "models": [config.__dict__ | {"db_dir": str(config.db_dir)} for config in configs],
            "openai_input_tokens": openai_tokens,
            "package_versions": package_versions(),
        },
    )
    jsonl_path = review_root / f"pilot_v01_{args.run_id}_blind.jsonl"
    csv_path = review_root / f"pilot_v01_{args.run_id}_blind.csv"
    write_jsonl(jsonl_path, blind_rows)
    write_review_csv(csv_path, blind_rows)
    print(f"블라인드 후보 JSONL: {jsonl_path}")
    print(f"블라인드 검수 CSV: {csv_path}")
    print(f"모델별 원본 순위: {run_dir / 'rankings.json'}")


if __name__ == "__main__":
    main()
