# precedent_config.py
"""
Description: 판례 수집·전처리·분류 스크립트가 공유하는 API 주소, 경로, 환경변수 기본값을 정의한다.
로컬 데이터 저장 위치와 manifest/result 파일 경로를 한 곳에서 관리한다.
Author: choeminju
Date: 2026-08-11
Before:
    - 판례 파이프라인 스크립트마다 API 주소와 저장 경로를 개별 관리해야 하는 상태.

After:
    - 판례 파이프라인 공통 설정과 디렉터리 생성 함수가 제공.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"

SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
DETAIL_URL = "https://www.law.go.kr/DRF/lawService.do"
API_TARGET = "prec"
API_RESPONSE_TYPE = "JSON"
DATA_SOURCE_NAME = "대법원"

LOCAL_DATA_ROOT = Path(
    os.environ.get("ALPHALAWVA_LOCAL_DATA_DIR", PROJECT_ROOT / "local_data")
)
if not LOCAL_DATA_ROOT.is_absolute():
    LOCAL_DATA_ROOT = PROJECT_ROOT / LOCAL_DATA_ROOT

PRECEDENT_DATA_ROOT = LOCAL_DATA_ROOT / "precedents"
RAW_SEARCHES_DIR = PRECEDENT_DATA_ROOT / "raw" / "searches"
RAW_DETAILS_DIR = PRECEDENT_DATA_ROOT / "raw" / "details"
MANIFESTS_DIR = PRECEDENT_DATA_ROOT / "manifests"
PROCESSED_DIR = PRECEDENT_DATA_ROOT / "processed"

COLLECTION_MANIFEST_PATH = MANIFESTS_DIR / "collection_manifest.json"
CANDIDATES_PATH = MANIFESTS_DIR / "candidates.jsonl"
RETRY_QUEUE_PATH = MANIFESTS_DIR / "retry_queue.jsonl"
LLM_INPUTS_PATH = PROCESSED_DIR / "llm_inputs.jsonl"
CLASSIFICATION_RESULTS_PATH = PROCESSED_DIR / "classification_results.jsonl"
CLASSIFICATION_FAILURES_PATH = PROCESSED_DIR / "classification_failures.jsonl"
CLASSIFICATION_MANIFEST_PATH = PROCESSED_DIR / "classification_manifest.json"
BASIC_FIELD_CLASSIFICATION_INPUTS_PATH = PROCESSED_DIR / "basic_field_classification_inputs.jsonl"
BASIC_FIELD_CLASSIFICATION_RESULTS_PATH = PROCESSED_DIR / "basic_field_classification_results.jsonl"
BASIC_FIELD_CLASSIFICATION_FAILURES_PATH = PROCESSED_DIR / "basic_field_classification_failures.jsonl"
BASIC_FIELD_CLASSIFICATION_MANIFEST_PATH = PROCESSED_DIR / "basic_field_classification_manifest.json"
PROCESSED_CASES_DIR = PROCESSED_DIR / "cases"
CLASSIFICATION_CASES_DIR = PROCESSED_DIR / "classification_cases"
PROCESSED_EXCLUDED_OUTLIERS_DIR = PROCESSED_DIR / "excluded_outliers"
PREPROCESS_INDEX_PATH = PROCESSED_DIR / "preprocess_index.json"
PREPROCESS_REPORT_PATH = PROCESSED_DIR / "preprocess_report.json"
KEYWORD_DIAGNOSIS_REPORT_PATH = PROCESSED_DIR / "keyword_diagnosis_report.json"

DEFAULT_DISPLAY = 100
DEFAULT_DELAY_SECONDS = float(
    os.environ.get("PRECEDENT_COLLECTION_DELAY_SECONDS", "0.3")
)
DEFAULT_TIMEOUT_SECONDS = float(
    os.environ.get("PRECEDENT_COLLECTION_TIMEOUT_SECONDS", "30")
)
DEFAULT_MAX_RETRIES = int(os.environ.get("PRECEDENT_COLLECTION_MAX_RETRIES", "3"))
USER_AGENT = "AlphaLawVA-precedent-collector/0.1"
DEFAULT_OLLAMA_BASE_URL = os.environ.get(
    "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
)
DEFAULT_PRECEDENT_LLM_MODEL = os.environ.get("PRECEDENT_LLM_MODEL", "gemma2:9b")
DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_GENERATE_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


def load_env_file(path: Path = ENV_FILE) -> None:
    """간단한 .env 파일을 읽어서 현재 프로세스 환경변수에 넣는다."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_law_api_key(allow_test_key: bool = False) -> str:
    """LAW_API_KEY를 가져오고, 테스트 모드면 공식 샘플 키를 허용한다."""
    load_env_file()
    api_key = os.environ.get("LAW_API_KEY", "").strip()
    if api_key:
        return api_key
    if allow_test_key:
        return "test"
    raise RuntimeError(
        "LAW_API_KEY 환경변수를 설정하세요. .env에 LAW_API_KEY=... 형태로 "
        "넣거나, 테스트 포맷 확인만 할 때 --allow-test-key를 사용하세요."
    )


def ensure_collection_dirs() -> None:
    """판례 수집 결과가 저장될 로컬 폴더들을 만든다."""
    RAW_SEARCHES_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DETAILS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_CASES_DIR.mkdir(parents=True, exist_ok=True)
    CLASSIFICATION_CASES_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_EXCLUDED_OUTLIERS_DIR.mkdir(parents=True, exist_ok=True)


def now_utc_iso() -> str:
    """수집 시각 기록용 UTC ISO 문자열을 만든다."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
