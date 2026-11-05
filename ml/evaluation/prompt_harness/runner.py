# runner.py
"""
Description: 계약서 분석·특약 생성 프롬프트를 같은 입력 세트로 실행하고 채점해 결과를 기록한다.
기본은 모델을 호출하지 않는 가짜 응답이며, 실제 호출은 명시적 승인 플래그와 호출 횟수 한도가 있어야 한다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 프롬프트 버전별 결과를 같은 기준으로 반복 측정하고 남기는 도구가 없는 상태.
After:
    - 프롬프트 파일 교체, 오프라인 검증, 실호출 가드, 요약·검토표 기록을 지원함.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

from api.services import gemini_contract_review_analyzer as review_module
from api.services import gemini_special_clause_generator as clause_module
from ml.agent.contract_review_graph import (
    ContractReviewEvidence,
    ContractReviewRetrievalQuery,
)
from ml.evaluation.prompt_harness.checks import (
    CheckResult,
    ItemContext,
    evaluate_item,
    output_text,
)

PACKAGE_DIR = Path(__file__).parent
DEFAULT_CASES_PATH = PACKAGE_DIR / "cases_v01.json"
DEFAULT_POOL_PATH = PACKAGE_DIR / "evidence_pool_v01.json"
DEFAULT_OUTPUT_ROOT = Path("local_data/prompt_harness/runs")
DEFAULT_MAX_LIVE_CALLS = 30


class HarnessConfigError(ValueError):
    """하네스 실행 설정이 안전하지 않거나 올바르지 않을 때 발생한다."""


@dataclass(frozen=True)
class CaseItem:
    query_item: ContractReviewRetrievalQuery
    evidence: list[ContractReviewEvidence]


@dataclass(frozen=True)
class HarnessCase:
    id: str
    kind: str  # "review" 또는 "clause"
    tags: list[str]
    items: list[CaseItem]
    expected_status: str | None
    injection_marker: str | None


@dataclass
class HarnessSummary:
    run_id: str
    mode: str
    model: str | None
    total_cases: int = 0
    total_items: int = 0
    expected_items: int = 0
    returned_items: int = 0
    failed_items: int = 0
    warned_items: int = 0
    check_failures: Counter = field(default_factory=Counter)
    check_warnings: Counter = field(default_factory=Counter)
    check_totals: Counter = field(default_factory=Counter)
    review_prompt_version: str | None = None
    review_prompt_sha256: str | None = None
    clause_prompt_version: str | None = None
    clause_prompt_sha256: str | None = None
    usage: dict[str, float] = field(default_factory=dict)


def load_pool(path: Path | None = None) -> dict[str, dict[str, str]]:
    entries = json.loads((path or DEFAULT_POOL_PATH).read_text(encoding="utf-8"))
    return {entry["source_id"]: entry for entry in entries}


def _evidence_from_pool(item_id: str, source_ids: list[str], pool) -> list[ContractReviewEvidence]:
    evidence = []
    for index, source_id in enumerate(source_ids):
        entry = pool[source_id]
        evidence.append(
            ContractReviewEvidence(
                item_id=item_id,
                source_type="statute",
                source_id=source_id,
                title=f"{entry['law_name']} {entry['article_label']}({entry['article_title']})",
                text=entry["text"],
                rank=index + 1,
                similarity=round(0.9 - 0.05 * index, 2),
                metadata={"effective_date": entry["effective_date"]},
            )
        )
    return evidence


def load_cases(
    cases_path: Path | None = None,
    pool_path: Path | None = None,
) -> list[HarnessCase]:
    document = json.loads((cases_path or DEFAULT_CASES_PATH).read_text(encoding="utf-8"))
    pool = load_pool(pool_path)
    cases: list[HarnessCase] = []
    for kind, key in (("review", "review_cases"), ("clause", "clause_cases")):
        for raw in document[key]:
            synthetic = {entry["source_id"]: entry for entry in raw.get("synthetic_evidence", [])}
            items = []
            for raw_item in raw["items"]:
                item_id = raw_item["item_id"]
                evidence = _evidence_from_pool(item_id, raw_item["evidence_ids"], pool)
                for offset, source_id in enumerate(raw_item.get("extra_evidence_ids", [])):
                    entry = synthetic[source_id]
                    evidence.append(
                        ContractReviewEvidence(
                            item_id=item_id,
                            source_type="statute",
                            source_id=source_id,
                            title=entry["title"],
                            text=entry["text"],
                            rank=len(evidence) + 1,
                            similarity=round(0.9 - 0.05 * (len(evidence) + offset), 2),
                            metadata={},
                        )
                    )
                items.append(
                    CaseItem(
                        query_item=ContractReviewRetrievalQuery(
                            item_id=item_id,
                            category=raw_item["category"],
                            query=raw_item["query"],
                        ),
                        evidence=evidence,
                    )
                )
            cases.append(
                HarnessCase(
                    id=raw["id"],
                    kind=kind,
                    tags=raw.get("tags", []),
                    items=items,
                    expected_status=raw.get("expected_status"),
                    injection_marker=raw.get("injection_marker"),
                )
            )
    return cases


def _fake_hook(case: HarnessCase) -> Callable[[dict], dict]:
    """프롬프트 규칙을 지킨 깨끗한 응답을 만드는 오프라인용 가짜 모델이다."""

    def hook(payload: dict) -> dict:
        items = []
        for item in payload["items"]:
            source_id = item["evidence"][0]["source_id"]
            if case.kind == "review":
                items.append(
                    {
                        "item_id": item["item_id"],
                        "summary": "원문과 근거를 대조해 확인할 쟁점을 정리했습니다.",
                        "review_status": "check",
                        "revision": "원문의 취지를 유지하며 범위와 조건을 명확히 합니다.",
                        "negotiation": "범위와 조건을 상대방과 확인하세요.",
                        "evidence_source_ids": [source_id],
                    }
                )
            else:
                ready = (case.expected_status or "draft_ready") == "draft_ready"
                items.append(
                    {
                        "item_id": item["item_id"],
                        "status": "draft_ready" if ready else "needs_input",
                        "draft_text": "당사자는 합의한 범위에서 이행한다." if ready else "",
                        "title": "특약 초안" if ready else "추가 정보 확인",
                        "reason": "필요한 이유를 정리한 문구입니다." if ready else "",
                        "strategy": "합의할 범위를 확인하세요." if ready else "",
                        "needs_input": [] if ready else ["적용 범위와 기한을 알려주세요."],
                        "evidence_source_ids": [source_id],
                    }
                )
        return {
            "candidates": [{"content": {"parts": [{"text": json.dumps({"items": items}, ensure_ascii=False)}]}}],
            "usageMetadata": {"promptTokenCount": 0, "candidatesTokenCount": 0},
        }

    return hook


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _item_results(
    case: HarnessCase,
    raw_items: list[Any],
    validated_ids: set[str],
) -> list[dict[str, Any]]:
    by_id = {item.query_item.item_id: item for item in case.items}
    records: list[dict[str, Any]] = []
    returned: set[str] = set()
    for raw in raw_items:
        item_id = raw.get("item_id") if isinstance(raw, dict) else None
        case_item = by_id.get(item_id) if isinstance(item_id, str) else None
        if case_item is None or item_id in returned:
            records.append(
                {
                    "item_id": str(item_id),
                    "checks": [CheckResult("known_item_id", "fail", False, str(item_id))],
                    "output": raw if isinstance(raw, dict) else {},
                }
            )
            continue
        returned.add(item_id)
        context = ItemContext(
            kind=case.kind,
            item_id=item_id,
            query=case_item.query_item.query,
            evidence={entry.source_id: entry.title + "\n" + entry.text for entry in case_item.evidence},
            injection_marker=case.injection_marker,
            expected_status=case.expected_status if len(case.items) == 1 else None,
        )
        checks = evaluate_item(context, raw)
        checks.append(
            CheckResult(
                "passes_production_validation",
                "fail",
                item_id in validated_ids,
                "" if item_id in validated_ids else "서버 검증에서 제외됨",
            )
        )
        records.append({"item_id": item_id, "checks": checks, "output": raw})
    for item_id in by_id:
        if item_id not in returned:
            records.append(
                {
                    "item_id": item_id,
                    "checks": [CheckResult("item_returned", "warn", False, "응답에 항목이 없음")],
                    "output": {},
                }
            )
    return records


def _run_case(
    case: HarnessCase,
    instance: Any,
    method: str,
    hook: Callable[[dict], dict] | None,
) -> list[dict[str, Any]]:
    captured: dict[str, Any] = {}
    original = instance._post_json

    def post_json(request_payload: dict) -> dict:
        if hook is not None:
            items_payload = json.loads(request_payload["contents"][0]["parts"][0]["text"])
            response = hook(items_payload)
        else:
            response = original(request_payload)
        captured["response"] = response
        return response

    instance._post_json = post_json
    queries = [item.query_item for item in case.items]
    evidence = [entry for item in case.items for entry in item.evidence]
    error: str | None = None
    validated_ids: set[str] = set()
    try:
        validated = getattr(instance, method)(queries, evidence)
        validated_ids = {entry.item_id for entry in validated}
    except Exception as exc:  # 모델 호출·검증 실패도 결과로 남긴다.
        error = f"{type(exc).__name__}: {exc}"
    raw_items: list[Any] = []
    if "response" in captured:
        try:
            content = json.loads(instance._extract_text(captured["response"]))
            raw_items = list(content.get("items", []))
        except Exception as exc:
            error = error or f"{type(exc).__name__}: {exc}"
    records = _item_results(case, raw_items, validated_ids)
    for record in records:
        record.update({"case_id": case.id, "kind": case.kind, "tags": case.tags, "error": error})
    if error and not raw_items:
        records.append(
            {
                "case_id": case.id,
                "kind": case.kind,
                "tags": case.tags,
                "item_id": "(case)",
                "checks": [CheckResult("model_responded", "fail", False, error)],
                "output": {},
                "error": error,
            }
        )
    return records


def _write_outputs(output_dir: Path, summary: HarnessSummary, records, cases) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "results.jsonl").open("w", encoding="utf-8") as file:
        for record in records:
            serializable = {
                **record,
                "checks": [check.__dict__ for check in record["checks"]],
            }
            file.write(json.dumps(serializable, ensure_ascii=False) + "\n")
    config = {
        "run_id": summary.run_id,
        "mode": summary.mode,
        "model": summary.model,
        "cases_version": "cases_v01",
        "review_prompt_version": summary.review_prompt_version,
        "review_prompt_sha256": summary.review_prompt_sha256,
        "clause_prompt_version": summary.clause_prompt_version,
        "clause_prompt_sha256": summary.clause_prompt_sha256,
        "usage": summary.usage,
    }
    (output_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        f"# 프롬프트 하네스 결과 {summary.run_id}",
        "",
        f"- 모드: {summary.mode} / 모델: {summary.model}",
        f"- 분석 프롬프트: {summary.review_prompt_version} ({summary.review_prompt_sha256})",
        f"- 특약 프롬프트: {summary.clause_prompt_version} ({summary.clause_prompt_sha256})",
        f"- 항목 반환율(쓸모 지표): {summary.returned_items}/{summary.expected_items}",
        f"- 케이스 {summary.total_cases}개 / 항목 {summary.total_items}개 / "
        f"실패 항목 {summary.failed_items}개 / 경고 항목 {summary.warned_items}개",
        f"- 사용량: {summary.usage}",
        "",
        "## 규칙별 결과",
        "",
        "| 규칙 | 평가 항목 수 | 실패 | 경고 |",
        "|---|---|---|---|",
    ]
    for name in sorted(summary.check_totals):
        lines.append(
            f"| {name} | {summary.check_totals[name]} | "
            f"{summary.check_failures.get(name, 0)} | {summary.check_warnings.get(name, 0)} |"
        )
    lines += ["", "## 실패·경고가 있는 항목", ""]
    for record in records:
        problems = [c for c in record["checks"] if not c.passed]
        if problems:
            detail = "; ".join(f"{c.severity}:{c.name}({c.detail})" for c in problems)
            lines.append(f"- {record['case_id']} / {record['item_id']}: {detail}")
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    sheet = ["# 사람 검토표", "", "자동 채점으로 못 보는 항목(쉬운 말, 원문 취지 유지, 근거 일치, 구체성)을 직접 확인합니다.", ""]
    case_by_id = {case.id: case for case in cases}
    for record in records:
        case = case_by_id.get(record["case_id"])
        if case is None or not record["output"]:
            continue
        item = next((i for i in case.items if i.query_item.item_id == record["item_id"]), None)
        sheet += [
            f"## {record['case_id']} / {record['item_id']}",
            f"- 입력: {item.query_item.query if item else ''}",
            f"- 근거: {', '.join(e.title for e in item.evidence) if item else ''}",
            "- 출력:",
            "```",
            output_text(record["kind"], record["output"]),
            "```",
            "- 평가: 쉬운 말 [ ]  원문 취지 유지 [ ]  근거와 일치 [ ]  협상·수정에 쓸 만큼 구체적 [ ]",
            "",
        ]
    (output_dir / "review_sheet.md").write_text("\n".join(sheet), encoding="utf-8")


def run_harness(
    *,
    mode: str = "fake",
    target: str = "all",
    output_dir: Path | None = None,
    model: str | None = None,
    review_prompt_file: Path | None = None,
    clause_prompt_file: Path | None = None,
    confirm_live: bool = False,
    api_key: str | None = None,
    max_calls: int = DEFAULT_MAX_LIVE_CALLS,
    post_json_override: Callable[[dict], dict] | None = None,
    cases_path: Path | None = None,
) -> HarnessSummary:
    if mode not in {"fake", "live"} or target not in {"all", "review", "clause"}:
        raise HarnessConfigError("mode는 fake|live, target은 all|review|clause여야 합니다.")
    cases = [
        case
        for case in load_cases(cases_path)
        if target == "all" or case.kind == target
    ]
    if mode == "live":
        if not confirm_live:
            raise HarnessConfigError("실제 모델 호출은 --confirm-live 가 있어야 실행합니다(유료).")
        if len(cases) > max_calls:
            raise HarnessConfigError(
                f"호출 {len(cases)}회가 한도 {max_calls}회를 넘습니다. --max-calls 로 명시하세요."
            )
        if api_key is None:
            from precedents.precedent_config import load_env_file

            load_env_file()
            api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise HarnessConfigError("GEMINI_API_KEY 가 없습니다.")

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = output_dir or DEFAULT_OUTPUT_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    usage_path = output_dir / "usage.jsonl"
    review_prompt = (
        review_prompt_file.read_text(encoding="utf-8") if review_prompt_file else review_module.SYSTEM_PROMPT
    )
    clause_prompt = (
        clause_prompt_file.read_text(encoding="utf-8") if clause_prompt_file else clause_module.SYSTEM_PROMPT
    )
    review_version = review_module.PROMPT_VERSION + ("+file" if review_prompt_file else "")
    clause_version = clause_module.PROMPT_VERSION + ("+file" if clause_prompt_file else "")
    effective_model = model or review_module.DEFAULT_GEMINI_MODEL
    summary = HarnessSummary(run_id=run_id, mode=mode, model=effective_model if mode == "live" else "fake")
    if target in {"all", "review"}:
        summary.review_prompt_version = review_version
        summary.review_prompt_sha256 = _sha256(review_prompt)
    if target in {"all", "clause"}:
        summary.clause_prompt_version = clause_version
        summary.clause_prompt_sha256 = _sha256(clause_prompt)

    records: list[dict[str, Any]] = []
    with (
        patch.object(review_module, "SYSTEM_PROMPT", review_prompt),
        patch.object(review_module, "PROMPT_VERSION", review_version),
        patch.object(clause_module, "SYSTEM_PROMPT", clause_prompt),
        patch.object(clause_module, "PROMPT_VERSION", clause_version),
    ):
        for case in cases:
            if case.kind == "review":
                instance = review_module.GeminiContractReviewAnalyzer(
                    api_key=api_key or "offline-key",
                    model=effective_model,
                    usage_manifest_path=usage_path,
                )
                method = "analyze"
            else:
                instance = clause_module.GeminiSpecialClauseGenerator(
                    api_key=api_key or "offline-key",
                    model=effective_model,
                    usage_manifest_path=usage_path,
                )
                method = "generate"
            if mode == "fake":
                hook = post_json_override or _fake_hook(case)
            else:
                hook = None
            records.extend(_run_case(case, instance, method, hook))

    summary.total_cases = len(cases)
    summary.expected_items = sum(len(case.items) for case in cases)
    summary.returned_items = sum(
        1
        for record in records
        if record["output"] and record["item_id"] != "(case)"
        and all(check.name != "known_item_id" for check in record["checks"])
    )
    for record in records:
        summary.total_items += 1
        failed = [c for c in record["checks"] if not c.passed and c.severity == "fail"]
        warned = [c for c in record["checks"] if not c.passed and c.severity == "warn"]
        summary.check_totals.update(c.name for c in record["checks"])
        summary.failed_items += bool(failed)
        summary.warned_items += bool(warned)
        summary.check_failures.update(c.name for c in failed)
        summary.check_warnings.update(c.name for c in warned)
    if usage_path.exists():
        usage_rows = [json.loads(line) for line in usage_path.read_text(encoding="utf-8").splitlines()]
        summary.usage = {
            "calls": len(usage_rows),
            "input_tokens": sum(row.get("input_tokens", 0) for row in usage_rows),
            "output_tokens": sum(row.get("output_tokens", 0) for row in usage_rows),
            "estimated_cost_usd": round(sum(row.get("estimated_cost_usd", 0) for row in usage_rows), 6),
        }
    _write_outputs(output_dir, summary, records, cases)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["fake", "live"], default="fake")
    parser.add_argument("--target", choices=["all", "review", "clause"], default="all")
    parser.add_argument("--model", default=None)
    parser.add_argument("--review-prompt-file", type=Path, default=None)
    parser.add_argument("--clause-prompt-file", type=Path, default=None)
    parser.add_argument("--confirm-live", action="store_true", help="유료 모델 호출을 허용합니다.")
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_LIVE_CALLS)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    summary = run_harness(
        mode=args.mode,
        target=args.target,
        output_dir=args.output_dir,
        model=args.model,
        review_prompt_file=args.review_prompt_file,
        clause_prompt_file=args.clause_prompt_file,
        confirm_live=args.confirm_live,
        max_calls=args.max_calls,
    )
    print(
        f"실행 {summary.run_id}: 항목 {summary.total_items}개, 실패 {summary.failed_items}개, "
        f"경고 {summary.warned_items}개 → {args.output_dir or DEFAULT_OUTPUT_ROOT / summary.run_id}"
    )


if __name__ == "__main__":
    main()
