# scope_eval.py
"""
Description: 특약 요청 적합성 분류기(범위 안·정보 부족·범위 밖)의 정확도를 같은 케이스로 측정한다.
기본은 모델을 호출하지 않는 가짜 분류기이며, 실호출은 승인 플래그와 호출 횟수 한도가 있어야 한다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 적합성 분류의 정확도를 반복 측정하는 방법이 없는 상태.
After:
    - 케이스별 기대 판정과 비교해 정확도와 오분류 목록을 기록함.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ml.evaluation.prompt_harness.runner import DEFAULT_OUTPUT_ROOT, HarnessConfigError

DEFAULT_SCOPE_CASES_PATH = Path(__file__).with_name("scope_cases_v01.json")
BATCH_SIZE = 10
DEFAULT_MAX_LIVE_CALLS = 5


@dataclass
class ScopeEvalSummary:
    run_id: str
    mode: str
    total: int = 0
    correct: int = 0
    mistakes: list[dict[str, str]] = field(default_factory=list)
    usage: dict[str, float] = field(default_factory=dict)


class _PerfectClassifier:
    """채점 흐름 확인용 가짜 분류기로, 항상 기대 판정을 돌려준다."""

    def __init__(self, cases: list[dict[str, str]]) -> None:
        self._expected = {case["request"]: case["expected"] for case in cases}

    def classify(self, requests: list[str]) -> list[str]:
        return [self._expected[request] for request in requests]


def load_scope_cases(path: Path | None = None) -> list[dict[str, str]]:
    return json.loads((path or DEFAULT_SCOPE_CASES_PATH).read_text(encoding="utf-8"))["cases"]


def run_scope_eval(
    *,
    mode: str = "fake",
    output_dir: Path | None = None,
    classifier: Any = None,
    confirm_live: bool = False,
    api_key: str | None = None,
    max_calls: int = DEFAULT_MAX_LIVE_CALLS,
    model: str | None = None,
) -> ScopeEvalSummary:
    if mode not in {"fake", "live"}:
        raise HarnessConfigError("mode는 fake|live여야 합니다.")
    cases = load_scope_cases()
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = output_dir or DEFAULT_OUTPUT_ROOT / f"scope_{run_id}"
    output_dir.mkdir(parents=True, exist_ok=True)
    usage_path = output_dir / "usage.jsonl"

    if mode == "live":
        if not confirm_live:
            raise HarnessConfigError("실제 모델 호출은 --confirm-live 가 있어야 실행합니다(유료).")
        calls = math.ceil(len(cases) / BATCH_SIZE)
        if calls > max_calls:
            raise HarnessConfigError(f"호출 {calls}회가 한도 {max_calls}회를 넘습니다.")
        if api_key is None:
            from precedents.precedent_config import load_env_file

            load_env_file()
            api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise HarnessConfigError("GEMINI_API_KEY 가 없습니다.")
        if classifier is None:
            from api.services.gemini_special_clause_scope import (
                DEFAULT_GEMINI_MODEL,
                GeminiSpecialClauseScopeClassifier,
            )

            classifier = GeminiSpecialClauseScopeClassifier(
                api_key=api_key,
                model=model or DEFAULT_GEMINI_MODEL,
                usage_manifest_path=usage_path,
            )
    elif classifier is None:
        classifier = _PerfectClassifier(cases)

    summary = ScopeEvalSummary(run_id=run_id, mode=mode, total=len(cases))
    rows: list[dict[str, str]] = []
    for start in range(0, len(cases), BATCH_SIZE):
        batch = cases[start : start + BATCH_SIZE]
        answers = classifier.classify([case["request"] for case in batch])
        for case, answer in zip(batch, answers):
            correct = answer == case["expected"]
            summary.correct += correct
            row = {
                "id": case["id"],
                "request": case["request"],
                "expected": case["expected"],
                "actual": answer,
                "correct": correct,
            }
            rows.append(row)
            if not correct:
                summary.mistakes.append(
                    {"id": case["id"], "expected": case["expected"], "actual": answer}
                )
    if usage_path.exists():
        usage_rows = [json.loads(line) for line in usage_path.read_text(encoding="utf-8").splitlines()]
        summary.usage = {
            "calls": len(usage_rows),
            "input_tokens": sum(row.get("input_tokens", 0) for row in usage_rows),
            "output_tokens": sum(row.get("output_tokens", 0) for row in usage_rows),
            "estimated_cost_usd": round(sum(row.get("estimated_cost_usd", 0) for row in usage_rows), 6),
        }

    with (output_dir / "results.jsonl").open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    lines = [
        f"# 적합성 분류 평가 {run_id}",
        "",
        f"- 모드: {mode}",
        f"- 정확도: {summary.correct}/{summary.total}",
        f"- 사용량: {summary.usage}",
        "",
        "## 오분류",
        "",
    ]
    lines += [
        f"- {m['id']}: 기대 {m['expected']} / 실제 {m['actual']}" for m in summary.mistakes
    ] or ["- 없음"]
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["fake", "live"], default="fake")
    parser.add_argument("--confirm-live", action="store_true", help="유료 모델 호출을 허용합니다.")
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_LIVE_CALLS)
    parser.add_argument("--model", default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    summary = run_scope_eval(
        mode=args.mode,
        output_dir=args.output_dir,
        confirm_live=args.confirm_live,
        max_calls=args.max_calls,
        model=args.model,
    )
    print(f"적합성 분류 {summary.correct}/{summary.total} 정답, 오분류 {len(summary.mistakes)}건")


if __name__ == "__main__":
    main()
