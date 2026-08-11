import argparse
import csv
import hashlib
from collections import Counter
from pathlib import Path

from law_api_common import PROJECT_ROOT, read_json, write_json

DEFAULT_SELECTION = (
    PROJECT_ROOT / "local_data" / "statutes" / "manifests" / "selection_v02.json"
)
DEFAULT_RULES = PROJECT_ROOT / "config" / "statute_selection_rules_v03.json"
DEFAULT_OUTPUT = (
    PROJECT_ROOT / "local_data" / "statutes" / "manifests" / "selection_v03.json"
)
DEFAULT_REVIEW_CSV = (
    PROJECT_ROOT
    / "local_data"
    / "statutes"
    / "reports"
    / "selection_review_queue_v03.csv"
)
DEFAULT_SAMPLE_CSV = (
    PROJECT_ROOT
    / "local_data"
    / "statutes"
    / "reports"
    / "selection_auto_exclude_sample_v03.csv"
)


def find_title_terms(law_name: str, terms: list[str]) -> list[str]:
    return [term for term in terms if term in law_name]


def triage_candidate(row: dict, rules: dict) -> tuple[str, str, list[str]]:
    classification = row["classification"]
    analysis = row["analysis"]
    score = int(classification["score"])
    title_terms = find_title_terms(row["law_name"], rules["review_title_terms"])
    purpose_and_context = bool(analysis["purpose_domain_terms"]) and (
        analysis["same_context_article_count"] > 0
    )

    reasons = []
    if score >= rules["minimum_review_score"]:
        reasons.append(f"관련성 점수 {score}점")
    if purpose_and_context:
        reasons.append("목적·정의 신호와 동일 조문 관련 문맥이 함께 존재")
    if title_terms:
        reasons.append(f"보호 주제 법령명: {', '.join(title_terms)}")

    if reasons:
        return "3차 사람 검토 필요", "; ".join(reasons), title_terms
    return (
        "3차 자동 제외 후보",
        "점수 4점 미만이며 목적·동일 조문·보호 주제 법령명 근거가 없음",
        [],
    )


def recommend_direction(row: dict, title_terms: list[str]) -> str:
    analysis = row["analysis"]
    classification = row["classification"]
    if title_terms and (
        analysis["same_context_article_count"] > 0
        or classification["core_article_terms"]
    ):
        return "관련성 확인 우선"
    if not title_terms and not analysis["purpose_domain_terms"]:
        return "제외 가능성 높음"
    return "경계 사례"


def evidence_text(row: dict) -> str:
    evidence = row["classification"].get("evidence", [])
    parts = []
    for item in evidence[:3]:
        parts.append(
            f"제{item.get('article_number', '')}조 "
            f"{item.get('article_title', '')}: {item.get('excerpt', '')}"
        )
    return " || ".join(parts)


def same_context_text(row: dict) -> str:
    items = row["analysis"].get("same_context_evidence", [])
    return " || ".join(
        f"제{item.get('article_number', '')}조 {item.get('article_title', '')} "
        f"(핵심: {', '.join(item.get('core_terms', []))}; "
        f"주거 문맥: {', '.join(item.get('anchor_terms', []))})"
        for item in items
    )


def review_record(row: dict) -> dict:
    return {
        "법령명": row["law_name"],
        "법령ID": row["law_id"],
        "추천방향": row["review_recommendation"],
        "3차선정근거": row["selection_reason"],
        "기존점수": row["classification"]["score"],
        "법령명보호키워드": ", ".join(row["review_title_terms"]),
        "목적·정의키워드": ", ".join(row["analysis"]["purpose_domain_terms"]),
        "목적·정의내용": row["analysis"]["purpose_excerpt"],
        "동일문맥근거": same_context_text(row),
        "관련조문근거": evidence_text(row),
        "최종결정": "",
        "최종근거": "",
    }


