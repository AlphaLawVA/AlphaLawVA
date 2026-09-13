# run_llama_candidate_scores.py
"""
Description: 판례 검색 후보 전체를 llama3.1:8b 로컬 모델로 평가하도록
compare_local_llm_candidate_scores.py를 고정 옵션으로 실행한다.
Author: choeminju
Date: 2026-09-12
Before:
    - Top10_후보판례_100문항 시트와 로컬 Ollama llama3.1:8b 모델이 준비된 상태.
    - local_data/precedents/evaluation/llm_score_runs/에 결과를 저장할 수 있는 상태.
After:
    - llama3.1:8b가 후보 판례별 0~3점과 판단 근거를 CSV 및 XLSM 시트로 저장.
    - 재실행 시 이미 성공한 후보 평가는 건너뛰고 미완료/실패 건부터 이어서 처리.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
COMPARE_SCRIPT = (
    PROJECT_ROOT
    / "ml"
    / "evaluation"
    / "precedents"
    / "compare_local_llm_candidate_scores.py"
)
OUTPUT_DIR = (
    PROJECT_ROOT
    / "local_data"
    / "precedents"
    / "evaluation"
    / "llm_score_runs"
    / "llama3_1_8b_full_model_specific"
)


def main() -> int:
    """llama3.1:8b 전체 후보 평가를 재개 가능한 고정 옵션으로 실행한다."""
    command = [
        sys.executable,
        str(COMPARE_SCRIPT),
        "--sample-size",
        "0",
        "--models",
        "llama3.1:8b",
        "--prompt-mode",
        "model_specific",
        "--result-sheet",
        "로컬LLM점수_라마_전체",
        "--output-dir",
        str(OUTPUT_DIR),
        "--num-ctx",
        "4096",
        "--num-predict",
        "120",
    ]
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
