# create_statute_article_chunks.py
"""
Description: 정규화된 법령 JSON의 조·항·호·목을 조문 단위 검색 텍스트로
결합하고, 입력 한도 초과 조문은 항 경계로 분할해 기준선 청크를 생성한다.
Author: ooheunsu
Date: 2026-08-25
Before:
    - 103개 법령이 조·항·호·목 계층을 보존한 JSON으로 정규화된 상태.

After:
    - data/statutes/chunks/에 법령별 청크 JSON과 manifests/chunking_v01.json이 생성.
"""

import argparse
import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ml.data_collection.statutes.law_api_common import (
    PROJECT_ROOT,
    read_json,
    write_json,
)


DEFAULT_INPUT_DIR = PROJECT_ROOT / "data/statutes/filtered_hierarchical_jsons"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/statutes/chunks"
DEFAULT_MANIFEST = PROJECT_ROOT / "data/statutes/manifests/chunking_v01.json"
CHUNK_SCHEMA_VERSION = "0.2"
CHUNKING_STRATEGY = "article_with_overflow_split_v01"

# 후보 모델 토큰 분석에서 공통 입력 한도를 초과한 조문만 명시적으로 분할한다.
ARTICLE_PARAGRAPH_SPLITS = {
    "law:002118:article:0186": ((1,), (2, 3, 4, 5)),
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalized_text(node: dict) -> str | None:
    text = node.get("text")
    if not isinstance(text, dict):
        return None
    value = text.get("normalized_text")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def project_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def ordered_nodes(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    nodes = [node for node in value if isinstance(node, dict)]
    return sorted(nodes, key=lambda node: node.get("order", 0))


def article_display_name(article: dict) -> str:
    label = article["label"]
    title = article.get("title")
    if isinstance(title, str) and title.strip():
        return f"{label}({title.strip()})"
    return label


def article_body_text(article: dict) -> str | None:
    value = normalized_text(article)
    if not value:
        return None

    heading = article_display_name(article)
    if value.startswith(heading):
        value = value[len(heading) :].strip()
    return value or None


def paragraph_text_nodes(paragraph: dict) -> list[str]:
    texts: list[str] = []
    paragraph_value = normalized_text(paragraph)
    if paragraph_value:
        texts.append(paragraph_value)

    for subparagraph in ordered_nodes(paragraph.get("subparagraphs")):
        subparagraph_value = normalized_text(subparagraph)
        if subparagraph_value:
            texts.append(subparagraph_value)

        for item in ordered_nodes(subparagraph.get("items")):
            item_value = normalized_text(item)
            if item_value:
                texts.append(item_value)
    return texts


def article_text_nodes(
    article: dict,
    paragraph_orders: set[int] | None = None,
    include_article_body: bool = True,
) -> list[str]:
    texts: list[str] = []

    if include_article_body:
        article_value = article_body_text(article)
        if article_value:
            texts.append(article_value)

    for paragraph in ordered_nodes(article.get("paragraphs")):
        if paragraph_orders is not None and paragraph.get("order") not in (
            paragraph_orders
        ):
            continue
        texts.extend(paragraph_text_nodes(paragraph))

    return texts


def article_structure_counts(
    article: dict,
    paragraph_orders: set[int] | None = None,
) -> dict[str, int]:
    paragraphs = ordered_nodes(article.get("paragraphs"))
    if paragraph_orders is not None:
        paragraphs = [
            paragraph
            for paragraph in paragraphs
            if paragraph.get("order") in paragraph_orders
        ]
    subparagraphs = [
        subparagraph
        for paragraph in paragraphs
        for subparagraph in ordered_nodes(paragraph.get("subparagraphs"))
    ]
    items = [
        item
        for subparagraph in subparagraphs
        for item in ordered_nodes(subparagraph.get("items"))
    ]
    return {
        "articles": 1,
        "paragraphs": len(paragraphs),
        "subparagraphs": len(subparagraphs),
        "items": len(items),
    }


def heading_titles(document: dict, article: dict) -> list[str]:
    law = document["law"]
    heading_by_id = {
        heading.get("node_id"): heading
        for heading in ordered_nodes(law.get("headings"))
    }
    titles: list[str] = []
    for heading_id in article.get("heading_path", []):
        heading = heading_by_id.get(heading_id)
        if not heading:
            raise ValueError(
                f"{article.get('node_id')}의 heading_path가 존재하지 않는 "
                f"노드를 참조합니다: {heading_id}"
            )
        title = heading.get("title")
        if isinstance(title, str) and title.strip():
            titles.append(title.strip())
    return titles


def create_article_chunk(
    document: dict,
    article: dict,
    paragraph_orders: set[int] | None = None,
    part_index: int | None = None,
    part_count: int | None = None,
) -> dict:
    law = document["law"]
    law_id = law["law_id"]
    law_name = law["name"]["ko"]
    headings = heading_titles(document, article)
    body_nodes = article_text_nodes(
        article,
        paragraph_orders=paragraph_orders,
        include_article_body=part_index in (None, 1),
    )

    lines = [f"법령: {law_name}"]
    if headings:
        lines.append(f"구조: {' > '.join(headings)}")
    lines.append(f"조문: {article_display_name(article)}")
    lines.append("")
    lines.append("본문:")
    lines.extend(body_nodes)
    retrieval_text = "\n".join(lines).strip()

    for text in body_nodes:
        if text not in retrieval_text:
            raise ValueError(f"청크에서 계층 텍스트가 누락됐습니다: {article['node_id']}")

    is_split = part_index is not None and part_count is not None
    chunk_id = article["node_id"]
    if is_split:
        chunk_id = f"{chunk_id}:part:{part_index:02d}"
    metadata = {
        "law_id": law_id,
        "law_name": law_name,
        "article_label": article["label"],
        "article_title": article.get("title"),
        "heading_path": headings,
        "effective_date": article.get("effective_date")
        or law["effective_date"],
        "contains_excluded_image": article["has_excluded_image"],
    }
    if is_split:
        metadata.update(
            {
                "part_index": part_index,
                "part_count": part_count,
                "paragraph_orders": sorted(paragraph_orders or set()),
            }
        )

    return {
        "chunk_id": chunk_id,
        "source_node_id": article["node_id"],
        "chunk_type": "article_part" if is_split else "article",
        "retrieval_text": retrieval_text,
        "metadata": metadata,
    }


def create_split_article_chunks(
    document: dict,
    article: dict,
    paragraph_groups: tuple[tuple[int, ...], ...],
) -> list[dict]:
    actual_orders = [
        paragraph.get("order")
        for paragraph in ordered_nodes(article.get("paragraphs"))
    ]
    configured_orders = [
        order for group in paragraph_groups for order in group
    ]
    if any(not group for group in paragraph_groups):
        raise ValueError(f"빈 조문 분할 그룹: {article['node_id']}")
    if sorted(configured_orders) != sorted(actual_orders) or len(
        configured_orders
    ) != len(set(configured_orders)):
        raise ValueError(
            f"조문 분할 항 구성이 실제 항과 다릅니다: {article['node_id']}"
        )

    part_count = len(paragraph_groups)
    return [
        create_article_chunk(
            document,
            article,
            paragraph_orders=set(group),
            part_index=index,
            part_count=part_count,
        )
        for index, group in enumerate(paragraph_groups, start=1)
    ]


def create_law_chunks(
    document: dict,
) -> tuple[list[dict], dict[str, int], list[dict]]:
    chunks: list[dict] = []
    counts = Counter()
    excluded_articles: list[dict] = []

    for article in ordered_nodes(document["law"].get("articles")):
        counts.update(article_structure_counts(article))
        if not article_text_nodes(article):
            excluded_articles.append(
                {
                    "source_node_id": article["node_id"],
                    "article_label": article["label"],
                    "article_title": article.get("title"),
                    "reason": "본문·항·호·목이 없는 이동 조문 자리표시자",
                }
            )
            continue
        paragraph_groups = ARTICLE_PARAGRAPH_SPLITS.get(article["node_id"])
        if paragraph_groups:
            chunks.extend(
                create_split_article_chunks(
                    document,
                    article,
                    paragraph_groups,
                )
            )
        else:
            chunks.append(create_article_chunk(document, article))

    return chunks, dict(counts), excluded_articles


def percentile(sorted_values: list[int], ratio: float) -> int:
    if not sorted_values:
        return 0
    index = round((len(sorted_values) - 1) * ratio)
    return sorted_values[index]


def length_summary(values: list[int]) -> dict[str, int | float]:
    sorted_values = sorted(values)
    if not sorted_values:
        return {
            "minimum": 0,
            "p50": 0,
            "p90": 0,
            "p95": 0,
            "p99": 0,
            "maximum": 0,
            "average": 0,
        }
    return {
        "minimum": sorted_values[0],
        "p50": percentile(sorted_values, 0.50),
        "p90": percentile(sorted_values, 0.90),
        "p95": percentile(sorted_values, 0.95),
        "p99": percentile(sorted_values, 0.99),
        "maximum": sorted_values[-1],
        "average": round(sum(sorted_values) / len(sorted_values), 2),
    }


def validate_all_chunks(chunks: list[dict], expected_chunks: int) -> dict:
    chunk_ids = [chunk["chunk_id"] for chunk in chunks]
    empty_chunks = [
        chunk["chunk_id"]
        for chunk in chunks
        if not chunk["retrieval_text"].strip()
    ]
    duplicate_ids = [
        chunk_id for chunk_id, count in Counter(chunk_ids).items() if count > 1
    ]
    text_hashes = [
        hashlib.sha256(chunk["retrieval_text"].encode("utf-8")).hexdigest()
        for chunk in chunks
    ]
    duplicate_text_groups = sum(
        1 for count in Counter(text_hashes).values() if count > 1
    )
    if len(chunks) != expected_chunks:
        raise ValueError(
            f"예상 청크 수와 실제 청크 수가 다릅니다: "
            f"{expected_chunks} != {len(chunks)}"
        )
    if empty_chunks:
        raise ValueError(f"빈 청크가 있습니다: {empty_chunks[:5]}")
    if duplicate_ids:
        raise ValueError(f"중복 chunk_id가 있습니다: {duplicate_ids[:5]}")

    return {
        "empty_chunk_count": len(empty_chunks),
        "duplicate_chunk_id_count": len(duplicate_ids),
        "duplicate_retrieval_text_group_count": duplicate_text_groups,
    }


def build_manifest(
    input_dir: Path,
    output_dir: Path,
    law_results: list[dict],
    all_chunks: list[dict],
    hierarchy_counts: Counter,
    excluded_articles: list[dict],
) -> dict:
    chunks_per_article = Counter(
        chunk["source_node_id"] for chunk in all_chunks
    )
    split_articles = {
        source_node_id: count
        for source_node_id, count in chunks_per_article.items()
        if count > 1
    }
    split_extra_chunk_count = sum(
        count - 1 for count in split_articles.values()
    )
    expected_chunks = (
        hierarchy_counts["articles"]
        - len(excluded_articles)
        + split_extra_chunk_count
    )
    validation = validate_all_chunks(all_chunks, expected_chunks)
    lengths = [len(chunk["retrieval_text"]) for chunk in all_chunks]
    longest_articles = sorted(
        (
            {
                "chunk_id": chunk["chunk_id"],
                "law_name": chunk["metadata"]["law_name"],
                "article_label": chunk["metadata"]["article_label"],
                "article_title": chunk["metadata"]["article_title"],
                "character_count": len(chunk["retrieval_text"]),
                "hierarchy_counts": chunk["hierarchy_counts"],
            }
            for chunk in all_chunks
        ),
        key=lambda item: item["character_count"],
        reverse=True,
    )[:24]

    return {
        "schema_version": CHUNK_SCHEMA_VERSION,
        "chunking_strategy": CHUNKING_STRATEGY,
        "input_dir": project_path(input_dir),
        "output_dir": project_path(output_dir),
        "generated_at": utc_timestamp(),
        "law_count": len(law_results),
        "source_article_count": hierarchy_counts["articles"],
        "chunk_count": len(all_chunks),
        "excluded_article_count": len(excluded_articles),
        "excluded_articles": excluded_articles,
        "split_article_count": len(split_articles),
        "split_extra_chunk_count": split_extra_chunk_count,
        "split_articles": split_articles,
        "hierarchy_counts": dict(hierarchy_counts),
        "validation": validation,
        "character_count": length_summary(lengths),
        "token_analysis": {
            "status": "pending",
            "reason": "임베딩 모델과 토크나이저가 아직 확정되지 않음",
            "thresholds": [512, 1024],
        },
        "longest_articles": longest_articles,
        "laws": law_results,
    }


def process_directory(input_dir: Path, output_dir: Path, manifest_path: Path) -> dict:
    input_files = sorted(input_dir.glob("*.json"))
    if not input_files:
        raise FileNotFoundError(f"정규화 법령 JSON이 없습니다: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    law_results: list[dict] = []
    all_chunks: list[dict] = []
    hierarchy_counts = Counter()
    all_excluded_articles: list[dict] = []

    for input_file in input_files:
        document = read_json(input_file)
        law = document["law"]
        chunks, law_counts, excluded_articles = create_law_chunks(document)
        hierarchy_counts.update(law_counts)
        for excluded_article in excluded_articles:
            all_excluded_articles.append(
                {
                    "law_id": law["law_id"],
                    "law_name": law["name"]["ko"],
                    **excluded_article,
                }
            )

        output_file = output_dir / f"{law['law_id']}.json"
        write_json(
            output_file,
            {
                "schema_version": CHUNK_SCHEMA_VERSION,
                "chunking_strategy": CHUNKING_STRATEGY,
                "law_id": law["law_id"],
                "law_name": law["name"]["ko"],
                "chunks": chunks,
            },
        )
        law_results.append(
            {
                "law_id": law["law_id"],
                "law_name": law["name"]["ko"],
                "source_article_count": law_counts["articles"],
                "chunk_count": len(chunks),
                "excluded_article_count": len(excluded_articles),
                "output_file": project_path(output_file),
            }
        )
        article_by_id = {
            article["node_id"]: article
            for article in ordered_nodes(law.get("articles"))
        }
        for chunk in chunks:
            article = article_by_id[chunk["source_node_id"]]
            paragraph_orders = chunk["metadata"].get("paragraph_orders")
            all_chunks.append(
                {
                    **chunk,
                    "hierarchy_counts": article_structure_counts(
                        article,
                        set(paragraph_orders) if paragraph_orders else None,
                    ),
                }
            )

    manifest = build_manifest(
        input_dir,
        output_dir,
        law_results,
        all_chunks,
        hierarchy_counts,
        all_excluded_articles,
    )
    write_json(manifest_path, manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="법령 조문 단위 청크 생성")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = process_directory(args.input_dir, args.output_dir, args.manifest)
    print(
        f"청킹 완료: 법령 {manifest['law_count']}개, "
        f"청크 {manifest['chunk_count']}개"
    )
    print(f"검증 manifest: {args.manifest.resolve()}")


if __name__ == "__main__":
    main()
