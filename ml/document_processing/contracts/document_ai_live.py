# document_ai_live.py
"""
Description: 합성 표준 임대차·매매 PDF의 승인된 Google OCR 호출과 로컬 추출을 연결한다.
Author: ooheunsu
Date: 2026-10-06
Before:
    - 합성 PDF와 명시적인 프로세서 버전 및 개별 호출 승인이 필요함.
After:
    - OCR 원문, 요청 시간, 실행 기록, 사용자 확인용 추출 결과를 보존함.
"""

import argparse
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


def load_adc_credentials():
    """Load ADC and require the configured quota project without refreshing it."""
    try:
        import google.auth
        from google.auth.exceptions import GoogleAuthError

        credentials, _ = google.auth.default(
            scopes=("https://www.googleapis.com/auth/cloud-platform",),
        )
    except (GoogleAuthError, OSError, ValueError):
        raise LiveOCRError(
            "Google Application Default Credentials are unavailable or invalid"
        ) from None
    quota_project = credentials.quota_project_id
    if not quota_project or not re.fullmatch(
        r"[a-z][a-z0-9-]{4,28}[a-z0-9]", quota_project
    ):
        raise LiveOCRError("Google ADC does not contain a valid quota project")
    return credentials


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
        raise LiveOCRError("A three-page synthetic standard contract PDF is required")
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


def request_document(resource, location, raw, timeout, *, credentials=None):
    from google.api_core.client_options import ClientOptions
    from google.api_core.exceptions import GoogleAPICallError
    from google.cloud import documentai_v1 as documentai
    from google.protobuf.json_format import MessageToDict

    started = perf_counter()
    try:
        client = documentai.DocumentProcessorServiceClient(
            credentials=credentials,
            client_options=ClientOptions(
                api_endpoint=f"{location}-documentai.googleapis.com"
            )
        )
        request = documentai.ProcessRequest(
            name=resource,
            raw_document=documentai.RawDocument(
                content=raw,
                mime_type="application/pdf",
            ),
            field_mask="text,pages",
            skip_human_review=True,
        )
        response = client.process_document(request=request, retry=None, timeout=timeout)
        document = MessageToDict(
            response.document._pb,
            preserving_proto_field_name=False,
        )
    except GoogleAPICallError as error:
        status = getattr(getattr(error, "code", None), "name", type(error).__name__)
        raise LiveOCRError(f"Google OCR RPC failed with status {status}") from None
    except (OSError, ValueError):
        raise LiveOCRError(
            "OCR transport failed; billing/completion may be unknown. Do not retry blindly."
        ) from None
    if (
        not isinstance(document.get("text"), str)
        or not isinstance(document.get("pages"), list)
        or len(document["pages"]) != 3
    ):
        raise LiveOCRError("Google OCR returned an unexpected document structure")
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
    timeout=120,
):
    if not synthetic_only:
        raise LiveOCRError("Only explicitly declared synthetic documents are allowed")
    if contract_type not in {"jeonse", "monthly_rent", "sale"}:
        raise LiveOCRError("Unsupported contract type")
    if not math.isfinite(timeout) or not 1 <= timeout <= 300:
        raise LiveOCRError("Read timeout must be between 1 and 300 seconds")
    raw, config, key, _ = prepare(pdf, project, location, processor, version)
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
        credentials = load_adc_credentials()
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
            "authentication": "application_default_credentials",
            "status": "attempt_started_completion_unknown",
        }
        write_json(directory / "attempt.json", attempt)
        document, elapsed = request_document(
            config["resource"],
            location,
            raw,
            timeout,
            credentials=credentials,
        )
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
    result["pipeline_version"] = "standard-contract-live.v0.2"
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
        "--contract-type", choices=("jeonse", "monthly_rent", "sale"), required=True
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
    parser.add_argument("--timeout", type=float, default=120)
    args = vars(parser.parse_args())
    try:
        result = run_live(**args)
    except LiveOCRError as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
