# certified_letter_configuration.py
"""
Description: 환경변수와 로컬 DB 경로로 내용증명 생성 서비스의 런타임 의존성을 구성한다.
키가 없을 때는 비활성 서비스를 유지하고 모델과 검색기는 실제 요청 시 지연 로딩한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 기본 FastAPI 앱이 내용증명 그래프를 실제 모델·검색 DB와 조립하지 않는 상태.
After:
    - Gemini 질문 분기와 GPT-6.1 Terra 생성 모델이 검색·리랭커와 분리 연결됨.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from threading import RLock
from typing import Any

from api.services.certified_letter_service import CertifiedLetterService
from api.services.precedent_case_repository import PrecedentCaseRepository
from ml.agent.certified_letter.factory import create_certified_letter_workflow
from ml.agent.certified_letter.gemini import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_TIMEOUT_SECONDS,
    GeminiStructuredLetterModel,
)
from ml.agent.certified_letter.openai import (
    DEFAULT_MAX_OUTPUT_TOKENS as DEFAULT_OPENAI_MAX_OUTPUT_TOKENS,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_TIMEOUT_SECONDS as DEFAULT_OPENAI_TIMEOUT_SECONDS,
    OpenAIStructuredLetterModel,
)
from ml.agent.certified_letter.retrieval import (
    BgeM3PrecedentSearchAdapter,
    BgeM3StatuteSearchAdapter,
)
from ml.rag.precedent_eligibility import (
    create_eligibility_filtered_precedent_retriever_from_env,
)
from ml.rag.precedent_retriever import BgeM3PrecedentRetriever
from ml.rag.reranker import (
    SHARED_CROSS_ENCODER_MODEL_LOADER,
    CrossEncoderReranker,
    DenseSimilarityReranker,
)
from ml.rag.statute_retriever import (
    BgeM3StatuteRetriever,
    load_bge_model as load_statute_bge_model,
)
from precedents.precedent_config import load_env_file


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATUTE_DB_DIR = PROJECT_ROOT / "data/statutes/vectorstores/bge_m3"
DEFAULT_LOCAL_DATA_DIR = PROJECT_ROOT / "local_data"
DEFAULT_MODEL_CACHE_DIR = PROJECT_ROOT / "data/statutes/models"
ENV_FILE_VARIABLE = "ALPHALAWVA_ENV_FILE"


class _SharedBgeM3ModelLoader:
    """법령·판례 검색기가 동일한 BGE-M3 인스턴스를 공유하게 한다."""

    def __init__(self) -> None:
        self._model: Any | None = None
        self._model_cache: Path | None = None
        self._device: str | None = None
        self._lock = RLock()

    def __call__(self, model_cache: Path, device: str) -> Any:
        with self._lock:
            if self._model is None:
                self._model = load_statute_bge_model(model_cache, device)
                self._model_cache = model_cache.resolve()
                self._device = device
            elif (
                self._model_cache != model_cache.resolve()
                or self._device != device
            ):
                raise ValueError("공유 BGE-M3 검색기의 모델 설정이 일치하지 않습니다.")
            return self._model


def create_certified_letter_service_from_env() -> CertifiedLetterService:
    runtime_root = _load_environment()
    if not _enabled("CERTIFIED_LETTER_GENERATION_ENABLED", default=True):
        return CertifiedLetterService()
    gemini_api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    openai_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not gemini_api_key or not openai_api_key:
        return CertifiedLetterService()

    local_data_dir = _path_from_env(
        "ALPHALAWVA_LOCAL_DATA_DIR",
        runtime_root / "local_data",
        base_dir=runtime_root,
    )
    model_cache = _path_from_env(
        "CERTIFIED_LETTER_MODEL_CACHE_DIR",
        runtime_root / "data/statutes/models",
        base_dir=runtime_root,
    )
    statute_db = _path_from_env(
        "CERTIFIED_LETTER_STATUTE_DB_DIR",
        runtime_root / "data/statutes/vectorstores/bge_m3",
        base_dir=runtime_root,
    )
    precedent_db = _path_from_env(
        "CERTIFIED_LETTER_PRECEDENT_DB_DIR",
        local_data_dir
        / "precedents"
        / "vector_dbs"
        / "precedents_bge-m3_v1",
        base_dir=runtime_root,
    )
    gemini_usage_manifest = _path_from_env(
        "CERTIFIED_LETTER_GEMINI_USAGE_MANIFEST",
        local_data_dir
        / "certified_letters"
        / "manifests"
        / "gemini_scope_usage.jsonl",
        base_dir=runtime_root,
    )
    openai_usage_manifest = _path_from_env(
        "CERTIFIED_LETTER_OPENAI_USAGE_MANIFEST",
        local_data_dir
        / "certified_letters"
        / "manifests"
        / "openai_generation_usage.jsonl",
        base_dir=runtime_root,
    )
    device = os.environ.get("CERTIFIED_LETTER_EMBEDDING_DEVICE", "auto").strip()
    shared_model_loader = _SharedBgeM3ModelLoader()

    scope_classifier = GeminiStructuredLetterModel(
        api_key=gemini_api_key,
        model=os.environ.get(
            "CERTIFIED_LETTER_GEMINI_MODEL",
            DEFAULT_GEMINI_MODEL,
        ),
        timeout_seconds=float(
            os.environ.get(
                "CERTIFIED_LETTER_GEMINI_TIMEOUT_SECONDS",
                str(DEFAULT_TIMEOUT_SECONDS),
            )
        ),
        max_output_tokens=int(
            os.environ.get(
                "CERTIFIED_LETTER_GEMINI_MAX_OUTPUT_TOKENS",
                str(DEFAULT_MAX_OUTPUT_TOKENS),
            )
        ),
        usage_manifest_path=gemini_usage_manifest,
        max_session_cost_usd=_optional_positive_float(
            "CERTIFIED_LETTER_GEMINI_SESSION_BUDGET_USD"
        ),
    )
    model = OpenAIStructuredLetterModel(
        api_key=openai_api_key,
        model=os.environ.get(
            "CERTIFIED_LETTER_OPENAI_MODEL",
            DEFAULT_OPENAI_MODEL,
        ),
        timeout_seconds=float(
            os.environ.get(
                "CERTIFIED_LETTER_OPENAI_TIMEOUT_SECONDS",
                str(DEFAULT_OPENAI_TIMEOUT_SECONDS),
            )
        ),
        max_output_tokens=int(
            os.environ.get(
                "CERTIFIED_LETTER_OPENAI_MAX_OUTPUT_TOKENS",
                str(DEFAULT_OPENAI_MAX_OUTPUT_TOKENS),
            )
        ),
        reasoning_effort=os.environ.get(
            "CERTIFIED_LETTER_OPENAI_REASONING_EFFORT",
            DEFAULT_REASONING_EFFORT,
        ),
        usage_manifest_path=openai_usage_manifest,
        max_session_cost_usd=_optional_positive_float(
            "CERTIFIED_LETTER_OPENAI_SESSION_BUDGET_USD"
        ),
    )
    statute_search = BgeM3StatuteSearchAdapter(
        BgeM3StatuteRetriever(
            db_dir=statute_db,
            model_cache=model_cache,
            device=device,
            model_loader=shared_model_loader,
        )
    )
    precedent_retriever = create_eligibility_filtered_precedent_retriever_from_env(
        BgeM3PrecedentRetriever(
            db_dir=precedent_db,
            model_cache=model_cache,
            device=device,
            model_loader=shared_model_loader,
        ),
        base_dir=runtime_root,
    )
    precedent_search = BgeM3PrecedentSearchAdapter(precedent_retriever)
    statute_reranker = _create_reranker(
        role="STATUTE",
        local_data_dir=local_data_dir,
        runtime_root=runtime_root,
        default="kure-reranker-nano",
    )
    precedent_reranker = _create_reranker(
        role="PRECEDENT",
        local_data_dir=local_data_dir,
        runtime_root=runtime_root,
        default="kure-reranker-nano",
    )
    graph = create_certified_letter_workflow(
        model=model,
        scope_classifier=scope_classifier,
        statute_search=statute_search,
        precedent_search=precedent_search,
        statute_reranker=statute_reranker,
        precedent_reranker=precedent_reranker,
    )
    return CertifiedLetterService(
        graph=graph,
        precedent_repository=PrecedentCaseRepository(
            cases_dir=local_data_dir / "precedents" / "processed" / "final_cases"
        ),
    )


def _create_reranker(
    *,
    role: str,
    local_data_dir: Path,
    runtime_root: Path,
    default: str,
):
    prefix = f"CERTIFIED_LETTER_{role}_RERANKER"
    selected = os.environ.get(
        prefix,
        default,
    ).strip()
    if selected == "dense":
        return DenseSimilarityReranker()
    if not selected:
        raise ValueError(f"{prefix}는 비어 있을 수 없습니다.")
    configured_path = os.environ.get(f"{prefix}_DIR", "").strip()
    if not configured_path and re.fullmatch(r"[A-Za-z0-9._-]+", selected) is None:
        raise ValueError(
            f"{prefix} 이름에는 영문, 숫자, 점, 밑줄, 하이픈만 사용할 수 있습니다."
        )
    default_path = local_data_dir / "models/rerankers" / selected
    return CrossEncoderReranker(
        model_path=_path_from_env(
        f"{prefix}_DIR",
        default_path,
        base_dir=runtime_root,
        ),
        max_length=int(
            os.environ.get("CERTIFIED_LETTER_RERANKER_MAX_LENGTH", "512")
        ),
        batch_size=int(os.environ.get("CERTIFIED_LETTER_RERANKER_BATCH_SIZE", "16")),
        device=os.environ.get("CERTIFIED_LETTER_RERANKER_DEVICE", "auto").strip(),
        trust_remote_code=_enabled(
            "CERTIFIED_LETTER_RERANKER_TRUST_REMOTE_CODE",
            default=False,
        ),
        model_loader=SHARED_CROSS_ENCODER_MODEL_LOADER,
    )


def _load_environment() -> Path:
    configured = os.environ.get(ENV_FILE_VARIABLE, "").strip()
    if not configured:
        load_env_file()
        return PROJECT_ROOT

    env_file = Path(configured).expanduser()
    if not env_file.is_absolute():
        env_file = PROJECT_ROOT / env_file
    env_file = env_file.resolve()
    if not env_file.is_file():
        raise FileNotFoundError(
            f"{ENV_FILE_VARIABLE}에 지정한 파일을 찾을 수 없습니다: {env_file}"
        )
    load_env_file(env_file)
    return env_file.parent


def _path_from_env(name: str, default: Path, *, base_dir: Path) -> Path:
    value = os.environ.get(name, "").strip()
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        path = base_dir / path
    return path


def _enabled(name: str, *, default: bool) -> bool:
    fallback = "true" if default else "false"
    return os.environ.get(name, fallback).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _optional_positive_float(name: str) -> float | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    parsed = float(value)
    if parsed <= 0:
        raise ValueError(f"{name}은 0보다 커야 합니다.")
    return parsed
