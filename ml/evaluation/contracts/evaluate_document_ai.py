# evaluate_document_ai.py
"""
Description: 저장된 Google OCR 결과의 필드 정확도와 특약 CER을 평가한다.
Author: ooheunsu
Date: 2026-10-05
Before:
    - 사용자 제공 OCR JSON 세 개와 고정 골드셋이 있음.
After:
    - 추출 JSON, 필드별 판정, 특약 분리 진단, 입력 해시가 저장됨.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path

from ml.document_processing.contracts.document_ai_extractor import extract_document_ai
from ml.evaluation.contracts.evaluate_structured_extraction import (
    _gold_fields,
    _value_at,
)


def normalize(value):
    # Preserve decimal points, punctuation and signs in scalar fields.
    return re.sub(r"\s+", "", str(value))


def distance(a, b):
    previous = list(range(len(b) + 1))
    for i, char in enumerate(a, 1):
        current = [i]
        for j, other in enumerate(b, 1):
            current.append(
                min(previous[j] + 1, current[-1] + 1, previous[j - 1] + (char != other))
            )
        previous = current
    return previous[-1]


def evaluate(gold, prediction):
    fields = []
    for path, expected in _gold_fields(gold):
        if path.startswith(("special_terms.", "articles.")):
            continue
        actual = _value_at(prediction, path)
        exact = actual is not None and (
            actual == expected
            if isinstance(expected, (int, float, bool))
            else normalize(actual) == normalize(expected)
        )
        evaluated = path != "contract_type" and not path.endswith(
            ".signature_present"
        )
        fields.append(
            {
                "path": path,
                "expected": expected,
                "actual": actual,
                "exact": exact,
                "missing": actual is None,
                "evaluated": evaluated,
            }
        )
    terms = []
    predicted = {t["item_order"]: t for t in prediction["special_terms"]}
    for item in gold["special_terms"]:
        text = predicted.get(item["item_order"], {}).get("original_text", "")
        a, b = normalize(item["original_text"]), normalize(text)
        terms.append(
            {
                "number": item["item_order"],
                "expected": item["original_text"],
                "actual": text,
                "exact": a == b,
                "edit_distance": distance(a, b),
                "reference_characters": len(a),
                "cer": distance(a, b) / len(a),
            }
        )
    evaluated = [f for f in fields if f["evaluated"]]
    return {
        "fields": fields,
        "field_count": len(evaluated),
        "exact_count": sum(f["exact"] for f in evaluated),
        "missing_count": sum(f["missing"] for f in evaluated),
        "special_terms": terms,
        "segmentation": {
            "expected_count": len(gold["special_terms"]),
            "predicted_count": len(prediction["special_terms"]),
            "expected_numbers": [t["item_order"] for t in gold["special_terms"]],
            "predicted_numbers": [t["item_order"] for t in prediction["special_terms"]],
            "note": "Count/number agreement does not prove correct boundaries; inspect per-clause text.",
        },
        "articles": [
            {
                "expected": a["original_text"],
                "actual": _value_at(prediction, f"articles.{i}"),
            }
            for i, a in enumerate(gold.get("articles", []))
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    for name in ("jeonse", "monthly", "sale"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument(
        "--gold-dir", type=Path, default=Path("data/contracts/evaluation/goldset_v01")
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    documents = []
    for name, kind, stem in (
        ("jeonse", "jeonse", "jeonse_handwritten_01"),
        ("monthly", "monthly_rent", "monthly_rent_handwritten_01"),
        ("sale", "sale", "sale_handwritten_01"),
    ):
        path = getattr(args, name)
        raw = path.read_bytes()
        # Extraction precedes loading the gold; no answer-dependent corrections.
        prediction = extract_document_ai(json.loads(raw), kind).model_dump(
            mode="json", exclude_none=True
        )
        (args.output_dir / f"{stem}_extracted.json").write_text(
            json.dumps(prediction, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        gold_bytes = (args.gold_dir / f"{stem}_gold.json").read_bytes()
        result = evaluate(json.loads(gold_bytes), prediction)
        result.update(
            name=name,
            input_sha256=hashlib.sha256(raw).hexdigest(),
            gold_sha256=hashlib.sha256(gold_bytes).hexdigest(),
            warnings=prediction["warnings"],
        )
        documents.append(result)
    report = {
        "version": "document-ai-layout-baseline.v0.2",
        "ocr_elapsed_seconds": None,
        "timing_note": "Uploaded Document JSON has no request timing; file timestamps are not OCR latency.",
        "provider": "Google Document AI",
        "processor_version": "user-reported pretrained-ocr-v2.1-2024-08-07; not embedded in input",
        "limitations": [
            "Three development samples, not held-out validation",
            "No external calls",
            "Contract type supplied by caller and excluded from accuracy",
            "Unmarked checkbox states and signatures remain unknown; no inference from label presence",
            "Unnumbered clause fragments preserved in warnings, not assigned a guessed clause number",
        ],
        "documents": documents,
    }
    (args.output_dir / "evaluation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = [
        "# Document AI layout baseline",
        "",
        "Strict scalar match; whitespace only normalized. No fuzzy credit for amounts/dates.",
        "",
        "| Document | Correct | Fields | Missing | Term CER |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for doc in documents:
        cer = sum(t["edit_distance"] for t in doc["special_terms"]) / sum(
            t["reference_characters"] for t in doc["special_terms"]
        )
        summary.append(
            f"| {doc['name']} | {doc['exact_count']} | {doc['field_count']} | {doc['missing_count']} | {cer:.1%} |"
        )
        print(
            doc["name"],
            doc["exact_count"],
            "/",
            doc["field_count"],
            "missing",
            doc["missing_count"],
            "CER",
            round(cer, 4),
        )
    summary.extend(
        [
            "",
            "All fields require review. CER measures edits, not legal meaning or safety.",
            "Contract type excluded. Missing unsupported fields included. See JSON for every failure.",
        ]
    )
    (args.output_dir / "evaluation_summary.md").write_text(
        "\n".join(summary), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
