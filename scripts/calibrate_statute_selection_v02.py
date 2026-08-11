import argparse
import csv
from collections import Counter
from pathlib import Path

from calibrate_statute_selection import (
    calculate_metrics,
    find_parent_decision,
    write_review_queue,
)
from classify_statute_relevance import extract_article_units, find_terms
from law_api_common import PROJECT_ROOT, read_json, write_json

DEFAULT_CLASSIFICATION = (
    PROJECT_ROOT
    / "local_data"
    / "statutes"
    / "manifests"
    / "relevance_classification.json"
)
DEFAULT_HUMAN_LABELS = (
    PROJECT_ROOT / "local_data" / "statutes" / "reviews" / "human_labels_v01.json"
)
DEFAULT_RULES = PROJECT_ROOT / "config" / "statute_selection_rules_v02.json"
DEFAULT_V01_SELECTION = (
    PROJECT_ROOT / "local_data" / "statutes" / "manifests" / "selection_v01.json"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT / "local_data" / "statutes" / "manifests" / "selection_v02.json"
)
DEFAULT_REVIEW_CSV = (
    PROJECT_ROOT / "local_data" / "statutes" / "reports" / "selection_review_queue_v02.csv"
)
DEFAULT_COMPARISON_CSV = (
    PROJECT_ROOT / "local_data" / "statutes" / "reports" / "selection_v01_to_v02.csv"
)


def analyze_detail(detail_payload: dict, relevance_rules: dict, rules: dict) -> dict:
    articles = extract_article_units(detail_payload)
    title_patterns = rules["purpose_article_title_patterns"]
    purpose_articles = [
        article
        for article in articles
        if any(pattern in article["article_title"] for pattern in title_patterns)
    ]
    purpose_text = " ".join(article["content"] for article in purpose_articles)
    purpose_terms = find_terms(purpose_text, rules["purpose_domain_terms"])

    core_terms = relevance_rules["core_article_terms"]
    anchors = relevance_rules["residential_anchor_terms"]
    same_context_evidence = []
    for article in articles:
        text = f"{article['article_title']} {article['content']}"
        core = find_terms(text, core_terms)
        article_anchors = find_terms(text, anchors)
        if core and article_anchors:
            same_context_evidence.append(
                {
                    "article_number": article["article_number"],
                    "article_title": article["article_title"],
                    "core_terms": core,
                    "anchor_terms": article_anchors,
                }
            )

    excerpt = purpose_text[: rules["max_purpose_excerpt_length"]]
    return {
        "purpose_domain_terms": purpose_terms,
        "purpose_excerpt": excerpt,
        "same_context_article_count": len(same_context_evidence),
        "same_context_evidence": same_context_evidence[:3],
    }


def suggest_decision_v02(
    result: dict,
    analysis: dict,
    labels_by_name: dict[str, dict],
    rules: dict,
    excluded_name: str | None = None,
) -> tuple[str, str]:
    out_terms = find_terms(
        result["law_name"], rules["explicit_out_of_domain_title_terms"]
    )
    if out_terms:
        return "자동 제외 후보", f"명백한 타 도메인 법령명: {', '.join(out_terms)}"

    if result["scope_warnings"]:
        warnings = ", ".join(result["scope_warnings"])
        return "자동 제외 후보", f"MVP 제외 범위 법령명 경고: {warnings}"

    parent = find_parent_decision(result["law_name"], labels_by_name, excluded_name)
    family_terms = find_terms(result["law_name"], rules["direct_family_terms"])
    same_context = analysis["same_context_article_count"] > 0
    purpose_signal = bool(analysis["purpose_domain_terms"])

    if parent:
        parent_decision, parent_name = parent
        if parent_decision == "제외":
            if result["title_direct_terms"] and same_context:
                return (
                    "추가 검토 필요",
                    f"상위 법령 '{parent_name}'은 제외지만 직접 법령명·조문 근거가 존재함",
                )
            return "자동 제외 후보", f"사람이 제외한 상위 법령 '{parent_name}'의 하위 법령"

        if family_terms and (purpose_signal or same_context or result["title_direct_terms"]):
            return (
                "자동 포함 후보",
                f"포함 상위 법령 '{parent_name}'과 직접 도메인·목적 또는 조문 근거가 함께 확인됨",
            )
        return (
            "추가 검토 필요",
            f"상위 법령 '{parent_name}'은 포함이지만 하위 법령 자체의 직접 근거가 부족함",
        )

    if result["title_direct_terms"] and same_context:
        return "자동 포함 후보", "법령명 직접 키워드와 동일 조문의 핵심·주거 문맥이 확인됨"

    has_title_signal = bool(
        result["title_direct_terms"] or result["title_supporting_terms"] or family_terms
    )
    has_article_signal = bool(
        result["core_article_terms"] or result["supporting_article_terms"]
    )
    if not has_title_signal and not has_article_signal and not purpose_signal:
        return "자동 제외 후보", "법령명·목적·적용범위·조문에 관련 근거가 모두 없음"

    return "추가 검토 필요", "직접 포함 또는 안전한 제외를 확정할 근거가 부족함"


