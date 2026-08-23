# normalize_statute_xmls.py
"""
Description: 국가법령정보 공동활용 API로 수집한 XML 원본을
조·항·호·목 계층을 보존한 JSON으로 정규화하고 데이터 무결성을 검증한다.
Author: ooheunsu
Date: 2026-08-17
Before:
    - 선정된 법령의 XML 원본이 있지만 DB 구축용 계층형 구조로 정리되지 않은 상태.

After:
    - data/statutes/filtered_hierarchical_jsons/에 법령별 JSON이, data/statutes/manifests/normalization_v01.json에 검증 결과가 생성.
"""

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from ml.data_collection.statutes.law_api_common import (
    PROJECT_ROOT,
    read_json,
    write_json,
)


DEFAULT_SELECTION_FILE = (
    PROJECT_ROOT / "data/statutes/metadata/statute_inclusion_list_v01.json"
)
DEFAULT_XML_DIR = PROJECT_ROOT / "data/statutes/raw_xmls/details"
DEFAULT_JSON_DIR = PROJECT_ROOT / "data/statutes/raw_jsons/details"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/statutes/filtered_hierarchical_jsons"
DEFAULT_MANIFEST = PROJECT_ROOT / "data/statutes/manifests/normalization_v01.json"

STRUCTURE_TAGS = {
    "조문단위": "article_units",
    "항": "paragraphs",
    "호": "subparagraphs",
    "목": "items",
    "부칙단위": "supplementary_provisions",
    "별표단위": "appendices",
}
TEXT_TAGS = ("조문내용", "항내용", "호내용", "목내용")
HEADING_PATTERNS = (
    ("part", re.compile(r"^제.+편")),
    ("chapter", re.compile(r"^제.+장")),
    ("section", re.compile(r"^제.+절")),
    ("subsection", re.compile(r"^제.+관")),
)
IMG_BLOCK_PATTERN = re.compile(r"<img\b[^>]*>.*?</img>", re.IGNORECASE | re.DOTALL)
IMG_TAG_PATTERN = re.compile(r"</?img\b[^>]*>", re.IGNORECASE | re.DOTALL)
NORMALIZED_STRUCTURE_KEYS = {
    "article_units",
    "headings",
    "articles",
    "paragraphs",
    "subparagraphs",
    "items",
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def children(parent: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in list(parent) if local_name(child.tag) == name]


def child(parent: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return next(iter(children(parent, name)), None)


def element_text(element: ElementTree.Element | None) -> str | None:
    if element is None:
        return None
    return "".join(element.itertext())


def child_text(parent: ElementTree.Element, name: str) -> str | None:
    return element_text(child(parent, name))


def nullable(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = IMG_BLOCK_PATTERN.sub("", normalized)
    normalized = IMG_TAG_PATTERN.sub("", normalized)
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r" *\n *", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    return normalized or None


def has_excluded_image(value: str | None) -> bool:
    return bool(value and IMG_BLOCK_PATTERN.search(value))


def text_fields(value: str | None) -> dict:
    return {
        "raw_text": value,
        "normalized_text": normalize_text(value),
    }


def coded_value(element: ElementTree.Element | None, code_attribute: str) -> dict:
    if element is None:
        return {"code": None, "name": None}
    return {
        "code": nullable(element.attrib.get(code_attribute)),
        "name": nullable(element_text(element)),
    }


def node_id(law_id: str, kind: str, *orders: int) -> str:
    suffix = ":".join(f"{order:04d}" for order in orders)
    return f"law:{law_id}:{kind}:{suffix}"


def source_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def heading_level(text: str) -> str:
    for level, pattern in HEADING_PATTERNS:
        if pattern.match(text):
            return level
    return "unknown"


def update_heading_path(path: list[dict], heading: dict) -> list[dict]:
    levels = {"part": 0, "chapter": 1, "section": 2, "subsection": 3}
    level = heading["level"]
    if level == "unknown":
        return [*path, heading]
    rank = levels[level]
    return [item for item in path if levels.get(item["level"], -1) < rank] + [heading]


def normalize_item(
    element: ElementTree.Element,
    law_id: str,
    article_order: int,
    paragraph_order: int,
    subparagraph_order: int,
    item_order: int,
    parent_node_id: str,
) -> dict:
    raw_text = child_text(element, "목내용")
    return {
        "node_id": node_id(
            law_id,
            "item",
            article_order,
            paragraph_order,
            subparagraph_order,
            item_order,
        ),
        "parent_node_id": parent_node_id,
        "order": item_order,
        "label": nullable(child_text(element, "목번호")),
        "text": text_fields(raw_text),
    }


def normalize_subparagraph(
    element: ElementTree.Element,
    law_id: str,
    article_order: int,
    paragraph_order: int,
    subparagraph_order: int,
    parent_node_id: str,
) -> dict:
    current_node_id = node_id(
        law_id,
        "subparagraph",
        article_order,
        paragraph_order,
        subparagraph_order,
    )
    return {
        "node_id": current_node_id,
        "parent_node_id": parent_node_id,
        "order": subparagraph_order,
        "label": nullable(child_text(element, "호번호")),
        "text": text_fields(child_text(element, "호내용")),
        "items": [
            normalize_item(
                item,
                law_id,
                article_order,
                paragraph_order,
                subparagraph_order,
                item_order,
                current_node_id,
            )
            for item_order, item in enumerate(children(element, "목"), start=1)
        ],
    }


def normalize_paragraph(
    element: ElementTree.Element,
    law_id: str,
    article_order: int,
    paragraph_order: int,
    parent_node_id: str,
) -> dict:
    current_node_id = node_id(
        law_id,
        "paragraph",
        article_order,
        paragraph_order,
    )
    raw_text = child_text(element, "항내용")
    label = nullable(child_text(element, "항번호"))
    return {
        "node_id": current_node_id,
        "parent_node_id": parent_node_id,
        "order": paragraph_order,
        "label": label,
        "container_only": label is None and normalize_text(raw_text) is None,
        "text": text_fields(raw_text),
        "subparagraphs": [
            normalize_subparagraph(
                subparagraph,
                law_id,
                article_order,
                paragraph_order,
                subparagraph_order,
                current_node_id,
            )
            for subparagraph_order, subparagraph in enumerate(
                children(element, "호"), start=1
            )
        ],
    }


def article_label(article_no: str, branch_no: str | None) -> str:
    return f"제{article_no}조" + (f"의{branch_no}" if branch_no else "")


def normalize_article(
    element: ElementTree.Element,
    law_id: str,
    article_order: int,
    heading_path: list[dict],
) -> dict:
    article_no = nullable(child_text(element, "조문번호"))
    if article_no is None:
        raise ValueError(f"조문번호가 없습니다: {law_id} 조문 순서 {article_order}")
    branch_no = nullable(child_text(element, "조문가지번호"))
    current_node_id = node_id(law_id, "article", article_order)
    return {
        "node_id": current_node_id,
        "parent_node_id": f"law:{law_id}",
        "source_article_key": nullable(element.attrib.get("조문키")),
        "order": article_order,
        "article_no": article_no,
        "branch_no": branch_no,
        "label": article_label(article_no, branch_no),
        "title": nullable(child_text(element, "조문제목")),
        "has_excluded_image": has_excluded_image(element_text(element)),
        "text": text_fields(child_text(element, "조문내용")),
        "effective_date": nullable(child_text(element, "조문시행일자")),
        "amendment_type": nullable(child_text(element, "조문제개정유형")),
        "reference_note": nullable(child_text(element, "조문참고자료")),
        "heading_path": [heading["node_id"] for heading in heading_path],
        "paragraphs": [
            normalize_paragraph(
                paragraph,
                law_id,
                article_order,
                paragraph_order,
                current_node_id,
            )
            for paragraph_order, paragraph in enumerate(
                children(element, "항"), start=1
            )
        ],
        "source_status": {
            "changed": nullable(child_text(element, "조문변경여부")),
            "moved_from": nullable(child_text(element, "조문이동이전")),
            "moved_to": nullable(child_text(element, "조문이동이후")),
        },
    }


def normalize_heading(
    element: ElementTree.Element,
    law_id: str,
    heading_order: int,
) -> dict:
    raw_text = normalize_text(child_text(element, "조문내용")) or ""
    label_match = re.match(r"^(제.+?(?:편|장|절|관))", raw_text)
    return {
        "node_id": node_id(law_id, "heading", heading_order),
        "parent_node_id": f"law:{law_id}",
        "order": heading_order,
        "level": heading_level(raw_text),
        "label": label_match.group(1) if label_match else None,
        "title": raw_text,
    }


def normalize_xml(xml_path: Path, collected_at: str | None = None) -> dict:
    xml_bytes = xml_path.read_bytes()
    root = ElementTree.fromstring(xml_bytes)
    if local_name(root.tag) != "법령":
        raise ValueError(f"XML 최상위 요소가 법령이 아닙니다: {xml_path}")
    basic = child(root, "기본정보")
    if basic is None:
        raise ValueError(f"기본정보가 없습니다: {xml_path}")

    law_id = nullable(child_text(basic, "법령ID"))
    law_name = nullable(child_text(basic, "법령명_한글"))
    effective_date = nullable(child_text(basic, "시행일자"))
    if law_id is None or law_name is None or effective_date is None:
        raise ValueError(f"법령 식별 필드가 없습니다: {xml_path}")

    headings = []
    articles = []
    current_heading_path: list[dict] = []
    article_container = child(root, "조문")
    units = children(article_container, "조문단위") if article_container is not None else []
    for unit in units:
        kind = nullable(child_text(unit, "조문여부"))
        if kind == "전문":
            heading = normalize_heading(unit, law_id, len(headings) + 1)
            headings.append(heading)
            current_heading_path = update_heading_path(current_heading_path, heading)
        elif kind == "조문":
            articles.append(
                normalize_article(unit, law_id, len(articles) + 1, current_heading_path)
            )
        else:
            raise ValueError(f"알 수 없는 조문여부 값입니다: {law_id}/{kind}")

    ministries = [
        coded_value(node, "소관부처코드") for node in children(basic, "소관부처")
    ]
    law_type_element = child(basic, "법종구분")
    return {
        "schema_version": "0.1",
        "source": {
            "provider": "국가법령정보 공동활용 API",
            "format": "XML",
            "raw_file": source_path(xml_path),
            "raw_sha256": hashlib.sha256(xml_bytes).hexdigest(),
            "collected_at": collected_at,
        },
        "law": {
            "node_id": f"law:{law_id}",
            "law_id": law_id,
            "source_law_key": nullable(root.attrib.get("법령키")),
            "name": {
                "ko": law_name,
                "abbreviation": nullable(child_text(basic, "법령명약칭")),
                "previous": nullable(child_text(basic, "이전법령명")),
                "hanja": nullable(child_text(basic, "법령명_한자")),
                "en": nullable(child_text(basic, "법령명_영어")),
            },
            "law_type": coded_value(law_type_element, "법종구분코드"),
            "ministries": ministries,
            "promulgation_number": nullable(child_text(basic, "공포번호")),
            "promulgation_date": nullable(child_text(basic, "공포일자")),
            "effective_date": effective_date,
            "amendment_type": nullable(child_text(basic, "제개정구분")),
            "language": nullable(child_text(basic, "언어")),
            "headings": headings,
            "articles": articles,
        },
    }


def recursive_objects(value: object):
    if isinstance(value, dict):
        yield value
        for child_value in value.values():
            yield from recursive_objects(child_value)
    elif isinstance(value, list):
        for item in value:
            yield from recursive_objects(item)


def source_counts(root: ElementTree.Element) -> Counter:
    counts = Counter()
    for element in root.iter():
        target = STRUCTURE_TAGS.get(local_name(element.tag))
        if target:
            counts[target] += 1
    counts["headings"] = sum(
        1
        for element in root.iter()
        if local_name(element.tag) == "조문단위"
        and nullable(child_text(element, "조문여부")) == "전문"
    )
    counts["articles"] = counts["article_units"] - counts["headings"]
    return counts


def normalized_counts(document: dict) -> Counter:
    articles = document["law"]["articles"]
    counts = Counter(
        headings=len(document["law"]["headings"]),
        articles=len(articles),
        article_units=len(document["law"]["headings"]) + len(articles),
    )
    for article in articles:
        counts["paragraphs"] += len(article["paragraphs"])
        for paragraph in article["paragraphs"]:
            counts["subparagraphs"] += len(paragraph["subparagraphs"])
            counts["items"] += sum(
                len(subparagraph["items"])
                for subparagraph in paragraph["subparagraphs"]
            )
    return counts


def validate_node_links(document: dict) -> None:
    nodes = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if "node_id" in value:
                nodes.append(value)
            for child_value in value.values():
                visit(child_value)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(document)
    node_ids = [node["node_id"] for node in nodes]
    if len(node_ids) != len(set(node_ids)):
        duplicates = [
            node_id for node_id, count in Counter(node_ids).items() if count > 1
        ]
        raise ValueError(f"중복 node_id가 있습니다: {duplicates[:5]}")

    known_ids = set(node_ids)
    for node in nodes:
        parent_id = node.get("parent_node_id")
        if parent_id is not None and parent_id not in known_ids:
            raise ValueError(
                f"존재하지 않는 parent_node_id입니다: {node['node_id']} -> {parent_id}"
            )
    for article in document["law"]["articles"]:
        missing_headings = set(article["heading_path"]) - known_ids
        if missing_headings:
            raise ValueError(
                f"존재하지 않는 heading_path입니다: {article['node_id']} -> "
                f"{sorted(missing_headings)}"
            )


def source_text_counter(root: ElementTree.Element) -> Counter:
    def comparable_text(value: str | None) -> str | None:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n") if value else None
        return re.sub(r"\s+", "", normalized) if normalized else None

    return Counter(
        (local_name(element.tag), comparable_text(element_text(element)))
        for element in root.iter()
        if local_name(element.tag) in TEXT_TAGS
    )


def json_text_counter(path: Path) -> Counter:
    def strings(value: object):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)

    def comparable_text(value: str) -> str | None:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n")
        return re.sub(r"\s+", "", normalized) if normalized else None

    payload = read_json(path)
    values = Counter()
    for obj in recursive_objects(payload.get("법령", {})):
        for tag in TEXT_TAGS:
            if tag in obj:
                segments = list(strings(obj[tag]))
                if segments:
                    values[(tag, comparable_text("\n".join(segments)))] += 1
    return values


def json_effective_date(path: Path) -> str | None:
    payload = read_json(path)
    law = payload.get("법령")
    if not isinstance(law, dict):
        return None
    basic = law.get("기본정보")
    if not isinstance(basic, dict):
        return None
    return nullable(str(basic.get("시행일자", "")))


def validate_document(
    document: dict,
    xml_path: Path,
    json_path: Path | None,
    expected_law: dict,
) -> dict:
    root = ElementTree.parse(xml_path).getroot()
    source = source_counts(root)
    normalized = normalized_counts(document)
    source_normalized = Counter(
        {key: source[key] for key in NORMALIZED_STRUCTURE_KEYS}
    )
    if source_normalized != normalized:
        raise ValueError(
            f"구조 개수가 변환 전후 다릅니다: "
            f"{dict(source_normalized)} != {dict(normalized)}"
        )
    validate_node_links(document)

    law = document["law"]
    if law["law_id"] != str(expected_law["law_id"]):
        raise ValueError("선정 목록과 정규화 법령 ID가 다릅니다.")
    if law["name"]["ko"] != str(expected_law["law_name"]):
        raise ValueError("선정 목록과 정규화 법령명이 다릅니다.")

    json_match = None
    json_comparison = "not_available"
    json_date = None
    if json_path is not None and json_path.exists():
        xml_texts = source_text_counter(root)
        json_texts = json_text_counter(json_path)
        json_match = xml_texts == json_texts
        json_date = json_effective_date(json_path)
        if json_match:
            json_comparison = "matched"
        elif json_date != law["effective_date"]:
            json_comparison = "version_mismatch"
        else:
            missing = sum((xml_texts - json_texts).values())
            extra = sum((json_texts - xml_texts).values())
            raise ValueError(
                f"XML과 JSON의 조문 텍스트가 다릅니다: 누락 {missing}, 추가 {extra}"
            )

    return {
        "structure_counts": dict(source),
        "excluded_structure_counts": {
            "supplementary_provisions": source["supplementary_provisions"],
            "appendices": source["appendices"],
        },
        "json_texts_match": json_match,
        "json_comparison": json_comparison,
        "json_effective_date": json_date,
        "selected_effective_date": expected_law.get("effective_date"),
        "effective_date_changed": (
            str(expected_law.get("effective_date", "")) != law["effective_date"]
        ),
    }


def load_collection_times(path: Path) -> dict[str, str | None]:
    if not path.exists():
        return {}
    payload = read_json(path)
    return {
        str(result.get("law_id")): result.get("collected_at")
        for result in payload.get("results", [])
        if isinstance(result, dict)
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="법령 XML을 계층형 JSON으로 정규화하고 무결성을 검사합니다."
    )
    parser.add_argument("--selection-file", type=Path, default=DEFAULT_SELECTION_FILE)
    parser.add_argument("--xml-dir", type=Path, default=DEFAULT_XML_DIR)
    parser.add_argument("--json-dir", type=Path, default=DEFAULT_JSON_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    selection = read_json(args.selection_file.resolve())
    laws = selection.get("laws")
    if not isinstance(laws, list) or selection.get("total") != len(laws):
        raise ValueError("선정 목록의 laws 또는 total이 올바르지 않습니다.")
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit은 1 이상이어야 합니다.")
        laws = laws[: args.limit]

    collection_times = load_collection_times(
        PROJECT_ROOT / "data/statutes/manifests/xml_collection_v01.json"
    )
    output_dir = args.output_dir.resolve()
    results = []
    for index, law in enumerate(laws, start=1):
        law_id = str(law["law_id"])
        law_name = str(law["law_name"])
        xml_path = args.xml_dir.resolve() / f"{law_id}.xml"
        json_path = args.json_dir.resolve() / f"{law_id}.json"
        output_path = output_dir / f"{law_id}.json"
        print(f"[{index}/{len(laws)}] {law_name} ({law_id})")
        try:
            document = normalize_xml(xml_path, collection_times.get(law_id))
            validation = validate_document(document, xml_path, json_path, law)
            write_json(output_path, document)
            results.append(
                {
                    "law_id": law_id,
                    "law_name": law_name,
                    "status": "normalized",
                    "output_file": output_path.relative_to(PROJECT_ROOT).as_posix(),
                    **validation,
                }
            )
        except Exception as error:
            results.append(
                {
                    "law_id": law_id,
                    "law_name": law_name,
                    "status": "failed",
                    "error": str(error),
                }
            )
            print(f"  실패: {error}")

    status_counts = Counter(result["status"] for result in results)
    aggregate = Counter()
    for result in results:
        aggregate.update(result.get("structure_counts", {}))
    write_json(
        args.manifest.resolve(),
        {
            "version": "0.1",
            "selection_file": args.selection_file.resolve()
            .relative_to(PROJECT_ROOT)
            .as_posix(),
            "requested_count": len(laws),
            "counts": dict(status_counts),
            "aggregate_structure_counts": dict(aggregate),
            "completed_at": utc_timestamp(),
            "results": results,
        },
    )
    print(f"정규화 결과: {dict(status_counts)}")
    print(f"전체 구조 개수: {dict(aggregate)}")
    print(f"정규화 manifest: {args.manifest.resolve()}")
    if status_counts.get("failed", 0):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
