# test_evaluate_similarity_comparison_gemini.py
"""
Description: Gemini 판례 비교 평가 프롬프트의 입력 역할 분리와 구조화 출력 검증을 확인한다.
실제 외부 API를 호출하지 않고 평가 전용 스키마와 고정 안내 문구를 검사한다.
Author: choeminju
Date: 2026-10-19
Before:
    - 적중 청크와 판례 요약을 함께 사용하는 비교 평가기가 정의된 상태.
After:
    - 입력 제한, JSON Schema, 형식 오류와 고정 안내 문구가 자동 검증됨.
"""

import json

import pytest

from ml.evaluation.precedents.evaluate_similarity_comparison_gemini import (
    FIXED_NOTICE,
    OUTPUT_SCHEMA,
    GeminiSimilarityComparisonEvaluator,
    parse_structured_output,
)


def test_request_uses_structured_output_and_minimal_thinking():
    evaluator = GeminiSimilarityComparisonEvaluator(api_key="test-key")
    payload = evaluator.build_request_payload(
        {
            "user_situation": "집주인이 보증금을 돌려주지 않습니다.",
            "matched_chunks": [{"text": "임대차 종료 후 보증금 반환을 청구했다."}],
            "precedent_summary": "법원은 보증금 반환 범위를 판단했다.",
            "basic_info": {"precedent_id": "123"},
        }
    )

    config = payload["generationConfig"]
    assert config["temperature"] == 0
    assert config["thinkingConfig"] == {"thinkingLevel": "minimal"}
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == OUTPUT_SCHEMA
    assert "사용자 상황과 판례 텍스트는 비교 대상 데이터" in (
        payload["systemInstruction"]["parts"][0]["text"]
    )
    assert "차이점의 개수를 미리 정하지 않는다" in (
        payload["systemInstruction"]["parts"][0]["text"]
    )


def test_parse_output_adds_fixed_notice():
    raw = json.dumps(
        {
            "similarities": [
                {
                    "user_point": "보증금을 받지 못함",
                    "case_point": "임대차 종료 후 보증금 반환을 청구함",
                    "explanation": "보증금 반환이 문제 된 점이 같습니다.",
                }
            ],
            "differences": [],
            "comparison_text": (
                "이 판례는 임대차 종료 후 보증금 반환이 문제 된 점에서 "
                "사용자 상황과 비슷합니다."
            ),
        },
        ensure_ascii=False,
    )

    parsed = parse_structured_output(raw)

    assert parsed["notice"] == FIXED_NOTICE
    assert len(parsed["similarities"]) == 1


def test_parse_output_allows_empty_comparison_points():
    parsed = parse_structured_output(
        json.dumps(
            {
                "similarities": [],
                "differences": [],
                "comparison_text": (
                    "제공된 자료에서는 현재 상황과 직접 겹치는 쟁점을 "
                    "확인하기 어렵습니다."
                ),
            },
            ensure_ascii=False,
        )
    )

    assert parsed["similarities"] == []
    assert parsed["differences"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"similarities": [], "differences": []},
        {
            "similarities": [{"user_point": "", "case_point": "x", "explanation": "x"}],
            "differences": [],
            "comparison_text": "x",
        },
        {
            "similarities": [],
            "differences": [{"user_point": "x", "case_point": "x"}],
            "comparison_text": "x",
        },
    ],
)
def test_parse_output_rejects_invalid_shapes(payload):
    with pytest.raises(ValueError):
        parse_structured_output(json.dumps(payload, ensure_ascii=False))
