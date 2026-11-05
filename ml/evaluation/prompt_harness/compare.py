# compare.py
"""
Description: 저장된 하네스 실행 결과를 현재 채점 규칙으로 다시 채점해 버전별로 나란히 비교한다.
모델을 호출하지 않으므로 규칙을 고친 뒤에도 이전 결과와 같은 기준으로 비교할 수 있다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 채점 규칙을 바꾸면 이전 실행 결과와 공정하게 비교하기 어려운 상태.
After:
    - 여러 실행 폴더를 같은 규칙으로 재채점해 반환율과 규칙별 실패·경고를 한 표로 출력함.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from ml.evaluation.prompt_harness.checks import ItemContext, evaluate_item
from ml.evaluation.prompt_harness.runner import load_cases


def rescore_run(run_dir: Path) -> dict:
    cases = {case.id: case for case in load_cases()}
    fails: Counter = Counter()
    warns: Counter = Counter()
    expected = sum(len(case.items) for case in cases.values())
    returned = 0
    for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["item_id"] == "(case)" or not record["output"]:
            continue
        case = cases[record["case_id"]]
        item = next(
            (i for i in case.items if i.query_item.item_id == record["item_id"]), None
        )
        if item is None:
            continue
        returned += 1
        context = ItemContext(
            kind=case.kind,
            item_id=record["item_id"],
            query=item.query_item.query,
            evidence={e.source_id: e.title + "\n" + e.text for e in item.evidence},
            injection_marker=case.injection_marker,
            expected_status=case.expected_status if len(case.items) == 1 else None,
            category=item.query_item.category,
        )
        for result in evaluate_item(context, record["output"]):
            if not result.passed:
                (fails if result.severity == "fail" else warns)[result.name] += 1
    return {"expected": expected, "returned": returned, "fails": fails, "warns": warns}


def render_table(runs: dict[str, dict]) -> str:
    names = sorted({n for r in runs.values() for n in (*r["fails"], *r["warns"])})
    header = "| 지표 | " + " | ".join(runs) + " |"
    lines = [header, "|---|" + "---|" * len(runs)]
    lines.append(
        "| 항목 반환 | "
        + " | ".join(f"{r['returned']}/{r['expected']}" for r in runs.values())
        + " |"
    )
    for name in names:
        cells = []
        for run in runs.values():
            fail, warn = run["fails"].get(name, 0), run["warns"].get(name, 0)
            cells.append(f"실패 {fail}" if fail else (f"경고 {warn}" if warn else "0"))
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+", type=Path)
    args = parser.parse_args()
    print(render_table({path.name: rescore_run(path) for path in args.run_dirs}))


if __name__ == "__main__":
    main()
