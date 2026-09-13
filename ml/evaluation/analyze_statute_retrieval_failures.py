# analyze_statute_retrieval_failures.py
"""
Description: 법령 검색 평가 결과를 질문과 정답 청크 단위로 분해해 공통
누락, 모델별 누락, 낮은 순위 및 라벨 민감도를 분석한다.
Author: ooheunsu
Date: 2026-09-02
Before:
    - 두 라벨 정책의 지표와 모델별 Top-10 순위가 생성된 상태.

After:
    - 질문별 검색 실패 원인 JSON과 검토용 Markdown 보고서가 생성.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ml.evaluation.evaluate_statute_label_sensitivity import (
    DEFAULT_ADJUDICATION,
    DEFAULT_GOLD,
    DEFAULT_RANKINGS,
    DEFAULT_RUN_MANIFEST,
    POSITIVE_THRESHOLD,
    build_label_policies,
    read_json,
    read_jsonl,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_ROOT = PROJECT_ROOT / "data/statutes/evaluation"
DEFAULT_CHUNK_ROOT = PROJECT_ROOT / "data/statutes/chunks"
DEFAULT_METRICS = (
    EVALUATION_ROOT
    / "runs/pilot-v01-initial/metrics_label_sensitivity.json"
)
DEFAULT_OUTPUT = (
    EVALUATION_ROOT / "runs/pilot-v01-initial/failure_analysis.json"
)
DEFAULT_REPORT = (
    EVALUATION_ROOT / "runs/pilot-v01-initial/failure_analysis.md"
)


def load_chunk_metadata(chunk_root: Path) -> dict[str, dict[str, str]]:
    result = {}
    for path in sorted(chunk_root.glob("*.json")):
        document = read_json(path)
        for chunk in document["chunks"]:
            metadata = chunk["metadata"]
            result[chunk["chunk_id"]] = {
                "law_name": metadata["law_name"],
                "article_label": metadata["article_label"],
                "article_title": metadata["article_title"],
            }
    return result


def chunk_summary(
    chunk_id: str,
    relevance: int,
    metadata: dict[str, dict[str, str]],
) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "relevance": relevance,
        **metadata[chunk_id],
    }


def analyze_policy(
    labels: dict[str, dict[str, int]],
    rankings: dict[str, dict[str, list[dict[str, Any]]]],
    cases: dict[str, dict[str, Any]],
    metadata: dict[str, dict[str, str]],
) -> dict[str, Any]:
    model_keys = list(rankings)
    questions = []
    shared_missing_counter: Counter[str] = Counter()
    model_specific_counter: Counter[str] = Counter()
    late_top_10_counter: Counter[str] = Counter()
    for query_id in sorted(labels):
        relevant = {
            chunk_id: relevance
            for chunk_id, relevance in labels[query_id].items()
            if relevance >= POSITIVE_THRESHOLD
        }
        rank_lookup = {
            model: {
                row["chunk_id"]: int(row["rank"])
                for row in rankings[model][query_id]
            }
            for model in model_keys
        }
        shared_missing_ids = {
            chunk_id
            for chunk_id in relevant
            if all(chunk_id not in rank_lookup[model] for model in model_keys)
        }
        model_results = {}
        for model in model_keys:
            retrieved = set(rank_lookup[model])
            missed_ids = set(relevant) - retrieved
            model_specific_ids = missed_ids - shared_missing_ids
            late_ids = {
                chunk_id
                for chunk_id, rank in rank_lookup[model].items()
                if chunk_id in relevant and 6 <= rank <= 10
            }
            shared_missing_counter[model] += len(shared_missing_ids)
            model_specific_counter[model] += len(model_specific_ids)
            late_top_10_counter[model] += len(late_ids)
            model_results[model] = {
                "relevant_count": len(relevant),
                "recall_at_5": sum(
                    rank_lookup[model].get(chunk_id, 11) <= 5
                    for chunk_id in relevant
                )
                / len(relevant),
                "recall_at_10": (len(relevant) - len(missed_ids))
                / len(relevant),
                "shared_missing": [
                    chunk_summary(chunk_id, relevant[chunk_id], metadata)
                    for chunk_id in sorted(shared_missing_ids)
                ],
                "model_specific_missing": [
                    {
                        **chunk_summary(chunk_id, relevant[chunk_id], metadata),
                        "retrieved_by": [
                            other
                            for other in model_keys
                            if chunk_id in rank_lookup[other]
                        ],
                    }
                    for chunk_id in sorted(model_specific_ids)
                ],
                "retrieved_at_6_to_10": [
                    {
                        **chunk_summary(chunk_id, relevant[chunk_id], metadata),
                        "rank": rank_lookup[model][chunk_id],
                    }
                    for chunk_id in sorted(
                        late_ids, key=lambda item: rank_lookup[model][item]
                    )
                ],
            }
        questions.append(
            {
                "query_id": query_id,
                "question": cases[query_id]["question"],
                "critical": bool(cases[query_id]["critical"]),
                "positive_chunk_count": len(relevant),
                "shared_missing": [
                    chunk_summary(chunk_id, relevant[chunk_id], metadata)
                    for chunk_id in sorted(shared_missing_ids)
                ],
                "models": model_results,
            }
        )
    return {
        "summary": {
            "shared_missing_assignments_by_model": dict(shared_missing_counter),
            "model_specific_missing_by_model": dict(model_specific_counter),
            "retrieved_at_6_to_10_by_model": dict(late_top_10_counter),
            "questions_with_shared_missing": sum(
                bool(question["shared_missing"]) for question in questions
            ),
        },
        "questions": questions,
    }


def analyze(
    gold_cases: list[dict[str, Any]],
    adjudication_rows: list[dict[str, Any]],
    rankings: dict[str, dict[str, list[dict[str, Any]]]],
    metadata: dict[str, dict[str, str]],
) -> dict[str, Any]:
    policies = build_label_policies(gold_cases, adjudication_rows)
    cases = {case["query_id"]: case for case in gold_cases}
    majority = policies["majority"]
    independent = policies["independent"]
    evidence_reviewed = {
        (row["query_id"], decision["chunk_id"])
        for row in adjudication_rows
        for decision in row["decisions"]
    }
    label_sensitive = []
    positive_not_evidence_rechecked = []
    for query_id in sorted(cases):
        all_chunk_ids = set(majority[query_id]) | set(independent[query_id])
        for chunk_id in sorted(all_chunk_ids):
            majority_score = majority[query_id].get(chunk_id, 0)
            independent_score = independent[query_id].get(chunk_id, 0)
            if (
                majority_score >= POSITIVE_THRESHOLD
                and (query_id, chunk_id) not in evidence_reviewed
            ):
                positive_not_evidence_rechecked.append(
                    {
                        "query_id": query_id,
                        **chunk_summary(chunk_id, majority_score, metadata),
                    }
                )
            if (majority_score >= POSITIVE_THRESHOLD) == (
                independent_score >= POSITIVE_THRESHOLD
            ):
                continue
            label_sensitive.append(
                {
                    "query_id": query_id,
                    **chunk_summary(chunk_id, majority_score, metadata),
                    "majority_relevance": majority_score,
                    "independent_relevance": independent_score,
                }
            )
    return {
        "question_count": len(gold_cases),
        "critical_question_count": sum(
            bool(case["critical"]) for case in gold_cases
        ),
        "label_sensitive_positive_count": len(label_sensitive),
        "label_sensitive_positive_chunks": label_sensitive,
        "positive_not_evidence_rechecked_count": len(
            positive_not_evidence_rechecked
        ),
        "positive_not_evidence_rechecked": positive_not_evidence_rechecked,
        "policies": {
            name: analyze_policy(labels, rankings, cases, metadata)
            for name, labels in policies.items()
        },
    }


def display_chunk(chunk: dict[str, Any]) -> str:
    title = f"({chunk['article_title']})" if chunk["article_title"] else ""
    return (
        f"{chunk['law_name']} {chunk['article_label']}{title}"
        f" [{chunk['chunk_id']}]"
    )


def render_report(
    analysis: dict[str, Any],
    metrics: dict[str, Any],
    display_names: dict[str, str],
) -> str:
    lines = [
        "# 법령 검색 11문항 실패 분석",
        "",
        "## 핵심 결과",
        "",
        f"- 치명 문항: {analysis['critical_question_count']}개",
        "- 라벨 정책에 따라 정답 여부가 바뀌는 청크: "
        f"{analysis['label_sensitive_positive_count']}개",
        "- 사람·초기 AI 일치로 최종 근거 재검토에서 제외된 양성 청크: "
        f"{analysis['positive_not_evidence_rechecked_count']}개",
    ]
    for policy_name, title in (
        ("majority", "다수결 잠정 라벨"),
        ("independent", "법령 독립 재검토 라벨"),
    ):
        summary = analysis["policies"][policy_name]["summary"]
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                "| 모델 | 공통 누락 | 모델 고유 누락 | 6~10위 정답 |",
                "|---|---:|---:|---:|",
            ]
        )
        for model in metrics["policies"][policy_name]["model_order"]:
            lines.append(
                f"| {display_names.get(model, model)} | "
                f"{summary['shared_missing_assignments_by_model'][model]} | "
                f"{summary['model_specific_missing_by_model'][model]} | "
                f"{summary['retrieved_at_6_to_10_by_model'][model]} |"
            )

        lines.extend(["", "### 치명 문항", ""])
        for question in analysis["policies"][policy_name]["questions"]:
            if not question["critical"]:
                continue
            lines.append(
                f"#### {question['query_id'][-4:].upper()} — {question['question']}"
            )
            if question["shared_missing"]:
                lines.append(
                    "- 모든 모델 공통 누락: "
                    + "; ".join(
                        display_chunk(chunk)
                        for chunk in question["shared_missing"]
                    )
                )
            else:
                lines.append("- 모든 모델 공통 누락: 없음")
            for model, result in question["models"].items():
                specific = result["model_specific_missing"]
                specific_text = (
                    "; ".join(display_chunk(chunk) for chunk in specific)
                    if specific
                    else "없음"
                )
                lines.append(
                    f"- {display_names.get(model, model)}: "
                    f"Recall@10 {result['recall_at_10'] * 100:.1f}%, "
                    f"고유 누락 {specific_text}"
                )
            lines.append("")

    pending = analysis["positive_not_evidence_rechecked_count"]
    if pending:
        positive_review_status = (
            f"1. 최종 근거 재검토에서 제외된 양성 {pending}건을 먼저 "
            "재판정한다."
        )
    else:
        positive_review_status = (
            "1. 양성 청크 근거 재검토가 완료됐으며 미재검토 양성은 0건이다. "
            "독립 라벨은 사람의 최종 승인 전까지 draft로 유지한다."
        )
    lines.extend(
        [
            "## 해석과 다음 조치",
            "",
            positive_review_status,
            "2. 골드 라벨 정리 후에도 남는 모든 모델 공통 누락은 특정 "
            "임베딩 모델의 단독 문제로 보기 어렵다. 질문 확장, 키워드·"
            "하이브리드 검색 또는 후보 수 조정을 검토한다.",
            "3. 모델 고유 누락은 같은 정답을 다른 모델이 찾았으므로 임베딩 "
            "모델 간 판별력 차이로 해석할 수 있다.",
            "4. 6~10위 정답은 검색 실패가 아니라 순위 품질 문제다. 향후 "
            "리랭커 실험 대상으로 분리한다.",
            "5. 라벨 민감 청크는 현재 모델 순위를 바꾸지 않았다. 50문항 "
            "확장 전 관련도 1과 2의 경계 사례로 보존한다.",
            "6. 이 결과는 11문항 파일럿 분석이며 최종 모델 선정 근거로 "
            "단독 사용하지 않는다.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="법령 검색 실패를 분석합니다.")
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument(
        "--adjudication", type=Path, default=DEFAULT_ADJUDICATION
    )
    parser.add_argument("--rankings", type=Path, default=DEFAULT_RANKINGS)
    parser.add_argument("--metrics", type=Path, default=DEFAULT_METRICS)
    parser.add_argument(
        "--run-manifest", type=Path, default=DEFAULT_RUN_MANIFEST
    )
    parser.add_argument("--chunk-root", type=Path, default=DEFAULT_CHUNK_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_manifest = read_json(args.run_manifest.resolve())
    analysis = analyze(
        read_jsonl(args.gold.resolve()),
        read_jsonl(args.adjudication.resolve()),
        read_json(args.rankings.resolve()),
        load_chunk_metadata(args.chunk_root.resolve()),
    )
    metrics = read_json(args.metrics.resolve())
    display_names = {
        model["key"]: model["display_name"]
        for model in run_manifest["models"]
    }
    output_path = args.output.resolve()
    report_path = args.report.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(
        render_report(analysis, metrics, display_names),
        encoding="utf-8",
    )
    print(report_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
