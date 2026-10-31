# ollama_similarity_explainer.py
"""
Description: 사용자 상황과 검색된 판례의 공통점을 로컬 Ollama 모델로 설명한다.
적중 청크와 판례 요약만 전달하고 구조화된 유사성 설명을 검증해 반환한다.
Author: choeminju
Date: 2026-10-14
Before:
    - Ollama 서버에 유사성 설명용 로컬 모델이 설치되어 실행 가능한 상태.
After:
    - 판례 상세 응답에서 근거 청크에 한정된 matched_reason을 생성할 수 있음.
"""

from __future__ import annotations

import json
import os
import unicodedata
from collections.abc import Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from api.models.precedents import SimilarityExplanationMethod
from ml.rag.precedent_retriever import PrecedentChunkMatch


DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_SIMILARITY_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_NUM_CTX = 8192
DEFAULT_NUM_PREDICT = 240
DEFAULT_MAX_CHUNKS = 3
DEFAULT_MAX_CHUNK_CHARS = 1200
DEFAULT_MAX_SOURCE_CHARS = 1200
MAX_MATCHED_REASON_CHARS = 500
MAX_EVIDENCE_CHARS = 320

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "user_evidence": {
            "type": "string",
            "minLength": 2,
            "maxLength": MAX_EVIDENCE_CHARS,
            "description": "사용자 상황에서 공통점을 뒷받침하는 원문 그대로의 구절",
        },
        "precedent_evidence": {
            "type": "string",
            "minLength": 2,
            "maxLength": MAX_EVIDENCE_CHARS,
            "description": "판례 자료에서 공통점을 뒷받침하는 원문 그대로의 구절",
        },
        "matched_reason": {
            "type": "string",
            "minLength": 20,
            "maxLength": MAX_MATCHED_REASON_CHARS,
            "description": (
                "'사용자 상황과 이 판례는'으로 시작하여 공통점을 설명하는 "
                "두 근거 구절의 공통점만 설명하는 완결된 한국어 한 문장"
            ),
        }
    },
    "required": ["user_evidence", "precedent_evidence", "matched_reason"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """당신은 부동산 판례 검색 결과의 유사성을 설명하는 도우미입니다.
제공된 사용자 상황과 판례 자료만 사용하세요.

규칙:
1. user_evidence에는 사용자 상황에서 공통점을 입증하는 구절을 원문 그대로 복사합니다.
2. precedent_evidence에는 판례 자료에서 대응하는 구절을 원문 그대로 복사합니다.
3. matched_reason은 두 근거 구절에 모두 나타난 공통점만 설명합니다.
4. matched_reason은 공통 사실을 일상적인 표현으로 설명합니다.
5. 어느 한쪽에만 있는 권리 행사 가능성, 당사자, 상속, 점유, 소멸시효,
   절차 또는 법률효과를 공통점에 넣지 않습니다.
6. 제공 자료에 없는 사실, 법률, 조문, 사건번호 또는 결론을 만들지 않습니다.
7. 승소 가능성, 법률 자문, 사용자 사건의 결론을 말하지 않습니다.
8. matched_reason은 반드시 '사용자 상황과 이 판례는 ... 점에서 유사합니다.'
   형식의 완결된 한 문장으로 작성합니다.
9. '반환청구 여부' 같은 제목형 명사구로 끝내지 않습니다.
10. 출력은 지정된 JSON 스키마를 따릅니다.
"""


class OllamaSimilarityExplainer:
    def __init__(
        self,
        *,
        model: str = DEFAULT_SIMILARITY_MODEL,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        num_ctx: int = DEFAULT_NUM_CTX,
        num_predict: int = DEFAULT_NUM_PREDICT,
        max_chunks: int = DEFAULT_MAX_CHUNKS,
        max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    ) -> None:
        normalized_model = model.strip()
        normalized_base_url = base_url.strip().rstrip("/")
        parsed_url = urlparse(normalized_base_url)
        if not normalized_model:
            raise ValueError("Ollama 모델명은 비어 있을 수 없습니다.")
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("Ollama base URL 형식이 올바르지 않습니다.")
        if timeout_seconds <= 0:
            raise ValueError("Ollama timeout은 0보다 커야 합니다.")
        if num_ctx < 1 or num_predict < 1:
            raise ValueError("Ollama context와 출력 토큰 수는 1 이상이어야 합니다.")
        if max_chunks < 1 or max_chunk_chars < 1:
            raise ValueError("LLM에 전달할 청크 제한은 1 이상이어야 합니다.")

        self._model_name = normalized_model
        self.base_url = normalized_base_url
        self.timeout_seconds = timeout_seconds
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.max_chunks = max_chunks
        self.max_chunk_chars = max_chunk_chars

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def method(self) -> SimilarityExplanationMethod:
        return SimilarityExplanationMethod.LOCAL_LLM

    def explain(
        self,
        *,
        query: str,
        precedent: dict[str, Any],
        matched_chunks: Sequence[PrecedentChunkMatch],
    ) -> str:
        if not matched_chunks:
            raise ValueError("유사성 설명에 사용할 적중 청크가 없습니다.")

        payload = {
            "user_situation": self._compact(query, 2000),
            "precedent": self._build_precedent_payload(precedent),
            "matched_evidence": [
                {
                    "rank": chunk.rank,
                    "section": chunk.section,
                    "chunk_type": chunk.chunk_type,
                    "similarity_score": round(chunk.similarity, 6),
                    "text": self._compact(chunk.text, self.max_chunk_chars),
                }
                for chunk in matched_chunks[: self.max_chunks]
            ],
        }
        request_payload = {
            "model": self.model_name,
            "stream": False,
            "think": False,
            "format": OUTPUT_SCHEMA,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, indent=2),
                },
            ],
            "options": {
                "temperature": 0,
                "num_ctx": self.num_ctx,
                "num_predict": self.num_predict,
            },
        }
        response_payload = self._post_json(request_payload)
        message = response_payload.get("message")
        if not isinstance(message, dict):
            raise ValueError("Ollama 유사성 설명 메시지가 없습니다.")
        raw_content = message.get("content", "")
        try:
            parsed_content = json.loads(raw_content)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("Ollama 유사성 설명이 JSON 형식이 아닙니다.") from error
        if not isinstance(parsed_content, dict):
            raise ValueError("Ollama 유사성 설명 객체 형식이 올바르지 않습니다.")

        user_evidence = self._normalize_text(parsed_content.get("user_evidence"))
        precedent_evidence = self._normalize_text(
            parsed_content.get("precedent_evidence")
        )
        if len(user_evidence) > MAX_EVIDENCE_CHARS:
            raise ValueError("Ollama가 제시한 사용자 근거가 너무 깁니다.")
        if len(precedent_evidence) > MAX_EVIDENCE_CHARS:
            raise ValueError("Ollama가 제시한 판례 근거가 너무 깁니다.")
        normalized_query = self._compact(query, 2000)
        precedent_corpus = " ".join(
            [
                *payload["precedent"].values(),
                *(item["text"] for item in payload["matched_evidence"]),
            ]
        )
        if not self._contains_evidence(user_evidence, normalized_query):
            raise ValueError("Ollama가 제시한 사용자 근거를 원문에서 확인할 수 없습니다.")
        if not self._contains_evidence(precedent_evidence, precedent_corpus):
            raise ValueError("Ollama가 제시한 판례 근거를 원문에서 확인할 수 없습니다.")

        reason = self._normalize_text(parsed_content.get("matched_reason"))
        if not reason:
            raise ValueError("Ollama 유사성 설명이 비어 있습니다.")
        if len(reason) > MAX_MATCHED_REASON_CHARS:
            raise ValueError("Ollama 유사성 설명이 너무 깁니다.")
        if len(reason) < 20 or "유사" not in reason:
            raise ValueError("Ollama 유사성 설명이 완결된 비교 문장이 아닙니다.")
        return reason

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError) as error:
            raise RuntimeError("로컬 유사성 설명 모델을 호출할 수 없습니다.") from error
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as error:
            raise RuntimeError("Ollama 응답을 해석할 수 없습니다.") from error
        if not isinstance(parsed, dict):
            raise RuntimeError("Ollama 응답 객체 형식이 올바르지 않습니다.")
        return parsed

    @classmethod
    def _build_precedent_payload(cls, precedent: dict[str, Any]) -> dict[str, str]:
        source_fields = {
            "precedent_id": "판례일련번호",
            "case_number": "사건번호",
            "case_name": "사건명",
            "court_name": "법원명",
            "decision_date": "선고일자",
            "precedent_summary": "생성요약",
            "easy_summary": "쉬운요약",
            "issue": "판시사항",
            "holding": "판결요지",
        }
        result = {}
        for public_key, source_key in source_fields.items():
            value = cls._compact(precedent.get(source_key), DEFAULT_MAX_SOURCE_CHARS)
            if value:
                result[public_key] = value
        return result

    @staticmethod
    def _compact(value: Any, limit: int) -> str:
        normalized = OllamaSimilarityExplainer._normalize_text(value)
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3].rstrip() + "..."

    @staticmethod
    def _normalize_text(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        return " ".join(value.split())

    @staticmethod
    def _contains_evidence(evidence: str, corpus: str) -> bool:
        def alphanumeric_text(value: str) -> str:
            normalized = unicodedata.normalize("NFKC", value)
            return "".join(character for character in normalized if character.isalnum())

        normalized_evidence = alphanumeric_text(evidence)
        return bool(
            len(normalized_evidence) >= 2
            and normalized_evidence in alphanumeric_text(corpus)
        )


def create_ollama_similarity_explainer_from_env() -> OllamaSimilarityExplainer | None:
    enabled = os.environ.get("PRECEDENT_SIMILARITY_LLM_ENABLED", "true")
    if enabled.strip().lower() in {"0", "false", "no", "off"}:
        return None
    return OllamaSimilarityExplainer(
        model=os.environ.get(
            "PRECEDENT_SIMILARITY_LLM_MODEL",
            DEFAULT_SIMILARITY_MODEL,
        ),
        base_url=os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
        timeout_seconds=float(
            os.environ.get(
                "PRECEDENT_SIMILARITY_LLM_TIMEOUT_SECONDS",
                str(DEFAULT_TIMEOUT_SECONDS),
            )
        ),
        num_ctx=int(
            os.environ.get(
                "PRECEDENT_SIMILARITY_LLM_NUM_CTX",
                str(DEFAULT_NUM_CTX),
            )
        ),
        num_predict=int(
            os.environ.get(
                "PRECEDENT_SIMILARITY_LLM_NUM_PREDICT",
                str(DEFAULT_NUM_PREDICT),
            )
        ),
    )
