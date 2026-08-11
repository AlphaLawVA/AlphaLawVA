import argparse
import csv
from collections import Counter
from pathlib import Path

from law_api_common import PROJECT_ROOT, read_json, write_json

DEFAULT_SELECTION = (
    PROJECT_ROOT / "local_data" / "statutes" / "manifests" / "selection_v03.json"
)
DEFAULT_DECISIONS = (
    PROJECT_ROOT / "config" / "statute_selection_decisions_v03.json"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "local_data"
    / "statutes"
    / "manifests"
    / "final_selection_v01.json"
)
DEFAULT_REPORT = (
    PROJECT_ROOT
    / "local_data"
    / "statutes"
    / "reports"
    / "final_selection_v01.csv"
)
DEFAULT_INCLUDED_LIST = (
    PROJECT_ROOT / "config" / "statute_inclusion_list_v01.json"
)


def finalize_row(row: dict, decisions: dict) -> tuple[str, str]:
    status = row["selection_status"]
    if status == "사람 검토 포함":
        return "포함", "기존 사람 검토 포함"
    if status == "사람 검토 제외":
        return "제외", "기존 사람 검토 제외"
    if status == "자동 포함 후보":
        return "포함", "2차 자동 포함 규칙 통과"
    if status in {"자동 제외 후보", "3차 자동 제외 후보"}:
        return "제외", status

    if status != "3차 사람 검토 필요":
        raise ValueError(f"알 수 없는 선별 상태: {status}")

    direction = row["review_recommendation"]
    include_names = set(decisions["related_priority"]["include"])
    if direction == "관련성 확인 우선":
        if row["law_name"] in include_names:
            return "포함", decisions["related_priority"]["reason"]
        return "제외", decisions["related_priority"]["reason"]
    if direction == "경계 사례":
        return "제외", decisions["boundary_cases"]["reason"]
    if direction == "제외 가능성 높음":
        return "제외", decisions["likely_excluded"]["reason"]
    raise ValueError(f"알 수 없는 검토 방향: {direction}")


def validate_decisions(rows: list[dict], decisions: dict) -> None:
    priority_names = {
        row["law_name"]
        for row in rows
        if row.get("review_recommendation") == "관련성 확인 우선"
    }
    include_names = set(decisions["related_priority"]["include"])
    missing = include_names - priority_names
    if missing:
        raise ValueError(f"관련성 확인 우선 목록에 없는 포함 법령: {sorted(missing)}")
    if len(priority_names) != 12:
        raise ValueError(f"관련성 확인 우선 법령 수가 12개가 아님: {len(priority_names)}")


def write_report(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "법령명",
        "법령ID",
        "최종결정",
        "최종근거",
        "이전상태",
        "검토방향",
        "기존점수",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "법령명": row["law_name"],
                    "법령ID": row["law_id"],
                    "최종결정": row["final_decision"],
                    "최종근거": row["final_reason"],
                    "이전상태": row["selection_status"],
                    "검토방향": row.get("review_recommendation", ""),
                    "기존점수": row["classification"]["score"],
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="법령 3차 검토 결정을 최종 반영합니다.")
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--included-list", type=Path, default=DEFAULT_INCLUDED_LIST)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = read_json(args.selection)
    decisions = read_json(args.decisions)
    validate_decisions(payload["results"], decisions)

    results = []
    for source_row in payload["results"]:
        row = dict(source_row)
        final_decision, final_reason = finalize_row(row, decisions)
        row["final_decision"] = final_decision
        row["final_reason"] = final_reason
        results.append(row)

    counts = Counter(row["final_decision"] for row in results)
    if len(results) != 598 or sum(counts.values()) != 598:
        raise ValueError(f"최종 선별 건수 오류: {len(results)}개, {dict(counts)}")

    write_json(
        args.output,
        {
            "version": "0.1",
            "source_selection": str(args.selection.resolve()),
            "decision_file": str(args.decisions.resolve()),
            "total": len(results),
            "counts": dict(counts),
            "results": results,
        },
    )
    write_report(args.report, results)
    included = [
        {
            "law_id": row["law_id"],
            "law_name": row["law_name"],
            "law_type": row["classification"]["law_type"],
            "effective_date": row["classification"]["effective_date"],
            "decision_basis": row["final_reason"],
        }
        for row in results
        if row["final_decision"] == "포함"
    ]
    included.sort(key=lambda row: (row["law_name"], row["law_id"]))
    write_json(
        args.included_list,
        {
            "version": "0.1",
            "scope": decisions["scope"],
            "total": len(included),
            "source_decisions": str(args.decisions.relative_to(PROJECT_ROOT)),
            "laws": included,
        },
    )
    print(f"최종 선별: {dict(counts)}")
    print(f"최종 manifest: {args.output}")
    print(f"최종 검토표: {args.report}")
    print(f"Git 저장용 포함 목록: {args.included_list}")


if __name__ == "__main__":
    main()
