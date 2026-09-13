# create_statute_parent_child_chunks.py
"""
Description: 계층형 법령 JSON에서 항 단위 검색 자식과 조문 단위 반환 부모를
생성하고, 자식-부모 연결 및 텍스트 누락 여부를 검증한다.
Author: ooheunsu
Date: 2026-09-05
Before:
    - 법령 JSON이 조·항·호·목 계층과 노드 연결을 보존한 상태.
After:
    - data/statutes/parent_child_chunks/와 부모-자식 청킹 manifest가 생성.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ml.data_collection.statutes.law_api_common import (
    PROJECT_ROOT,
    read_json,
    write_json,
)
from ml.preprocessing.statutes.create_statute_article_chunks import (
    CHUNK_SCHEMA_VERSION,
    article_body_text,
    article_display_name,
    create_article_chunk,
    heading_titles,
    ordered_nodes,
    paragraph_text_nodes,
    project_path,
    utc_timestamp,
)


DEFAULT_INPUT_DIR = PROJECT_ROOT / "data/statutes/filtered_hierarchical_jsons"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/statutes/parent_child_chunks"
DEFAULT_MANIFEST = (
    PROJECT_ROOT / "data/statutes/manifests/parent_child_chunking_v01.json"
)
CHUNKING_STRATEGY = "paragraph_child_article_parent_v01"


def parent_metadata(document: dict[str, Any], article: dict[str, Any]) -> dict[str, Any]:
    law = document["law"]
    return {
        "law_id": law["law_id"],
        "law_name": law["name"]["ko"],
        "article_label": article["label"],
        "article_title": article.get("title"),
        "heading_path": heading_titles(document, article),
        "effective_date": article.get("effective_date") or law["effective_date"],
        "contains_excluded_image": article["has_excluded_image"],
    }


def child_retrieval_text(
    document: dict[str, Any],
    article: dict[str, Any],
    body_nodes: list[str],
    child_label: str,
) -> str:
    law_name = document["law"]["name"]["ko"]
    headings = heading_titles(document, article)
    lines = [f"법령: {law_name}"]
    if headings:
        lines.append(f"구조: {' > '.join(headings)}")
    lines.append(f"조문: {article_display_name(article)}")
    lines.append(f"검색 단위: {child_label}")
    lines.extend(("", "본문:", *body_nodes))
    return "\n".join(lines).strip()


def create_paragraph_child(
    document: dict[str, Any],
    article: dict[str, Any],
    paragraph: dict[str, Any],
) -> dict[str, Any] | None:
    body_nodes = paragraph_text_nodes(paragraph)
    if not body_nodes:
        return None
    article_intro = article_body_text(article)
    if article_intro:
        body_nodes = [article_intro, *body_nodes]
    paragraph_label = paragraph.get("label") or f"제{paragraph['order']}항"
    return {
        "chunk_id": paragraph["node_id"],
        "source_node_id": paragraph["node_id"],
        "parent_article_id": article["node_id"],
        "chunk_type": "paragraph_child",
        "retrieval_text": child_retrieval_text(
            document,
            article,
            body_nodes,
            f"{article['label']} {paragraph_label}",
        ),
        "metadata": {
            **parent_metadata(document, article),
            "parent_article_id": article["node_id"],
            "paragraph_order": paragraph["order"],
            "paragraph_label": paragraph_label,
        },
    }


def create_article_fallback_child(
    document: dict[str, Any], article: dict[str, Any]
) -> dict[str, Any] | None:
    body = article_body_text(article)
    if not body:
        return None
    return {
        "chunk_id": f"{article['node_id']}:child",
        "source_node_id": article["node_id"],
        "parent_article_id": article["node_id"],
        "chunk_type": "article_child",
        "retrieval_text": child_retrieval_text(
            document,
            article,
            [body],
            article["label"],
        ),
        "metadata": {
            **parent_metadata(document, article),
            "parent_article_id": article["node_id"],
        },
    }


def create_law_parent_child_chunks(
    document: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    parents: list[dict[str, Any]] = []
    child_chunks: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for article in ordered_nodes(document["law"].get("articles")):
        article_children = [
            child
            for paragraph in ordered_nodes(article.get("paragraphs"))
            if (child := create_paragraph_child(document, article, paragraph))
            is not None
        ]
        if not article_children:
            fallback = create_article_fallback_child(document, article)
            if fallback is not None:
                article_children = [fallback]
        if not article_children:
            exclusions.append(
                {
                    "parent_article_id": article["node_id"],
                    "article_label": article["label"],
                    "article_title": article.get("title"),
                    "reason": "검색 가능한 조문·항·호·목 텍스트 없음",
                }
            )
            continue

        parent = create_article_chunk(document, article)
        parent["chunk_type"] = "article_parent"
        parents.append(parent)
        child_chunks.extend(article_children)
    return parents, child_chunks, exclusions


def validate_parent_child_chunks(
    parents: list[dict[str, Any]], children: list[dict[str, Any]]
) -> dict[str, int]:
    parent_ids = [parent["chunk_id"] for parent in parents]
    parent_id_set = set(parent_ids)
    child_ids = [child["chunk_id"] for child in children]
    failures = {
        "duplicate_parent_id_count": len(parent_ids) - len(parent_id_set),
        "duplicate_child_id_count": len(child_ids) - len(set(child_ids)),
        "empty_parent_count": sum(not row["retrieval_text"].strip() for row in parents),
        "empty_child_count": sum(not row["retrieval_text"].strip() for row in children),
        "missing_parent_link_count": sum(
            child["parent_article_id"] not in parent_id_set for child in children
        ),
        "metadata_link_mismatch_count": sum(
            child["metadata"].get("parent_article_id")
            != child["parent_article_id"]
            for child in children
        ),
        "parent_without_child_count": len(
            parent_id_set - {child["parent_article_id"] for child in children}
        ),
    }
    if any(failures.values()):
        raise ValueError(f"부모-자식 청크 무결성 검사 실패: {failures}")
    return failures


def corpus_sha256(rows: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda item: item["chunk_id"]):
        record = {
            "chunk_id": row["chunk_id"],
            "source_node_id": row.get("source_node_id"),
            "chunk_type": row.get("chunk_type"),
            "retrieval_text": row["retrieval_text"],
            "metadata": row.get("metadata", {}),
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


def process_directory(input_dir: Path, output_dir: Path, manifest_path: Path) -> dict:
    input_files = sorted(input_dir.glob("*.json"))
    if not input_files:
        raise FileNotFoundError(f"정규화 법령 JSON이 없습니다: {input_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    all_parents: list[dict[str, Any]] = []
    all_children: list[dict[str, Any]] = []
    all_exclusions: list[dict[str, Any]] = []
    law_results = []
    child_types: Counter[str] = Counter()
    for input_file in input_files:
        document = read_json(input_file)
        law = document["law"]
        parents, chunks, exclusions = create_law_parent_child_chunks(document)
        write_json(
            output_dir / f"{law['law_id']}.json",
            {
                "schema_version": CHUNK_SCHEMA_VERSION,
                "chunking_strategy": CHUNKING_STRATEGY,
                "law_id": law["law_id"],
                "law_name": law["name"]["ko"],
                "parent_documents": parents,
                "chunks": chunks,
            },
        )
        all_parents.extend(parents)
        all_children.extend(chunks)
        child_types.update(row["chunk_type"] for row in chunks)
        all_exclusions.extend(
            {
                "law_id": law["law_id"],
                "law_name": law["name"]["ko"],
                **row,
            }
            for row in exclusions
        )
        law_results.append(
            {
                "law_id": law["law_id"],
                "law_name": law["name"]["ko"],
                "parent_count": len(parents),
                "child_count": len(chunks),
                "excluded_article_count": len(exclusions),
            }
        )

    validation = validate_parent_child_chunks(all_parents, all_children)
    manifest = {
        "schema_version": CHUNK_SCHEMA_VERSION,
        "chunking_strategy": CHUNKING_STRATEGY,
        "generated_at": utc_timestamp(),
        "input_dir": project_path(input_dir),
        "output_dir": project_path(output_dir),
        "law_count": len(input_files),
        "parent_article_count": len(all_parents),
        "child_chunk_count": len(all_children),
        "child_type_counts": dict(sorted(child_types.items())),
        "excluded_article_count": len(all_exclusions),
        "excluded_articles": all_exclusions,
        "parent_corpus_sha256": corpus_sha256(all_parents),
        "child_corpus_sha256": corpus_sha256(all_children),
        "validation": validation,
        "laws": law_results,
    }
    write_json(manifest_path, manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="법령 부모-자식 검색 청크 생성")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = process_directory(
        args.input_dir.resolve(),
        args.output_dir.resolve(),
        args.manifest.resolve(),
    )
    print(
        f"부모-자식 청킹 완료: 부모 {manifest['parent_article_count']}개, "
        f"자식 {manifest['child_chunk_count']}개"
    )
    print(f"검증 manifest: {args.manifest.resolve()}")


if __name__ == "__main__":
    main()