def write_records(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [review_record(row) for row in rows]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def sample_key(row: dict, seed: str) -> str:
    value = f"{seed}:{row['law_id']}:{row['law_name']}".encode()
    return hashlib.sha256(value).hexdigest()


def choose_stratified_sample(rows: list[dict], rules: dict) -> list[dict]:
    strata: dict[str, list[dict]] = {}
    for row in rows:
        classification = row["classification"]
        if classification["core_article_terms"]:
            stratum = "핵심 조문 용어 존재"
        elif classification["supporting_article_terms"]:
            stratum = "보조 조문 용어만 존재"
        else:
            stratum = "조문 용어 신호 없음"
        strata.setdefault(stratum, []).append(row)

    selected = []
    ordered_strata = sorted(strata)
    while len(selected) < rules["sample_size"]:
        added = False
        for stratum in ordered_strata:
            candidates = sorted(
                strata[stratum], key=lambda row: sample_key(row, rules["sample_seed"])
            )
            if candidates:
                row = candidates.pop(0)
                strata[stratum] = candidates
                row["sample_stratum"] = stratum
                selected.append(row)
                added = True
                if len(selected) == rules["sample_size"]:
                    break
        if not added:
            break
    return selected


def evaluate_exclusion_rule(rows: list[dict], rules: dict) -> dict:
    evaluated = []
    for row in rows:
        if not row["selection_status"].startswith("사람 검토"):
            continue
        suggestion, _, _ = triage_candidate(row, rules)
        if suggestion != "3차 자동 제외 후보":
            continue
        evaluated.append(row)

    correct = sum(
        row["selection_status"] == "사람 검토 제외" for row in evaluated
    )
    return {
        "human_labeled_total": sum(
            row["selection_status"].startswith("사람 검토") for row in rows
        ),
        "auto_exclude_evaluated": len(evaluated),
        "auto_exclude_correct": correct,
        "auto_exclude_precision": round(correct / len(evaluated), 4)
        if evaluated
        else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="2차 추가 검토 법령을 3차 축소합니다.")
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--review-csv", type=Path, default=DEFAULT_REVIEW_CSV)
    parser.add_argument("--sample-csv", type=Path, default=DEFAULT_SAMPLE_CSV)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = read_json(args.selection)
    rules = read_json(args.rules)
    results = []
    review_rows = []
    auto_exclude_rows = []

    for source_row in payload["results"]:
        row = dict(source_row)
        if row["selection_status"] == "추가 검토 필요":
            status, reason, title_terms = triage_candidate(row, rules)
            row["selection_status"] = status
            row["selection_reason"] = reason
            row["review_title_terms"] = title_terms
            row["review_recommendation"] = recommend_direction(row, title_terms)
            if status == "3차 사람 검토 필요":
                review_rows.append(row)
            else:
                auto_exclude_rows.append(row)
        results.append(row)

    review_rows.sort(
        key=lambda row: (-int(row["classification"]["score"]), row["law_name"])
    )
    sample_rows = choose_stratified_sample(auto_exclude_rows, rules)
    for row in sample_rows:
        row["review_recommendation"] = "자동 제외 표본 확인"
        row["selection_reason"] += f"; 표본층: {row['sample_stratum']}"

    metrics = evaluate_exclusion_rule(payload["results"], rules)
    counts = Counter(row["selection_status"] for row in results)
    write_json(
        args.output,
        {
            "version": "0.3",
            "source_selection": str(args.selection.resolve()),
            "total": len(results),
            "counts": dict(counts),
            "third_pass_metrics": metrics,
            "automatic_status_is_final_decision": False,
            "results": results,
        },
    )
    write_records(args.review_csv, review_rows)
    write_records(args.sample_csv, sample_rows)

    print(f"3차 사람 검토 필요: {len(review_rows)}개")
    print(f"3차 자동 제외 후보: {len(auto_exclude_rows)}개")
    print(f"자동 제외 표본: {len(sample_rows)}개")
    print(f"기존 사람 라벨 기준 평가: {metrics}")
    print(f"최종 검토표: {args.review_csv}")
    print(f"자동 제외 표본표: {args.sample_csv}")


if __name__ == "__main__":
    main()
