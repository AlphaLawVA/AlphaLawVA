# document_ai_live.py
"""
Description: 합성 표준임대차 PDF의 승인된 Google OCR 호출과 로컬 추출을 연결한다.
Author: ooheunsu
Date: 2026-10-06
Before:
    - 합성 PDF와 명시적인 프로세서 버전 및 개별 호출 승인이 필요함.
After:
    - OCR 원문, 요청 시간, 실행 기록, 사용자 확인용 추출 결과를 보존함.
"""

import argparse
import base64
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from ml.document_processing.contracts.document_intake import (
    DocumentKind,
    inspect_contract_document,
)
from ml.document_processing.contracts.lease_pipeline import run_replay


class LiveOCRError(ValueError):
    """Safe message without provider response bodies or credentials."""


def prepare(pdf, project, location, processor, version):
    for value in (project, processor):
        if not re.fullmatch(r"[a-zA-Z0-9-]+", value):
            raise LiveOCRError("Invalid project or processor identifier")
    if location not in {"us", "eu"}:
        raise LiveOCRError("This experiment supports us/eu locations only")
    if not re.fullmatch(r"pretrained-ocr-v[0-9.]+-\d{4}-\d{2}-\d{2}", version):
        raise LiveOCRError("An explicit dated OCR version is required")
    inspection = inspect_contract_document(pdf, max_pdf_pages=3)
    if inspection.kind != DocumentKind.PDF or inspection.page_count != 3:
        raise LiveOCRError("A three-page synthetic lease PDF is required")
    raw = pdf.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != inspection.sha256:
        raise LiveOCRError("Input changed during validation")
    resource = (
        f"projects/{project}/locations/{location}/processors/{processor}"
        f"/processorVersions/{version}"
    )
    config = {
        "provider": "Google Document AI",
        "resource": resource,
        "version": version,
        "input_sha256": digest,
        "page_count": 3,
        "mime_type": "application/pdf",
        "field_mask": "text,pages",
        "skip_human_review": True,
    }
    key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    url = f"https://{location}-documentai.googleapis.com/v1/{resource}:process"
    return raw, config, key, url


def request_document(url, raw, token, timeout):
    import requests

    started = perf_counter()
    try:
        # A fresh Session has no automatic HTTP retries. Redirects are refused.
        with requests.Session() as session:
            response = session.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "rawDocument": {
                        "content": base64.b64encode(raw).decode("ascii"),
                        "mimeType": "application/pdf",
                    },
                    "fieldMask": "text,pages",
                    "skipHumanReview": True,
                },
                timeout=(10, timeout),
                allow_redirects=False,
            )
            if response.status_code != 200:
                raise LiveOCRError(f"Google OCR HTTP status {response.status_code}")
            document = response.json().get("document")
            if (
                not isinstance(document, dict)
                or not isinstance(document.get("text"), str)
                or not isinstance(document.get("pages"), list)
                or len(document["pages"]) != 3
            ):
                raise LiveOCRError(
                    "Google OCR returned an unexpected document structure"
                )
    except (requests.RequestException, ValueError):
        # Do not include request headers, OCR content, or server response text.
        raise LiveOCRError(
            "OCR request failed; billing/completion may be unknown. Do not retry blindly."
        ) from None
    return document, perf_counter() - started


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def run_live(
    pdf,
    cache_root,
    contract_type,
    *,
    project,
    location,
    processor,
    version,
    synthetic_only=False,
    execute_approved=False,
    estimated_cost_usd=None,
    env_file=Path(".env"),
    timeout=120,
):
    if not synthetic_only:
        raise LiveOCRError("Only explicitly declared synthetic documents are allowed")
    if contract_type not in {"jeonse", "monthly_rent"}:
        raise LiveOCRError("Only jeonse/monthly_rent extraction is supported")
    if not math.isfinite(timeout) or not 1 <= timeout <= 300:
        raise LiveOCRError("Read timeout must be between 1 and 300 seconds")
    raw, config, key, url = prepare(pdf, project, location, processor, version)
    directory = cache_root / key
    document_path = directory / "document.json"
    manifest_path = directory / "manifest.json"
    cached = manifest_path.is_file() and document_path.is_file()
    if cached:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("config") != config
            or manifest.get("document_sha256")
            != hashlib.sha256(document_path.read_bytes()).hexdigest()
        ):
            raise LiveOCRError("Cache integrity mismatch; no automatic request allowed")
    plan = {
        "status": "cached" if cached else "approval_required",
        "config": config,
        "cache_directory": str(directory),
        "request_count": 0 if cached else 1,
        "estimated_cost_usd": estimated_cost_usd,
    }
    if not execute_approved:
        return plan
    if not cached:
        if (
            estimated_cost_usd is None
            or not math.isfinite(estimated_cost_usd)
            or estimated_cost_usd < 0
        ):
            raise LiveOCRError(
                "Record the estimate approved for this individual execution"
            )
        from dotenv import dotenv_values

        token = dotenv_values(env_file).get("GOOGLE_DOCUMENT_AI_ACCESS_TOKEN")
        if (
            not token
            or token == "your_short_lived_access_token"
            or any(c.isspace() for c in token)
        ):
            raise LiveOCRError("Set a valid short-lived access token in the local .env")
        cache_root.mkdir(parents=True, exist_ok=True)
        try:
            directory.mkdir()
        except FileExistsError:
            raise LiveOCRError(
                "An earlier or concurrent attempt exists. Inspect it before any paid retry."
            ) from None
        attempt = {
            "config": config,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "estimated_cost_usd": estimated_cost_usd,
            "automatic_retries": 0,
            "request_count": 1,
            "synthetic_only": True,
            "status": "attempt_started_completion_unknown",
        }
        write_json(directory / "attempt.json", attempt)
        document, elapsed = request_document(url, raw, token, timeout)
        write_json(document_path, document)
        manifest = {
            **attempt,
            "status": "ocr_complete",
            "ocr_request_seconds": elapsed,
            "document_sha256": hashlib.sha256(document_path.read_bytes()).hexdigest(),
            "actual_charge_usd": None,
            "token_usage": None,
        }
        write_json(manifest_path, manifest)
    result = run_replay(document_path, contract_type)
    result["mode"] = "google_ocr_cache" if cached else "live_google_ocr"
    result["ocr_run"] = {
        "cache_hit": cached,
        "requests_this_run": 0 if cached else 1,
        "manifest": str(manifest_path),
        "original_request_seconds": manifest["ocr_request_seconds"],
    }
    result["timings_seconds"]["ocr_request"] = (
        None if cached else manifest["ocr_request_seconds"]
    )
    result["limitations"][0] = (
        "OCR duration includes network/response decoding; not server-only inference time"
    )
    result["pipeline_version"] = "standard-lease-live.v0.1"
    output = directory / f"{contract_type}_extraction_{uuid4().hex}.json"
    # Keep cached OCR usable when only the downstream extractor fails or changes.
    write_json(output, result)
    return {
        **plan,
        "status": "needs_review",
        "extraction_path": str(output),
        "ocr_run": result["ocr_run"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument(
        "--contract-type", choices=("jeonse", "monthly_rent"), required=True
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--location", choices=("us", "eu"), required=True)
    parser.add_argument("--processor", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--cache-root", type=Path, default=Path("output/document_ai_live")
    )
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--execute-approved", action="store_true")
    parser.add_argument("--estimated-cost-usd", type=float)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--timeout", type=float, default=120)
    args = vars(parser.parse_args())
    try:
        result = run_live(**args)
    except LiveOCRError as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
