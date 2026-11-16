# diagnose_certified_letter_retrieval_keywords.py
"""
Description: 내용증명 목적과 사실관계에서 Gemini가 추출하는 검색 보조 쟁점어를
확인하는 일회성 로컬 진단 도구다.
Author: choeminju
Date: 2026-11-17
Before:
    - GEMINI_API_KEY와 CERTIFIED_LETTER_GEMINI_MODEL이 로컬 환경에 설정된 상태.
After:
    - 원문 검색어에 덧붙일 수 있는 짧은 쟁점어와 호출 사용량을 표준 출력으로 확인함.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from dotenv import load_dotenv


shared_env_file = os.environ.get("ALPHALAWVA_SHARED_ENV_FILE", "").strip()
if shared_env_file:
    load_dotenv(Path(shared_env_file), override=False)

DEFAULT_MODEL = "gemini-3.5-flash-lite"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
MAX_OUTPUT_TOKENS = 96

CONTRACT_TYPE = "전세 임대차"
PURPOSE = "11월분 관리비 지급 요구"
FACTS = (
    "계약서에는 관리비를 정액 월 180,000원으로 정하고 있습니다. "
    "임차인은 10월 31일에 입주했고, 11월 관리비 180,000원은 제가 관리사무소에 "
    "대신 납부했습니다. 11월 10일 문자로 이 금액을 보내 달라고 요청했지만 아직 "
    "입금이 없습니다."
)

KEYWORD_SCHEMA = {
    "type": "object",
    "properties": {
        "retrieval_keywords": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 4,
        }
    },
    "required": ["retrieval_keywords"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """당신은 부동산 계약 분쟁 판례 검색을 위한 쟁점어 추출기입니다.
계약유형, 목적, 사실관계만 읽고 Dense 검색 원문 뒤에 덧붙일 짧은 한국어 쟁점어를 1~4개 추출하세요.

규칙:
- 사용자가 말한 계약상 대상·의무·문제 상태를 우선합니다.
- 각 항목은 2~12자 정도의 짧은 명사구로 씁니다.
- 법령명, 조문, 판례명, 사건번호, 승소 가능성, 입력에 없는 사실은 만들지 마세요.
- 원문을 요약하거나 대체하지 않습니다. 검색 보조어만 반환하세요.
- 설명 없이 지정된 JSON만 반환하세요.
"""


def main() -> None:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    model = os.environ.get("CERTIFIED_LETTER_GEMINI_MODEL", DEFAULT_MODEL).strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다.")

    input_data = {
        "contract_type": CONTRACT_TYPE,
        "purpose": PURPOSE,
        "facts": FACTS,
    }
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(input_data, ensure_ascii=False)}]}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
            "responseMimeType": "application/json",
            "responseJsonSchema": KEYWORD_SCHEMA,
        },
    }
    request = Request(
        f"{BASE_URL}/models/{quote(model.removeprefix('models/'))}:generateContent?key={quote(api_key)}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))

    text = result["candidates"][0]["content"]["parts"][0]["text"]
    parsed = json.loads(text)
    print(f"model: {model}")
    print(f"retrieval_keywords: {parsed['retrieval_keywords']}")
    print(f"usage: {result.get('usageMetadata', {})}")


if __name__ == "__main__":
    main()
