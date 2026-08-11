import argparse
import csv
from collections import Counter
from pathlib import Path

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
DEFAULT_OUTPUT = (
    PROJECT_ROOT / "local_data" / "statutes" / "manifests" / "selection_v01.json"
)
DEFAULT_REVIEW_CSV = (
    PROJECT_ROOT / "local_data" / "statutes" / "reports" / "selection_review_queue_v01.csv"
)

FAMILY_SUFFIXES = (
    " 시행령",
    " 시행규칙",
    " 규칙",
    " 시행세칙",
)


def find_parent_decision(
    law_name: str,
    labels_by_name: dict[str, dict],
    excluded_name: str | None = None,
) -> tuple[str, str] | None:
    matches = []
    for parent_name, label in labels_by_name.items():
        if parent_name == excluded_name:
            continue
        remainder = law_name.removeprefix(parent_name)
        if remainder in FAMILY_SUFFIXES:
            matches.append((len(parent_name), parent_name, label["human_decision"]))
    if not matches:
        return None
    _, parent_name, decision = max(matches)
    return decision, parent_name


def suggest_decision(
    result: dict,
    labels_by_name: dict[str, dict],
    excluded_name: str | None = None,
) -> tuple[str, str]:
    parent = find_parent_decision(result["law_name"], labels_by_name, excluded_name)
    if parent:
        decision, parent_name = parent
        status = "자동 포함 후보" if decision == "포함" else "자동 제외 후보"
        return status, f"사람 검토된 상위 법령 '{parent_name}'의 하위 법령"

    if result["scope_warnings"]:
        warnings = ", ".join(result["scope_warnings"])
        return "자동 제외 후보", f"MVP 제외 범위 법령명 경고: {warnings}"

    if result["title_direct_terms"] and result["core_article_terms"]:
        return "자동 포함 후보", "법령명 직접 키워드와 핵심 조문 키워드가 함께 확인됨"

    has_title_signal = bool(
        result["title_direct_terms"] or result["title_supporting_terms"]
    )
    has_article_evidence = bool(
        result["core_article_terms"] or result["supporting_article_terms"]
    )
    if not has_title_signal and not has_article_evidence:
        return "자동 제외 후보", "법령명 신호와 관련 조문 근거가 모두 없음"

    return "추가 검토 필요", "현재 규칙만으로 포함·제외를 안전하게 확정하기 어려움"


def calculate_metrics(rows: list[dict]) -> dict:
    decided = [row for row in rows if row["suggestion"] != "추가 검토 필요"]
    correct = 0
    include_tp = 0
    include_fp = 0
    exclude_correct = 0
    exclude_wrong = 0
    for row in decided:
        predicted = "포함" if row["suggestion"] == "자동 포함 후보" else "제외"
        actual = row["human_decision"]
        correct += predicted == actual
        if predicted == "포함":
            include_tp += actual == "포함"
            include_fp += actual != "포함"
        else:
            exclude_correct += actual == "제외"
            exclude_wrong += actual != "제외"

    return {
        "labeled_total": len(rows),
        "auto_decided": len(decided),
        "coverage": round(len(decided) / len(rows), 4) if rows else 0,
        "accuracy_on_auto_decided": round(correct / len(decided), 4) if decided else 0,
        "auto_include_precision": round(
            include_tp / (include_tp + include_fp), 4
        )
        if include_tp + include_fp
        else None,
        "auto_exclude_precision": round(
            exclude_correct / (exclude_correct + exclude_wrong), 4
        )
        if exclude_correct + exclude_wrong
        else None,
        "confusion": {
            "include_correct": include_tp,
            "include_wrong": include_fp,
            "exclude_correct": exclude_correct,
            "exclude_wrong": exclude_wrong,
        },
    }


def write_review_queue(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "법령명",
        "법령ID",
        "자동분류",
        "점수",
        "교정결과",
        "교정근거",
        "법령명키워드",
        "조문핵심키워드",
        "범위경고",
        "근거조문1",
        "최종결정",
        "최종근거",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            result = row["classification"]
            evidence = result["evidence"][0] if result["evidence"] else {}
            evidence_text = ""
            if evidence:
                evidence_text = (
                    f"{evidence.get('article_number', '')} "
                    f"{evidence.get('article_title', '')} | "
                    f"{evidence.get('excerpt', '')}"
                )
            writer.writerow(
                {
                    "법령명": result["law_name"],
                    "법령ID": result["law_id"],
                    "자동분류": result["automatic_label"],
                    "점수": result["score"],
                    "교정결과": row["selection_status"],
                    "교정근거": row["selection_reason"],
                    "법령명키워드": ", ".join(
                        result["title_direct_terms"]
                        + result["title_supporting_terms"]
                    ),
                    "조문핵심키워드": ", ".join(result["core_article_terms"]),
                    "범위경고": ", ".join(result["scope_warnings"]),
                    "근거조문1": evidence_text,
                    "최종결정": "",
                    "최종근거": "",
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="사람 검토 라벨로 법령 선별 규칙을 교정합니다.")
    parser.add_argument("--classification", type=Path, default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--human-labels", type=Path, default=DEFAULT_HUMAN_LABELS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--review-csv", type=Path, default=DEFAULT_REVIEW_CSV)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    classification = read_json(args.classification)
    human_payload = read_json(args.human_labels)
    human_by_id = {item["law_id"]: item for item in human_payload["labels"]}
    labels_by_name = {item["law_name"]: item for item in human_payload["labels"]}

    evaluation_rows = []
    selection_rows = []
    for result in classification["results"]:
        human = human_by_id.get(result["law_id"])
        suggestion, reason = suggest_decision(
            result,
            labels_by_name,
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
                "classification": result,
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
            "classification_file": str(args.classification.resolve()),
            "human_labels_file": str(args.human_labels.resolve()),
            "total": len(selection_rows),
            "counts": dict(counts),
            "calibration_metrics": metrics,
            "automatic_status_is_final_decision": False,
            "results": selection_rows,
        },
    )
    write_review_queue(args.review_csv, review_rows)

    print(f"전체: {len(selection_rows)}개")
    for status, count in counts.items():
        print(f"- {status}: {count}개")
    print(f"교정 규칙 평가: {metrics}")
    print(f"추가 검토표: {args.review_csv}")


if __name__ == "__main__":
    main()
