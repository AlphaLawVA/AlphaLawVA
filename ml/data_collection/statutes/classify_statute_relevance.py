import argparse
import csv
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from law_api_common import (
    PROJECT_ROOT,
    STATUTE_CONFIG_DIR,
    STATUTE_DATA_DIR,
    as_list,
    read_json,
    write_json,
)

DEFAULT_RULES = STATUTE_CONFIG_DIR / "statute_relevance_rules_v01.json"
DEFAULT_SEED_MANIFEST = STATUTE_DATA_DIR / "manifests/seed_collection.json"
DEFAULT_EXPANDED_MANIFEST = STATUTE_DATA_DIR / "manifests/expanded_collection.json"
DEFAULT_DISCOVERY_MANIFEST = STATUTE_DATA_DIR / "manifests/discovery_candidates.json"
DEFAULT_OUTPUT_MANIFEST = STATUTE_DATA_DIR / "manifests/relevance_classification.json"
DEFAULT_OUTPUT_CSV = STATUTE_DATA_DIR / "reports/relevance_review.csv"


def normalize_space(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def find_terms(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if term in text]


def extract_law_name(payload: dict) -> str:
    law = payload.get("법령", {})
    basic = law.get("기본정보", {})
    name = basic.get("법령명_한글") or basic.get("법령명한글")
    return normalize_space(name)


def extract_nested_contents(value: object) -> list[str]:
    contents = []
    if isinstance(value, list):
        for item in value:
            contents.extend(extract_nested_contents(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in {"항내용", "호내용", "목내용"}:
                text = normalize_space(item)
                if text:
                    contents.append(text)
            elif key in {"항", "호", "목"}:
                contents.extend(extract_nested_contents(item))
    return contents


def extract_article_units(payload: dict) -> list[dict]:
    law = payload.get("법령", {})
    articles = law.get("조문") or {}
    units = as_list(articles.get("조문단위")) if isinstance(articles, dict) else []
    result = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        content_parts = [normalize_space(unit.get("조문내용"))]
        for key in ("항", "호", "목"):
            content_parts.extend(extract_nested_contents(unit.get(key)))
        content = normalize_space(" ".join(part for part in content_parts if part))
        if not content:
            continue
        result.append(
            {
                "article_number": normalize_space(unit.get("조문번호")),
                "article_title": normalize_space(unit.get("조문제목")),
                "content": content,
            }
        )
    return result


def make_excerpt(text: str, terms: list[str], max_length: int) -> str:
    positions = [text.find(term) for term in terms if term in text]
    context_before = min(70, max_length // 3)
    start = max(0, min(positions) - context_before) if positions else 0
    end = min(len(text), start + max_length)
    excerpt = text[start:end]
    if start > 0:
        excerpt = f"...{excerpt}"
    if end < len(text):
        excerpt = f"{excerpt}..."
    return excerpt


def collect_article_evidence(
    articles: list[dict], rules: dict
) -> tuple[list[str], list[str], list[dict], int]:
    core_terms = rules["core_article_terms"]
    supporting_terms = rules["supporting_article_terms"]
    anchors = rules["residential_anchor_terms"]
    evidence = []
    matched_core = set()
    matched_supporting = set()
    anchor_article_count = 0

    for article in articles:
        text = f"{article['article_title']} {article['content']}"
        core = find_terms(text, core_terms)
        supporting = find_terms(text, supporting_terms)
        matched = core + supporting
        if not matched:
            continue
        matched_core.update(core)
        matched_supporting.update(supporting)
        if find_terms(text, anchors):
            anchor_article_count += 1
        evidence.append(
            {
                "article_number": article["article_number"],
                "article_title": article["article_title"],
                "matched_terms": matched,
                "excerpt": make_excerpt(
                    article["content"], matched, rules["max_excerpt_length"]
                ),
            }
        )

    evidence.sort(
        key=lambda item: (
            -sum(term in core_terms for term in item["matched_terms"]),
            -len(item["matched_terms"]),
            item["article_number"],
        )
    )
    return (
        sorted(matched_core),
        sorted(matched_supporting),
        evidence[: rules["max_evidence_articles"]],
        anchor_article_count,
    )


def classify_law(
    metadata: dict,
    detail_payload: dict,
    rules: dict,
    is_seed: bool,
    discovery: dict | None = None,
) -> dict:
    law_name = metadata.get("law_name") or extract_law_name(detail_payload)
    title_direct = find_terms(law_name, rules["direct_title_terms"])
    title_supporting = find_terms(law_name, rules["supporting_title_terms"])
    scope_warnings = find_terms(law_name, rules["scope_warning_terms"])
    articles = extract_article_units(detail_payload)
    core, supporting, evidence, anchor_article_count = collect_article_evidence(
        articles, rules
    )

    scoring = rules["scoring"]
    score = 0
    score += scoring["seed_candidate"] if is_seed else 0
    score += len(title_direct) * scoring["direct_title_match"]
    score += len(title_supporting) * scoring["supporting_title_match"]
    score += len(core) * scoring["core_article_match"]
    score += len(supporting) * scoring["supporting_article_match"]
    score += len(scope_warnings) * scoring["scope_warning"]

    direct_rule = is_seed or (
        not scope_warnings
        and (
            (bool(title_direct) and bool(core or supporting))
            or (
                bool(title_supporting)
                and bool(core)
                and score >= scoring["direct_score"]
            )
            or (
                len(core) >= 3
                and anchor_article_count >= 2
                and score >= scoring["direct_score"]
            )
        )
    )
    if direct_rule:
        label = "직접 관련"
    elif score >= scoring["indirect_score"] and (core or supporting or title_supporting):
        label = "간접 관련"
    else:
        label = "노이즈 가능"

    discovery = discovery or {}
    return {
        "law_id": str(metadata.get("law_id", "")),
        "law_name": law_name,
        "law_type": metadata.get("law_type", ""),
        "effective_date": metadata.get("effective_date", ""),
        "automatic_label": label,
        "score": score,
        "seed_candidate": is_seed,
        "title_direct_terms": title_direct,
        "title_supporting_terms": title_supporting,
        "core_article_terms": core,
        "supporting_article_terms": supporting,
        "scope_warnings": scope_warnings,
        "discovery_queries": [
            item.get("query", "") for item in discovery.get("matched_queries", [])
        ],
        "evidence": evidence,
        "article_count": len(articles),
    }


def load_records(seed_manifest: dict, expanded_manifest: dict) -> tuple[list[dict], set[str]]:
    seed_records = [item for item in seed_manifest.get("results", []) if item.get("status") == "collected"]
    expanded_records = [
        item for item in expanded_manifest.get("results", []) if item.get("status") == "collected"
    ]
    seed_ids = {str(item["law_id"]) for item in seed_records}
    merged = {str(item["law_id"]): item for item in expanded_records}
    merged.update({str(item["law_id"]): item for item in seed_records})
    return sorted(merged.values(), key=lambda item: str(item["law_id"])), seed_ids


def evidence_text(item: dict) -> str:
    number = item.get("article_number") or "조문번호 없음"
    title = item.get("article_title") or "제목 없음"
    terms = ", ".join(item.get("matched_terms", []))
    return f"{number} {title} | 일치: {terms} | {item.get('excerpt', '')}"


def write_review_csv(path: Path, results: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "법령명",
        "법령ID",
        "법령종류",
        "시행일자",
        "자동분류",
        "점수",
        "초기22개",
        "법령명_직접키워드",
        "법령명_보조키워드",
        "조문_핵심키워드",
        "조문_보조키워드",
        "범위경고",
        "수집검색어",
        "근거조문1",
        "근거조문2",
        "근거조문3",
        "사람검토결정",
        "사람검토근거",
        "검토자",
        "검토일",
        "비고",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            evidence = [evidence_text(item) for item in result["evidence"]]
            evidence.extend([""] * (3 - len(evidence)))
            writer.writerow(
                {
                    "법령명": result["law_name"],
                    "법령ID": result["law_id"],
                    "법령종류": result["law_type"],
                    "시행일자": result["effective_date"],
                    "자동분류": result["automatic_label"],
                    "점수": result["score"],
                    "초기22개": "Y" if result["seed_candidate"] else "N",
                    "법령명_직접키워드": ", ".join(result["title_direct_terms"]),
                    "법령명_보조키워드": ", ".join(result["title_supporting_terms"]),
                    "조문_핵심키워드": ", ".join(result["core_article_terms"]),
                    "조문_보조키워드": ", ".join(result["supporting_article_terms"]),
                    "범위경고": ", ".join(result["scope_warnings"]),
                    "수집검색어": ", ".join(result["discovery_queries"]),
                    "근거조문1": evidence[0],
                    "근거조문2": evidence[1],
                    "근거조문3": evidence[2],
                    "사람검토결정": "",
                    "사람검토근거": "",
                    "검토자": "",
                    "검토일": "",
                    "비고": "",
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="수집한 법령의 관련성을 1차 자동 분류합니다.")
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--seed-manifest", type=Path, default=DEFAULT_SEED_MANIFEST)
    parser.add_argument("--expanded-manifest", type=Path, default=DEFAULT_EXPANDED_MANIFEST)
    parser.add_argument("--discovery-manifest", type=Path, default=DEFAULT_DISCOVERY_MANIFEST)
    parser.add_argument("--output-manifest", type=Path, default=DEFAULT_OUTPUT_MANIFEST)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rules = read_json(args.rules)
    seed_manifest = read_json(args.seed_manifest)
    expanded_manifest = read_json(args.expanded_manifest)
    discovery_payload = read_json(args.discovery_manifest)
    discovery_by_id = {
        str(item["law_id"]): item for item in discovery_payload.get("candidates", [])
    }
    records, seed_ids = load_records(seed_manifest, expanded_manifest)

    results = []
    for metadata in records:
        law_id = str(metadata["law_id"])
        detail_file = PROJECT_ROOT / metadata["detail_file"]
        result = classify_law(
            metadata=metadata,
            detail_payload=read_json(detail_file),
            rules=rules,
            is_seed=law_id in seed_ids,
            discovery=discovery_by_id.get(law_id),
        )
        results.append(result)

    results.sort(key=lambda item: (item["automatic_label"], -item["score"], item["law_name"]))
    counts = Counter(item["automatic_label"] for item in results)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_json(
        args.output_manifest,
        {
            "rules_file": str(args.rules.resolve()),
            "total": len(results),
            "counts": dict(counts),
            "generated_at": generated_at,
            "classification_is_deletion_decision": False,
            "results": results,
        },
    )
    write_review_csv(args.output_csv, results)
    print(f"분류 완료: {len(results)}개")
    for label in rules["labels"]:
        print(f"- {label}: {counts.get(label, 0)}개")
    print(f"검토표: {args.output_csv}")


if __name__ == "__main__":
    main()
