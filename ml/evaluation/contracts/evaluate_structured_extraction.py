# evaluate_structured_extraction.py
"""
Description: OCR 기반 계약 추출 JSON을 고정 골드셋과 필드별로 비교해
정확 일치·유사 일치·누락률 보고서를 생성한다.
Author: ooheunsu
Date: 2026-10-05
Before:
    - OCR 결과 JSON과 동일 원본에 대한 확정 골드 JSON이 준비된 상태.
After:
    - 문서별 추출 JSON과 필드별 정확도 보고서가 출력됨.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from ml.document_processing.contracts.ocr_contract_extractor import (
    extract_contract_from_ocr,
)


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value)).lower()
    return re.sub(r"[^0-9a-z가-힣]", "", text)


def _gold_fields(payload: Any, path: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(payload, dict):
        if "value" in payload and "status" in payload:
            if payload.get("value") is not None:
                yield path, payload["value"]
            return
        if "original_text" in payload and "source" in payload:
            yield path, payload["original_text"]
            return
        for key, value in payload.items():
            if key in {"schema_version", "document_type", "warnings"}:
                continue
            child = f"{path}.{key}" if path else key
            yield from _gold_fields(value, child)
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            yield from _gold_fields(value, f"{path}.{index}")


def _value_at(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if isinstance(current, list):
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
        elif isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        else:
            return None
    if isinstance(current, dict) and (
        "value" in current or "status" in current
    ):
        return current.get("value")
    if isinstance(current, dict) and "original_text" in current:
        return current.get("original_text")
    return current


def _category(path: str) -> str:
    root = path.split(".", maxsplit=1)[0]
    if root.endswith("date") or root in {"start_date", "end_date"}:
        return "dates"
    return root


def _evaluate(
    gold: dict[str, Any],
    predicted: dict[str, Any],
) -> list[dict[str, Any]]:
    results = []
    for path, expected in _gold_fields(gold):
        actual = _value_at(predicted, path)
        expected_normalized = _normalize(expected)
        actual_normalized = _normalize(actual) if actual is not None else ""
        exact = actual is not None and expected_normalized == actual_normalized
        similarity = (
            SequenceMatcher(
                None,
                expected_normalized,
                actual_normalized,
                autojunk=False,
            ).ratio()
            if actual_normalized
            else 0.0
        )
        threshold = 0.85 if len(expected_normalized) >= 10 else 0.9
        results.append(
            {
                "path": path,
                "category": _category(path),
                "expected": expected,
                "actual": actual,
                "exact_match": exact,
                "similarity": round(similarity, 4),
                "fuzzy_match": exact or similarity >= threshold,
                "missing": actual is None,
            }
        )
    return results


def _summary(fields: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(fields)
    exact = sum(field["exact_match"] for field in fields)
    fuzzy = sum(field["fuzzy_match"] for field in fields)
    missing = sum(field["missing"] for field in fields)
    return {
        "field_count": total,
        "exact_count": exact,
        "exact_rate": round(exact / total, 4) if total else None,
        "fuzzy_count": fuzzy,
        "fuzzy_rate": round(fuzzy / total, 4) if total else None,
        "missing_count": missing,
        "missing_rate": round(missing / total, 4) if total else None,
    }


def _aggregate(fields: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for field in fields:
        grouped[field["category"]].append(field)
    return {
        "overall": _summary(fields),
        "by_category": {
            category: _summary(values)
            for category, values in sorted(grouped.items())
        },
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 손글씨 계약서 구조화 추출 평가",
        "",
        "- 입력: 고정된 PP-OCRv5 Mobile OCR 결과",
        "- 추출: 골드값을 참조하지 않는 양식·라벨·좌표 규칙",
        "- exact: 추출 JSON 값과 골드 JSON 값의 정규화 완전 일치",
        "- fuzzy: 긴 문자열 0.85, 짧은 문자열 0.90 이상 유사도",
        "",
    ]
    for document in report["documents"]:
        summary = document["metrics"]["overall"]
        lines.extend(
            [
                f"## {document['source_filename']}",
                "",
                f"- 평가 필드: {summary['field_count']}개",
                f"- 완전 일치: {summary['exact_count']}개 "
                f"({summary['exact_rate']:.1%})",
                f"- 유사 일치: {summary['fuzzy_count']}개 "
                f"({summary['fuzzy_rate']:.1%})",
                f"- 누락: {summary['missing_count']}개 "
                f"({summary['missing_rate']:.1%})",
                "",
                "| 범주 | 필드 | 완전 일치 | 유사 일치 | 누락 |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for category, metrics in document["metrics"]["by_category"].items():
            lines.append(
                f"| {category} | {metrics['field_count']} | "
                f"{metrics['exact_rate']:.1%} | {metrics['fuzzy_rate']:.1%} | "
                f"{metrics['missing_rate']:.1%} |"
            )
        lines.extend(["", "실패 필드:"])
        lines.extend(
            f"- `{field['path']}`: 기대 `{field['expected']}` / "
            f"추출 `{field['actual']}`"
            for field in document["fields"]
            if not field["fuzzy_match"]
        )
        lines.append("")
    overall = report["metrics"]["overall"]
    lines.extend(
        [
            "## 전체",
            "",
            f"- 평가 필드: {overall['field_count']}개",
            f"- 완전 일치: {overall['exact_count']}개 "
            f"({overall['exact_rate']:.1%})",
            f"- 유사 일치: {overall['fuzzy_count']}개 "
            f"({overall['fuzzy_rate']:.1%})",
            f"- 누락: {overall['missing_count']}개 "
            f"({overall['missing_rate']:.1%})",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ocr-dir", required=True, type=Path)
    parser.add_argument("--goldset-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(
        (args.goldset_dir / "manifest.json").read_text(encoding="utf-8")
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)

    documents = []
    all_fields = []
    for item in manifest["documents"]:
        stem = Path(item["source_filename"]).stem
        ocr_path = args.ocr_dir / f"{stem}_ocr.json"
        ocr_document = json.loads(ocr_path.read_text(encoding="utf-8"))
        extraction = extract_contract_from_ocr(ocr_document)
        predicted = extraction.model_dump(mode="json", exclude_none=True)
        gold = json.loads(
            (args.goldset_dir / item["gold_filename"]).read_text(encoding="utf-8")
        )
        fields = _evaluate(gold, predicted)
        all_fields.extend(fields)
        result = {
            "source_filename": item["source_filename"],
            "gold_filename": item["gold_filename"],
            "metrics": _aggregate(fields),
            "fields": fields,
        }
        documents.append(result)
        (args.output_dir / f"{stem}_extracted.json").write_text(
            json.dumps(predicted, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    report = {
        "experiment_version": "handwritten-structured-extraction.v0.1",
        "dataset_version": manifest["dataset_version"],
        "ocr_engine": "PP-OCRv5 Mobile",
        "extractor": "standard-form-layout-rules.v0.1",
        "metrics": _aggregate(all_fields),
        "documents": documents,
    }
    (args.output_dir / "evaluation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_markdown(args.output_dir / "evaluation_summary.md", report)
    print(json.dumps(report["metrics"]["overall"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