def write_comparison_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "법령명",
                "법령ID",
                "v1결과",
                "v2결과",
                "v2근거",
                "목적_도메인키워드",
                "동일문맥_조문수",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "법령명": row["law_name"],
                    "법령ID": row["law_id"],
                    "v1결과": row["v1_status"],
                    "v2결과": row["v2_status"],
                    "v2근거": row["v2_reason"],
                    "목적_도메인키워드": ", ".join(
                        row["analysis"]["purpose_domain_terms"]
                    ),
                    "동일문맥_조문수": row["analysis"]["same_context_article_count"],
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="법령 목적·동일 조문 문맥 기반 2차 교정")
    parser.add_argument("--classification", type=Path, default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--human-labels", type=Path, default=DEFAULT_HUMAN_LABELS)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--v01-selection", type=Path, default=DEFAULT_V01_SELECTION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--review-csv", type=Path, default=DEFAULT_REVIEW_CSV)
    parser.add_argument("--comparison-csv", type=Path, default=DEFAULT_COMPARISON_CSV)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    classification = read_json(args.classification)
    human_payload = read_json(args.human_labels)
    rules = read_json(args.rules)
    relevance_rules = read_json(PROJECT_ROOT / "config" / "statute_relevance_rules_v01.json")
    v01 = read_json(args.v01_selection)

    human_by_id = {item["law_id"]: item for item in human_payload["labels"]}
    labels_by_name = {item["law_name"]: item for item in human_payload["labels"]}
    v01_by_id = {item["law_id"]: item for item in v01["results"]}

    evaluation_rows = []
    selection_rows = []
    comparisons = []
    for result in classification["results"]:
        detail_path = (
            PROJECT_ROOT
            / "local_data"
            / "statutes"
            / "raw"
            / "details"
            / f"{result['law_id']}.json"
        )
        analysis = analyze_detail(read_json(detail_path), relevance_rules, rules)
        human = human_by_id.get(result["law_id"])
        suggestion, reason = suggest_decision_v02(
            result,
            analysis,
            labels_by_name,
            rules,
            excluded_name=result["law_name"] if human else None,
        )
        if human:
            evaluation_rows.append(
                {
                    "law_id": result["law_id"],
                    "human_decision": human["human_decision"],
                    "suggestion": suggestion,
                }
            )
            status = f"사람 검토 {human['human_decision']}"
            selection_reason = human["human_reason"]
        else:
            status = suggestion
            selection_reason = reason

        selection_rows.append(
            {
                "law_id": result["law_id"],
                "law_name": result["law_name"],
                "selection_status": status,
                "selection_reason": selection_reason,
                "analysis": analysis,
                "classification": result,
            }
        )
        comparisons.append(
            {
                "law_id": result["law_id"],
                "law_name": result["law_name"],
                "v1_status": v01_by_id[result["law_id"]]["selection_status"],
                "v2_status": status,
                "v2_reason": selection_reason,
                "analysis": analysis,
            }
        )

    metrics = calculate_metrics(evaluation_rows)
    counts = Counter(row["selection_status"] for row in selection_rows)
    review_rows = [
        row for row in selection_rows if row["selection_status"] == "추가 검토 필요"
    ]
    review_rows.sort(
        key=lambda row: (
            -row["classification"]["score"],
            row["classification"]["law_name"],
        )
    )
    write_json(
        args.output,
        {
            "version": "0.2",
            "total": len(selection_rows),
            "counts": dict(counts),
            "calibration_metrics": metrics,
            "automatic_status_is_final_decision": False,
            "results": selection_rows,
        },
    )
    write_review_queue(args.review_csv, review_rows)
    write_comparison_csv(args.comparison_csv, comparisons)

    print(f"전체: {len(selection_rows)}개")
    for status, count in counts.items():
        print(f"- {status}: {count}개")
    print(f"2차 규칙 평가: {metrics}")
    print(f"v1-v2 비교표: {args.comparison_csv}")


if __name__ == "__main__":
    main()
