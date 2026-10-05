# evaluate_handwritten_ocr.py
"""
Description: 손글씨 계약서 PDF를 로컬 PP-OCRv5 Mobile로 인식하고,
사람이 확정한 필드별 정답과 정규화·유사도 기준으로 비교한다.
Author: ooheunsu
Date: 2026-10-05
Before:
    - 손글씨 계약서 PDF와 manifest로 연결된 정답 JSON이 준비된 상태.
After:
    - 원시 OCR 결과와 필드별 일치 결과 및 집계 보고서가 출력됨.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import types
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from ml.document_processing.contracts.privacy import (
    mask_resident_registration_numbers,
    redact_resident_number_rows,
)


LAYOUT_ONLY_PATHS = (
    "contract_type",
    "disclosures.",
    "signature_present",
)


def _disable_unused_modelscope_downloads() -> None:
    modelscope = types.ModuleType("modelscope")

    def disabled_download(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("ModelScope download is disabled for this evaluation.")

    modelscope.snapshot_download = disabled_download  # type: ignore[attr-defined]
    sys.modules["modelscope"] = modelscope


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value)).lower()
    return re.sub(r"[^0-9a-z가-힣]", "", text)


def _category(path: str) -> str:
    if path.startswith("property."):
        return "property"
    if path.startswith("parties."):
        return "parties"
    if path.startswith("payment."):
        return "payment"
    if path.startswith("special_terms."):
        return "special_terms"
    if path.startswith("articles."):
        return "articles"
    if path.startswith("brokers."):
        return "brokers"
    if path in {
        "contract_date",
        "start_date",
        "end_date",
        "delivery_date",
        "ownership_transfer_date",
    }:
        return "dates"
    return "other"


def _field_targets(payload: Any, path: str = "") -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        if "value" in payload and "status" in payload:
            value = payload.get("value")
            source = payload.get("source")
            if value is None or isinstance(value, bool) or not source:
                return
            candidates = [payload.get("raw_text"), value]
            normalized = list(
                dict.fromkeys(
                    candidate
                    for item in candidates
                    if item is not None and len(candidate := _normalize(item)) >= 2
                )
            )
            if normalized:
                yield {
                    "path": path,
                    "category": _category(path),
                    "page_number": source["page_number"],
                    "expected": value,
                    "raw_text": payload.get("raw_text"),
                    "candidates": normalized,
                    "layout_only": any(token in path for token in LAYOUT_ONLY_PATHS),
                }
            return
        if "original_text" in payload and "source" in payload:
            normalized = _normalize(payload["original_text"])
            if normalized:
                yield {
                    "path": path,
                    "category": _category(path),
                    "page_number": payload["source"]["page_number"],
                    "expected": payload["original_text"],
                    "raw_text": payload["original_text"],
                    "candidates": [normalized],
                    "layout_only": False,
                }
            return
        for key, value in payload.items():
            child_path = f"{path}.{key}" if path else key
            yield from _field_targets(value, child_path)
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            yield from _field_targets(value, f"{path}.{index}")


def _line_windows(lines: list[str], max_window: int = 3) -> list[str]:
    normalized = [_normalize(line) for line in lines if _normalize(line)]
    windows: list[str] = []
    for size in range(1, max_window + 1):
        windows.extend(
            "".join(normalized[start : start + size])
            for start in range(0, len(normalized) - size + 1)
        )
    return windows


def _best_similarity(targets: list[str], windows: list[str]) -> float:
    if not targets or not windows:
        return 0.0
    return max(
        SequenceMatcher(None, target, window, autojunk=False).ratio()
        for target in targets
        for window in windows
    )


def _fuzzy_threshold(targets: list[str]) -> float:
    longest = max(len(target) for target in targets)
    if longest >= 30:
        return 0.8
    if longest >= 10:
        return 0.85
    return 0.9


def _evaluate_document(
    gold: dict[str, Any],
    pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    page_lines = {
        page["page_number"]: [line["text"] for line in page["recognized_lines"]]
        for page in pages
    }
    page_windows = {
        page_number: _line_windows(lines)
        for page_number, lines in page_lines.items()
    }
    page_text = {
        page_number: "".join(_normalize(line) for line in lines)
        for page_number, lines in page_lines.items()
    }
    evaluations: list[dict[str, Any]] = []
    seen: set[tuple[int, tuple[str, ...]]] = set()

    for target in _field_targets(gold):
        dedupe_key = (target["page_number"], tuple(target["candidates"]))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        recognized_text = page_text.get(target["page_number"], "")
        exact = any(candidate in recognized_text for candidate in target["candidates"])
        similarity = _best_similarity(
            target["candidates"],
            page_windows.get(target["page_number"], []),
        )
        threshold = _fuzzy_threshold(target["candidates"])
        evaluations.append(
            {
                **{key: value for key, value in target.items() if key != "candidates"},
                "normalized_candidates": target["candidates"],
                "normalized_exact_match": exact,
                "best_similarity": round(similarity, 4),
                "fuzzy_threshold": threshold,
                "fuzzy_match": exact or similarity >= threshold,
            }
        )
    return evaluations


def _aggregate(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    text_fields = [field for field in evaluations if not field["layout_only"]]
    layout_fields = [field for field in evaluations if field["layout_only"]]

    def summarize(fields: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(fields)
        exact = sum(field["normalized_exact_match"] for field in fields)
        fuzzy = sum(field["fuzzy_match"] for field in fields)
        return {
            "field_count": total,
            "normalized_exact_count": exact,
            "normalized_exact_rate": round(exact / total, 4) if total else None,
            "fuzzy_match_count": fuzzy,
            "fuzzy_match_rate": round(fuzzy / total, 4) if total else None,
        }

    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for field in text_fields:
        by_category[field["category"]].append(field)
    return {
        "text_recognition": summarize(text_fields),
        "layout_only_field_count": len(layout_fields),
        "by_category": {
            category: summarize(fields)
            for category, fields in sorted(by_category.items())
        },
    }


def _write_summary(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 손글씨 계약서 OCR 필드 비교",
        "",
        "- 모델: PP-OCRv5 Mobile detection + Korean Mobile recognition",
        "- 장치: CPU",
        "- 개인정보 포함 원문은 외부 서비스로 전송하지 않음",
        "- exact: 공백·문장부호를 제거한 정답 문자열이 OCR 결과에 존재",
        "- fuzzy: 필드 길이에 따라 0.80~0.90 이상의 문자열 유사도",
        "- 체크박스·불리언 필드는 텍스트 OCR 집계에서 제외",
        "",
    ]
    for document in report["documents"]:
        metrics = document["metrics"]["text_recognition"]
        lines.extend(
            [
                f"## {document['source_filename']}",
                "",
                f"- 페이지: {document['page_count']}",
                f"- OCR 시간: {document['inference_seconds']:.3f}초",
                f"- 평가 필드: {metrics['field_count']}개",
                f"- 정규화 완전 일치: {metrics['normalized_exact_count']}개 "
                f"({metrics['normalized_exact_rate']:.1%})",
                f"- 유사 일치: {metrics['fuzzy_match_count']}개 "
                f"({metrics['fuzzy_match_rate']:.1%})",
                "",
            ]
        )
        lines.extend(
            [
                "| 필드 범주 | 평가 수 | 완전 일치 | 유사 일치 |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for category, category_metrics in document["metrics"][
            "by_category"
        ].items():
            lines.append(
                f"| {category} | {category_metrics['field_count']} | "
                f"{category_metrics['normalized_exact_rate']:.1%} | "
                f"{category_metrics['fuzzy_match_rate']:.1%} |"
            )
        failed_fields = [
            field
            for field in document["fields"]
            if not field["layout_only"] and not field["fuzzy_match"]
        ]
        lines.extend(["", "유사 일치 기준 실패 필드:"])
        lines.extend(
            f"- `{field['path']}`: `{field['expected']}` "
            f"(유사도 {field['best_similarity']:.3f})"
            for field in failed_fields
        )
        lines.append("")
    overall = report["overall"]
    lines.extend(
        [
            "## 전체",
            "",
            f"- 평가 필드: {overall['field_count']}개",
            f"- 정규화 완전 일치: {overall['normalized_exact_count']}개 "
            f"({overall['normalized_exact_rate']:.1%})",
            f"- 유사 일치: {overall['fuzzy_match_count']}개 "
            f"({overall['fuzzy_match_rate']:.1%})",
            "",
            "이 수치는 OCR 텍스트 안에 정답 필드가 인식되었는지를 측정한 기준선이다. "
            "필드 위치 지정과 JSON 구조화 정확도는 후속 추출기 평가에서 별도로 측정한다.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--goldset-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--limit-side-len", type=int, default=1600)
    args = parser.parse_args()

    manifest = json.loads(
        (args.goldset_dir / "manifest.json").read_text(encoding="utf-8")
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)

    _disable_unused_modelscope_downloads()
    import numpy as np
    from paddleocr import PaddleOCR
    from pypdf import PdfReader

    initialization_started = time.perf_counter()
    ocr = PaddleOCR(
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name="korean_PP-OCRv5_mobile_rec",
        device="cpu",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_det_limit_side_len=args.limit_side_len,
        text_det_limit_type="max",
        text_rec_score_thresh=0.3,
    )
    initialization_seconds = time.perf_counter() - initialization_started

    documents: list[dict[str, Any]] = []
    all_text_evaluations: list[dict[str, Any]] = []
    for document in manifest["documents"]:
        source_path = args.source_dir / document["source_filename"]
        actual_hash = _sha256(source_path)
        if actual_hash != document["source_sha256"]:
            raise ValueError(f"원본 해시 불일치: {source_path.name}")

        reader = PdfReader(source_path, strict=True)
        if len(reader.pages) != document["page_count"]:
            raise ValueError(f"페이지 수 불일치: {source_path.name}")

        pages: list[dict[str, Any]] = []
        document_started = time.perf_counter()
        for page_number, page in enumerate(reader.pages, start=1):
            if len(page.images) != 1:
                raise ValueError(
                    f"스캔 이미지가 한 장이 아닌 페이지: {source_path.name} {page_number}"
                )
            image = np.asarray(page.images[0].image.convert("RGB"))
            page_started = time.perf_counter()
            result = list(ocr.predict(image))[0].json["res"]
            inference_seconds = time.perf_counter() - page_started
            recognized_lines = [
                {
                    "text": mask_resident_registration_numbers(text),
                    "confidence": round(float(score), 6),
                    "box": box.tolist() if hasattr(box, "tolist") else box,
                }
                for text, score, box in zip(
                    result.get("rec_texts", []),
                    result.get("rec_scores", []),
                    result.get("rec_boxes", []),
                    strict=True,
                )
            ]
            recognized_lines = redact_resident_number_rows(recognized_lines)
            pages.append(
                {
                    "page_number": page_number,
                    "inference_seconds": round(inference_seconds, 3),
                    "recognized_line_count": len(recognized_lines),
                    "recognized_lines": recognized_lines,
                }
            )
            print(
                f"{source_path.name} {page_number}/{len(reader.pages)} "
                f"{inference_seconds:.1f}s {len(recognized_lines)} lines",
                flush=True,
            )

        total_inference_seconds = time.perf_counter() - document_started
        gold = json.loads(
            (args.goldset_dir / document["gold_filename"]).read_text(encoding="utf-8")
        )
        evaluations = _evaluate_document(gold, pages)
        metrics = _aggregate(evaluations)
        all_text_evaluations.extend(
            field for field in evaluations if not field["layout_only"]
        )
        document_result = {
            "source_filename": source_path.name,
            "source_sha256": actual_hash,
            "gold_filename": document["gold_filename"],
            "page_count": len(pages),
            "inference_seconds": round(total_inference_seconds, 3),
            "metrics": metrics,
            "fields": evaluations,
            "pages": pages,
        }
        documents.append(document_result)
        (args.output_dir / f"{source_path.stem}_ocr.json").write_text(
            json.dumps(document_result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    overall = _aggregate(all_text_evaluations)["text_recognition"]
    report = {
        "experiment_version": "handwritten-ppocrv5-mobile.v0.1",
        "dataset_version": manifest["dataset_version"],
        "engine": "PP-OCRv5",
        "detection_model": "PP-OCRv5_mobile_det",
        "recognition_model": "korean_PP-OCRv5_mobile_rec",
        "device": "cpu",
        "limit_side_len": args.limit_side_len,
        "initialization_seconds": round(initialization_seconds, 3),
        "overall": overall,
        "documents": documents,
    }
    (args.output_dir / "evaluation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_summary(args.output_dir / "evaluation_summary.md", report)
    print(json.dumps({"overall": overall}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
