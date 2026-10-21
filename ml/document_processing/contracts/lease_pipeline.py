# lease_pipeline.py
"""
Description: 표준 임대차·매매 합성자료의 저장된 OCR 결과를 재실행하고 확인 항목과 시간을 기록한다.
Author: ooheunsu
Date: 2026-10-06
Before:
    - Google OCR JSON이 준비되어 있으나 단계별 실행 진입점이 없음.
After:
    - 지원 양식 검사, 추출 결과, 확인 항목, 실행 기록이 저장됨.
"""

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from ml.document_processing.contracts.document_ai_extractor import (
    extract_document_ai,
    load_lines,
)


class UnsupportedLeaseInput(ValueError):
    """Input is not a supported three-page standard contract OCR export."""


def validate_template(document, contract_type="jeonse"):
    if not isinstance(document, dict) or not isinstance(document.get("text"), str):
        raise UnsupportedLeaseInput("OCR text/pages 구조가 필요합니다.")
    pages = document.get("pages")
    if not isinstance(pages, list) or len(pages) != 3:
        raise UnsupportedLeaseInput("현재는 지원 표준계약서 본문 1~3페이지가 필요합니다.")
    for index, page in enumerate(pages, 1):
        if int(page.get("pageNumber", index)) != index:
            raise UnsupportedLeaseInput("페이지 순서 또는 번호가 올바르지 않습니다.")
        if not page.get("lines"):
            raise UnsupportedLeaseInput("페이지별 OCR 줄과 좌표가 필요합니다.")
    lines = load_lines(document)
    per_page = {
        n: re.sub(r"\s+", "", " ".join(line.text for line in lines if line.page == n))
        for n in range(1, 4)
    }
    if contract_type == "sale":
        required = {
            1: ("부동산매매계약서", "제1조", "제2조"),
            2: ("제3조", "제6조"),
            3: ("제7조", "매도인", "매수인"),
        }
        template = "court-general-sale-three-page"
    else:
        required = {
            1: ("주택임대차표준계약서", "제1조", "제2조"),
            2: ("제4조", "제12조"),
            3: ("계약을증명", "임대인", "임차인"),
        }
        template = "moj-standard-lease-three-page"
    if any(
        marker not in per_page[n]
        for n, markers in required.items()
        for marker in markers
    ):
        raise UnsupportedLeaseInput(
            "지원 양식을 확인할 수 없습니다. 페이지 누락/순서/인식 결과를 확인하세요."
        )
    return {
        "template": template,
        "recognition": "anchor_heuristic",
        "page_count": 3,
        "note": "표제 검사이며 양식 진위 또는 정확한 개정판 검증이 아님",
    }


def review_fields(payload, path=""):
    if isinstance(payload, dict):
        if "status" in payload and "value" in payload:
            if payload["status"] == "ambiguous":
                reason = "confirm_ambiguous_value"
            elif payload["value"] is None:
                reason = "missing"
            else:
                reason = "confirm_against_source"
            yield {
                "path": path,
                "status": payload["status"],
                "reason": reason,
                "source": payload.get("source"),
            }
            return
        for key, value in payload.items():
            if key != "warnings":
                yield from review_fields(value, f"{path}.{key}" if path else key)
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            yield from review_fields(value, f"{path}.{index}")


def run_replay(input_path, contract_type):
    if contract_type not in {"jeonse", "monthly_rent", "sale"}:
        raise UnsupportedLeaseInput("지원 계약 유형이 아닙니다.")
    start = perf_counter()
    if input_path.stat().st_size > 100 * 1024 * 1024:
        raise UnsupportedLeaseInput("OCR JSON 크기 제한은 100 MiB입니다.")
    raw = input_path.read_bytes()
    document = json.loads(raw)
    loaded = perf_counter()
    template = validate_template(document, contract_type)
    checked = perf_counter()
    extraction = extract_document_ai(document, contract_type).model_dump(mode="json")
    extracted = perf_counter()
    reviews = list(review_fields(extraction))
    reviews.extend(
        {
            "path": f"special_terms.{index}",
            "reason": (
                "confirm_inferred_clause_order_and_text"
                if term.get("confidence") == 0.0
                else "confirm_clause_boundary"
            ),
            "source": term["source"],
        }
        for index, term in enumerate(extraction["special_terms"])
    )
    finished = perf_counter()
    return {
        "pipeline_version": "standard-contract-replay.v0.2",
        "status": "needs_review",
        "mode": "saved_google_ocr",
        "synthetic_only": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "template": template,
        "contract_type_source": "caller",
        "extraction": extraction,
        "review_items": reviews,
        "warnings": extraction["warnings"],
        "timings_seconds": {
            "load": loaded - start,
            "template_check": checked - loaded,
            "extract_and_validate": extracted - checked,
            "review_list": finished - extracted,
            "local_total_excluding_output_write": finished - start,
            "ocr_request": None,
        },
        "limitations": [
            "No live OCR request; historical OCR latency unknown",
            "No user confirmation UI or encrypted storage implemented",
            "No real-document privacy guarantee; synthetic experiments only",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr-json", type=Path, required=True)
    parser.add_argument(
        "--contract-type", choices=("jeonse", "monthly_rent", "sale"), required=True
    )
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_replay(args.ocr_json, args.contract_type)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Refuse accidental overwrite of OCR sources or previous experiment results.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {
                "status": result["status"],
                "review_count": len(result["review_items"]),
                "timings_seconds": result["timings_seconds"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
