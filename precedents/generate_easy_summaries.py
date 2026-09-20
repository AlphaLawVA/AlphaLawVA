# generate_easy_summaries.py
"""
Description: 최종 판례 JSON의 생성요약을 일반 사용자용 쉬운요약으로 다시 작성한다.
Gemini 또는 Ollama 호환 LLM 결과를 JSONL로 즉시 저장하고, 선택 시 final_cases에 쉬운요약을 추가한다.
Author: choeminju
Date: 2026-09-14
Before:
    - local_data/precedents/processed/final_cases/에 생성요약이 포함된 최종 판례 JSON이 있는 상태.
After:
    - 각 final case JSON에 쉬운요약 필드가 추가되고 easy_summaries 결과·실패·manifest 파일이 생성.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from precedent_config import (
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_PRECEDENT_LLM_MODEL,
    DEFAULT_GEMINI_MODEL,
    GEMINI_GENERATE_URL_TEMPLATE,
    PROCESSED_DIR,
    PROJECT_ROOT,
    load_env_file,
    now_utc_iso,
)


FINAL_CASES_DIR = PROCESSED_DIR / "final_cases"
EASY_SUMMARY_DIR = PROCESSED_DIR / "easy_summaries"
DEFAULT_FIELD_NAME = "쉬운요약"
SCHEMA_VERSION = "precedent_easy_summary.v1"
MANIFEST_SCHEMA_VERSION = "precedent_easy_summary_manifest.v1"
PROMPT_VERSIONS = {
    "v7": "precedent_easy_summary_gemini.v7",
    "v9": "precedent_easy_summary_gemini.v9",
    "v10": "precedent_easy_summary_gemini.v10",
    "v11": "precedent_easy_summary_gemini.v11",
    "v12": "precedent_easy_summary_gemini.v12",
}
REVIEW_MIN_EASY_SUMMARY_CHARS = 80
MAX_EASY_SUMMARY_CHARS = 600
LEGACY_HARD_MIN_EASY_SUMMARY_CHARS = 150
LEGACY_RECOMMENDED_MIN_EASY_SUMMARY_CHARS = 180
LEGACY_RECOMMENDED_MAX_EASY_SUMMARY_CHARS = 650
LEGACY_MAX_EASY_SUMMARY_CHARS = 700
GEMINI_PRICING_USD_PER_MILLION = {
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
}


@dataclass(frozen=True)
class RunPaths:
    results: Path
    failures: Path
    manifest: Path


@dataclass
class RunStats:
    started_at: str
    total_targets: int
    generated_count: int = 0
    skipped_existing_count: int = 0
    failure_count: int = 0
    input_token_count: int = 0
    output_token_count: int = 0
    total_token_count: int = 0
    stopped_reason: str | None = None


def parse_args() -> argparse.Namespace:
    """커맨드라인 옵션을 정의한다."""
    load_env_file()
    parser = argparse.ArgumentParser(
        description="Generate user-friendly easy summaries for precedent final_cases."
    )
    parser.add_argument(
        "--provider",
        choices=["gemini", "ollama"],
        default=os.environ.get("PRECEDENT_EASY_SUMMARY_PROVIDER", "gemini"),
        help="쉬운요약 생성에 사용할 provider.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("PRECEDENT_EASY_SUMMARY_LLM_MODEL", DEFAULT_PRECEDENT_LLM_MODEL),
        help="Ollama 호환 API에서 사용할 모델명.",
    )
    parser.add_argument(
        "--gemini-model",
        default=os.environ.get("PRECEDENT_EASY_SUMMARY_GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        help="Gemini API에서 사용할 모델명.",
    )
    parser.add_argument(
        "--prompt-profile",
        choices=sorted(PROMPT_VERSIONS),
        default="v10",
        help="쉬운요약 생성에 사용할 버전 관리된 프롬프트.",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
        help="Ollama base URL. RunPod Ollama proxy URL도 여기에 넣는다.",
    )
    parser.add_argument(
        "--field-name",
        default=DEFAULT_FIELD_NAME,
        help="final case JSON에 추가할 쉬운요약 필드명.",
    )
    parser.add_argument(
        "--run-name",
        default=None,
        help="결과 파일명에 붙일 실행 이름. 생략하면 모델명으로 자동 생성한다.",
    )
    parser.add_argument(
        "--case-ids",
        default=None,
        help="처리할 쉼표 구분 판례일련번호. 생략하면 생성요약이 있는 전체 final_cases를 대상으로 한다.",
    )
    parser.add_argument(
        "--case-ids-from-file",
        type=Path,
        default=None,
        help="판례일련번호 목록이 들어 있는 JSON/JSONL/텍스트 파일.",
    )
    parser.add_argument("--limit", type=int, default=None, help="테스트용 처리 개수 제한.")
    parser.add_argument(
        "--sample-size",
        type=int,
        default=None,
        help="생성요약 길이 구간별로 섞은 테스트 샘플 개수. 전체 실행 시 생략한다.",
    )
    parser.add_argument("--delay", type=float, default=0.05, help="Gemini 호출 사이 대기 시간.")
    parser.add_argument("--timeout", type=float, default=120, help="Gemini HTTP 호출 타임아웃 초.")
    parser.add_argument(
        "--ollama-num-ctx",
        type=int,
        default=int(os.environ.get("PRECEDENT_EASY_SUMMARY_OLLAMA_NUM_CTX", "4096")),
        help="Ollama context window 크기.",
    )
    parser.add_argument(
        "--ollama-num-predict",
        type=int,
        default=int(os.environ.get("PRECEDENT_EASY_SUMMARY_OLLAMA_NUM_PREDICT", "360")),
        help="Ollama가 생성할 최대 토큰 수.",
    )
    parser.add_argument("--progress-every", type=int, default=20, help="N건마다 진행 로그를 출력한다.")
    parser.add_argument("--overwrite", action="store_true", help="이미 쉬운요약이 있어도 다시 생성한다.")
    parser.add_argument(
        "--skip-final-write",
        action="store_true",
        help="테스트 비교용으로 final case JSON은 수정하지 않고 결과 JSONL만 저장한다.",
    )
    parser.add_argument(
        "--ignore-final-field",
        action="store_true",
        help="final case에 쉬운요약이 있어도 스킵하지 않는다. 모델 비교 테스트에 사용한다.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="대상 개수와 첫 프롬프트만 확인하고 API를 호출하지 않는다.",
    )
    return parser.parse_args()


def normalize_text(value: Any) -> str:
    """과도한 공백을 줄인 한 문단 텍스트를 만든다."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_easy_summary(value: Any) -> str:
    """쉬운요약의 문단 구분을 보존하면서 과도한 공백을 정리한다."""
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = []
    for paragraph in re.split(r"\n{2,}", text):
        cleaned = re.sub(r"[ \t\f\v]+", " ", paragraph)
        cleaned = re.sub(r" *\n *", "\n", cleaned).strip()
        if cleaned:
            paragraphs.append(cleaned)
    return "\n\n".join(paragraphs).strip()


def sanitize_run_name(value: str) -> str:
    """모델명을 파일명에 안전한 문자열로 바꾼다."""
    return re.sub(r"[^A-Za-z0-9가-힣_.-]+", "-", value).strip("-")


def normalize_gemini_model_name(model: str) -> str:
    """Gemini REST URL에 넣을 모델명을 정리한다."""
    cleaned = model.strip()
    if cleaned.startswith("models/"):
        return cleaned.removeprefix("models/")
    return cleaned


def selected_model_name(args: argparse.Namespace) -> str:
    """현재 provider에 맞는 모델명을 반환한다."""
    if args.provider == "ollama":
        return args.model
    return normalize_gemini_model_name(args.gemini_model)


def build_run_name(args: argparse.Namespace) -> str:
    """실행 이름에 대응되는 결과 파일 접두어를 만든다."""
    if args.run_name:
        return sanitize_run_name(args.run_name)
    return sanitize_run_name(f"easy_summary_{args.provider}_{selected_model_name(args)}")


def build_run_paths(run_name: str) -> RunPaths:
    """실행 이름에 대응되는 입출력 파일 경로를 만든다."""
    EASY_SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    return RunPaths(
        results=EASY_SUMMARY_DIR / f"{run_name}_results.jsonl",
        failures=EASY_SUMMARY_DIR / f"{run_name}_failures.jsonl",
        manifest=EASY_SUMMARY_DIR / f"{run_name}_manifest.json",
    )


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 파일을 읽어 row 목록을 반환한다."""
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """한 row를 JSONL에 즉시 추가한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_processed_ids(path: Path) -> set[str]:
    """기존 결과 파일에서 이미 처리된 판례 ID를 읽는다."""
    processed_ids = set()
    for row in iter_jsonl(path):
        precedent_id = row.get("판례일련번호") or row.get("precedent_id")
        if precedent_id:
            processed_ids.add(str(precedent_id))
    return processed_ids


def load_ids_from_file(path: Path) -> list[str]:
    """JSON, JSONL, 텍스트 파일에서 판례 ID 목록을 읽는다."""
    if not path.exists():
        raise FileNotFoundError(f"판례 ID 파일을 찾지 못했습니다: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        payload = json.loads(text)
        if isinstance(payload, list):
            ids = []
            for item in payload:
                if isinstance(item, dict):
                    ids.append(str(item.get("판례일련번호") or item.get("precedent_id") or "").strip())
                else:
                    ids.append(str(item).strip())
            return [precedent_id for precedent_id in ids if precedent_id]
    if path.suffix == ".jsonl":
        return [
            str(row.get("판례일련번호") or row.get("precedent_id") or "").strip()
            for row in iter_jsonl(path)
            if str(row.get("판례일련번호") or row.get("precedent_id") or "").strip()
        ]
    return [line.strip() for line in text.splitlines() if line.strip()]


def load_final_case(path: Path) -> dict[str, Any]:
    """판례 final case JSON을 읽는다."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_final_case(path: Path, case: dict[str, Any]) -> None:
    """판례 final case JSON을 보기 좋은 UTF-8 JSON으로 저장한다."""
    path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collect_target_paths(args: argparse.Namespace) -> list[Path]:
    """실행 대상 final case JSON 경로를 고른다."""
    if args.case_ids_from_file:
        ids = load_ids_from_file(args.case_ids_from_file)
    elif args.case_ids:
        ids = [case_id.strip() for case_id in args.case_ids.split(",") if case_id.strip()]
    else:
        ids = []

    if ids:
        paths = [FINAL_CASES_DIR / f"{case_id}.json" for case_id in ids]
        missing = [str(path) for path in paths if not path.exists()]
        if missing:
            raise FileNotFoundError(f"final case 파일이 없습니다: {missing[:5]}")
    else:
        paths = sorted(FINAL_CASES_DIR.glob("*.json"))

    targets = []
    for path in paths:
        case = load_final_case(path)
        if normalize_text(case.get("생성요약")):
            targets.append(path)
    if args.sample_size is not None:
        targets = build_length_balanced_sample(targets, args.sample_size)
    if args.limit is not None:
        targets = targets[: args.limit]
    return targets


def build_length_balanced_sample(paths: list[Path], sample_size: int) -> list[Path]:
    """생성요약 길이 기준으로 짧음·중간·김 구간을 섞은 샘플을 만든다."""
    if sample_size <= 0 or sample_size >= len(paths):
        return paths

    rows = []
    for path in paths:
        case = load_final_case(path)
        summary_len = len(normalize_text(case.get("생성요약")))
        case_name = normalize_text(case.get("사건명"))
        rows.append((summary_len, case_name, path))

    rows.sort(key=lambda item: (item[0], item[1], item[2].name))
    bins: dict[int, list[tuple[int, str, Path]]] = defaultdict(list)
    for index, row in enumerate(rows):
        bins[min(2, index * 3 // len(rows))].append(row)

    per_bin = sample_size // 3
    remainder = sample_size % 3
    sampled: list[Path] = []
    used_case_names: set[str] = set()
    for bin_index in range(3):
        quota = per_bin + (1 if bin_index < remainder else 0)
        picked = 0
        for _, case_name, path in bins[bin_index]:
            if case_name in used_case_names and picked < len(bins[bin_index]) - quota:
                continue
            sampled.append(path)
            used_case_names.add(case_name)
            picked += 1
            if picked >= quota:
                break

    if len(sampled) < sample_size:
        existing = set(sampled)
        for _, _, path in rows:
            if path not in existing:
                sampled.append(path)
            if len(sampled) >= sample_size:
                break
    return sampled[:sample_size]


def build_prompt_v7(case: dict[str, Any], field_name: str) -> str:
    """초기 쉬운 표현과 정확성의 균형을 시도한 v7 프롬프트를 만든다."""
    precedent_id = normalize_text(case.get("판례일련번호"))
    generated_summary = normalize_text(case.get("생성요약"))
    return f"""
당신은 법률 지식이 없는 20~30대 일반인을 위해 법원 판례를 쉽고 정확하게 번역해 주는 리걸 에디터입니다.
아래의 [입력 요약]을 읽고, [작성 원칙]을 엄격히 준수하여 일반인이 이해할 수 있는 쉬운 요약으로 변환하십시오.

[작성 원칙]

1. 정확성 및 할루시네이션 차단 (가장 중요)

- [입력 요약]에 없는 사실, 법률 요건, 판단 이유, 법적 효과를 절대 추가하지 마십시오.
- 법률 용어의 뜻을 풀기 위해 외부 지식(예: 대항력의 요건, 근저당권의 일반적 의미 등)을 끌어오지 마십시오.
- '권리자', '본인'을 '원래 주인', '실제 주인'과 같이 법적 의미가 달라질 수 있는 단어로 임의 변경하지 마십시오.
- '파기환송' 판결을 누군가의 책임이 최종적으로 확정된 것처럼 표현하지 마십시오.

2. 가독성 및 쉬운 서술 방식

- 법률 용어를 억지로 '사전적 정의'하려고 시도하지 마십시오.
- 대신 "누가, 누구에게, 무엇을 주장했고, 법원은 어떻게 결론 내렸는지" 당사자의 행동과 상황 중심으로 주변 문맥을 쉬운 생활어로 서술하십시오.
- (예: '합의해지가 성립하려면' → '계약을 끝내기로 합의했다고 보려면')

3. 문체 및 분량 규칙

- 모든 문장은 '~다', '~했다', '~보았다', '~판단했다' 등의 평서형으로 작성하십시오. (존댓말 사용 금지)
- "이 판례는~" 이라는 표현으로 시작하지 마십시오.
- 사용자에게 어떠한 행동(소송, 상담 등)도 권유하지 마십시오.
- 사건번호, 법원명, 선고일자는 포함하지 마십시오.
- 분량: 쟁점이 1개인 경우 180~350자, 2개 이상인 경우 350~650자 (절대 범위: 150~700자)
- 복합 판례에 한해 최대 2개의 문단으로 나눌 수 있습니다.

[출력 형식]
반드시 아래 JSON 형식으로만 출력해야 하며, 다른 텍스트는 포함하지 마십시오.
{{
  "판례일련번호": "입력과 동일한 값",
  "{field_name}": "작성된 쉬운 요약 텍스트"
}}

[입력 요약]
판례일련번호: {precedent_id}
내용: {generated_summary}
""".strip()


def build_prompt_v10(case: dict[str, Any], field_name: str) -> str:
    """예시를 제거하고 의미 보존을 강화한 v10 프롬프트를 만든다."""
    precedent_id = normalize_text(case.get("판례일련번호"))
    case_name = normalize_text(case.get("사건명"))
    generated_summary = normalize_text(case.get("생성요약"))
    return f"""
너는 AlphaLawVA에서 법률에 익숙하지 않은 20대 초반 사용자를 위한
판례 요약을 작성하는 법률 콘텐츠 편집자다.

[작업과 우선순위]
입력으로 제공된 생성요약을 바탕으로, 사용자가 사건의 상황과 법원의 판단을
이해할 수 있는 쉬운요약을 작성한다.

이 작업은 새로운 법률 해설을 작성하는 작업이 아니다.
입력 생성요약의 의미를 유지하면서 문장 구조와 표현을 자연스럽고 쉽게 바꾸는 작업이다.
규칙이 충돌하면 정확성, 쟁점 보존, 가독성, 분량 순서로 우선한다.

[정확성 규칙 — 최우선]
1. 사실관계, 당사자의 주장, 법률요건, 판단 이유와 법적 효과는 생성요약에 명시된 내용만 사용한다. 입력에 없는 정의·요건·이유를 일반적인 법률 지식으로 보충하지 않는다.
2. 사실관계, 당사자의 주장, 법원의 판단을 서로 혼동하지 않는다.
3. 누가 무엇을 했는지, 권리의 주체와 목적물이 무엇인지 바꾸지 않는다. 법적 지위나 권리 관계를 일상적인 소유 관계로 임의 치환하지 않는다.
4. 법률용어의 의미, 판결 결과와 절차적 상태를 바꾸지 않는다. 입력에 명시되지 않은 최종 결론이나 후속 절차를 추론하여 추가하지 않는다.
5. 사건명은 문맥 확인에만 사용한다. 생성요약에 없는 사실을 사건명에서 추론하여 추가하지 않는다.

[쉽게 쓰는 방법]
1. 생성요약에서 확인되는 범위 안에서 '누가 무엇을 했는지', '무엇이 문제가 되었는지', '법원이 어떻게 판단했는지'가 드러나게 쓴다. 입력에 없는 항목은 억지로 채우지 않는다.
2. 긴 문장을 나누고, 추상적인 명사 나열보다 당사자의 행동과 관계가 보이는 문장으로 바꾼다.
3. 일상적인 표현으로 바꿔도 법적 의미가 완전히 동일한 경우에만 표현을 바꾼다. 의미가 달라질 가능성이 있으면 입력 표현을 유지한다.
4. 정확성에 필요한 법률용어는 유지한다. 용어 자체를 사전처럼 정의하지 말고, 생성요약에 이미 적힌 관계나 효과만 쉬운 문장으로 다시 쓴다.
5. 생성요약만으로 뜻을 안전하게 풀 수 없는 법률용어는 억지로 설명하거나 괄호 정의를 만들지 말고 원래 용어를 그대로 쓴다.
6. 문장은 자연스럽고 충분히 쉽게 쓰되, 어린이에게 설명하듯 가볍거나 유치하게 쓰지 않는다.

[문체와 분량]
1. 모든 문장은 '~다', '~했다', '~보았다', '~판단했다'와 같은 평서형으로 쓴다. '~습니다', '~입니다', '~합니다'는 사용하지 않는다.
2. '이 판례는'으로 시작하지 않는다.
3. 모델의 평가를 덧붙이거나 사용자에게 소송·신고·상담 등의 행동을 권하지 않는다.
4. 사건번호, 법원명, 선고일자와 생성요약에 없는 소송 절차는 작성하지 않는다.
5. 같은 내용을 반복하지 않으며, 서로 다른 쟁점이 여러 개일 때만 최대 2개 문단으로 나눈다.

- 쟁점이 1개인 일반 판례는 대체로 120~350자 안에서 작성한다.
- 쟁점이 2개 이상인 복합 판례는 대체로 250~600자 안에서 작성한다.
- 위 범위의 시작 글자 수는 최소 조건이 아니다. 입력이 짧으면 필요한 내용을 모두 담고 더 짧게 끝내도 된다.
- 전체 최대 허용 길이는 600자다.
- 글자 수를 맞추기 위해 사실을 추가하거나 같은 내용을 반복해서는 안 된다.

[출력 전 확인]
- 쉬운요약의 모든 사실과 법적 판단을 생성요약에서 직접 확인할 수 있는지 확인한다.
- 법률용어의 의미와 권리 주체를 바꾸지 않았는지 확인한다.
- 판결 결과와 절차적 상태를 바꾸지 않았는지 확인한다.
- 평서형과 분량 기준을 지켰는지 확인한다.
- 확인 과정은 출력하지 않는다.

[출력 규칙]
- 반드시 유효한 JSON 객체 하나만 출력한다.
- JSON 객체 외에는 어떤 문장도 출력하지 않는다.
- 제목, 설명, 인사말, 주석, 마크다운 코드 블록을 붙이지 않는다.
- 판례일련번호는 입력값과 완전히 동일하게 유지한다.
- JSON의 필드명은 반드시 "판례일련번호"와 "{field_name}"만 사용한다.
- {field_name}에 문단 구분이 필요하면 문자열 안에서 \\n\\n을 사용한다.
- JSON 문자열 안의 큰따옴표와 줄바꿈은 올바르게 이스케이프한다.
- 출력 직전에 {field_name}의 모든 문장이 '~다'체인지 확인하고, '~습니다', '~입니다', '~합니다'가 있으면 '~다'체로 고친다.

[출력 형식]

{{
  "판례일련번호": "입력과 동일한 판례일련번호",
  "{field_name}": "입력의 의미를 유지한 600자 이하의 쉬운 판례 요약"
}}

[입력]
판례일련번호:
{precedent_id}

사건명:
{case_name}

생성요약:
{generated_summary}
""".strip()


def build_prompt_v9(case: dict[str, Any], field_name: str) -> str:
    """v10 직전의 구체적 표현 예시와 강제 분량 기준을 사용한 v9 프롬프트를 만든다."""
    prompt = build_prompt_v10(case, field_name)
    replacements = {
        "3. 누가 무엇을 했는지, 권리의 주체와 목적물이 무엇인지 바꾸지 않는다. 법적 지위나 권리 관계를 일상적인 소유 관계로 임의 치환하지 않는다.": (
            "3. 누가 무엇을 했는지, 권리의 주체와 목적물이 무엇인지 바꾸지 않는다. "
            "특히 '본인'이나 '권리자'를 '원래 주인', '실제 주인', '소유자'로 바꾸지 않는다."
        ),
        "4. 법률용어의 의미, 판결 결과와 절차적 상태를 바꾸지 않는다. 입력에 명시되지 않은 최종 결론이나 후속 절차를 추론하여 추가하지 않는다.": (
            "4. 법률용어의 의미, 판결 결과와 절차적 상태를 바꾸지 않는다. "
            "파기환송은 책임이나 결론이 최종 확정된 것처럼 쓰지 않고, 기존 판결을 취소하고 다시 심리하도록 돌려보낸 것으로 쓴다."
        ),
        "3. 일상적인 표현으로 바꿔도 법적 의미가 완전히 동일한 경우에만 표현을 바꾼다. 의미가 달라질 가능성이 있으면 입력 표현을 유지한다.": (
            "3. 문맥상 법적 의미가 같을 때에는 '청구했다'를 '요구했다', '승계했다'를 '이어받았다', "
            "'인용했다'를 '요구를 받아들였다', '기각했다'를 '요구를 받아들이지 않았다'처럼 바꿀 수 있다."
        ),
        "- 쟁점이 1개인 일반 판례는 대체로 120~350자 안에서 작성한다.\n"
        "- 쟁점이 2개 이상인 복합 판례는 대체로 250~600자 안에서 작성한다.\n"
        "- 위 범위의 시작 글자 수는 최소 조건이 아니다. 입력이 짧으면 필요한 내용을 모두 담고 더 짧게 끝내도 된다.\n"
        "- 전체 최대 허용 길이는 600자다.": (
            "- 쟁점이 1개인 일반 판례: 180~350자\n"
            "- 쟁점이 2개 이상인 복합 판례: 350~650자\n"
            "- 전체 허용 범위: 150~700자"
        ),
        f'  "{field_name}": "입력의 의미를 유지한 600자 이하의 쉬운 판례 요약"': (
            f'  "{field_name}": "150~700자의 쉬운 판례 요약"'
        ),
    }
    for old, new in replacements.items():
        if old not in prompt:
            raise RuntimeError(f"v9 프롬프트 복원 대상 문구를 찾지 못했습니다: {old}")
        prompt = prompt.replace(old, new)
    return prompt


def build_prompt_v11(case: dict[str, Any], field_name: str) -> str:
    """Gemini용 구조를 적용하고 쉬운 표현과 의미 보존을 함께 강조한 v11 프롬프트를 만든다."""
    precedent_id = normalize_text(case.get("판례일련번호"))
    generated_summary = normalize_text(case.get("생성요약"))
    return f"""
당신은 법률에 익숙하지 않은 20~30대 일반 사용자를 위해 판례를 쉽게 설명하는 리걸 에디터입니다.
아래의 [생성요약]을 읽고, 법적 의미를 유지하면서 일반 사용자가 이해하기 쉬운 [쉬운요약]으로 변환하십시오.

### 1. 작업 목표 (Task Objective)
* **단순 요약 금지:** 단순히 길이를 줄이거나 단어 몇 개만 바꾸는 것이 아닙니다. 당사자의 행동, 분쟁 원인, 법원 판단과 결과가 사건의 흐름에 따라 드러나도록 풀어서 설명하십시오.
* **눈높이 맞춤:** 법률 문서를 읽는 느낌이 아니라, 내용을 정확히 이해한 성인이 옆에서 쉽게 설명해주는 톤을 유지하십시오. 단, 유치하거나 지나치게 가벼운 표현은 배제하십시오.

### 2. 정확성 원칙 (Strict Accuracy - 고위험)
* **외부 지식 차단:** [생성요약]에 명시된 사실관계, 주장, 법률요건, 판단 이유, 결론만 사용하십시오. 입력에 없는 사실이나 법리를 추론하여 덧붙이지 마십시오.
* **핵심 요소 보존:** 입력 생성요약에 포함된 핵심 쟁점, 권리관계, 판단 조건, 판단 이유와 판결 결과를 생략하거나 변경하지 마십시오.
* **절차적 상태 유지:** 파기, 파기환송, 기각, 인용 등 법원의 확정 범위와 소송 상태를 다른 결론으로 왜곡하지 마십시오.

### 3. 쉬운 표현 변환 규칙 (Simplification Rules)
* **생활어 변환:** 어려운 법률용어는 법적 의미가 훼손되지 않는 선에서 자연스러운 생활어로 변경하십시오.
* **예외 (핵심 용어 유지):** 임대인, 임차인, 보증금, 몰취 등 문맥상 이해 가능한 용어나, 생활어로 바꿀 경우 법적 의미가 달라질 위험이 있는 핵심 용어는 원래 표현을 유지하십시오.
* **문맥적 풀이:** 외부 지식으로 사전적 정의를 만들지 말고, [생성요약]에 나타난 당사자 간의 관계와 효과를 주변 서술어를 통해 쉽게 풀어주십시오.
* **문장 분할:** 하나의 문장에 너무 많은 조건과 결과가 압축되어 있다면, "누가 무엇을 했는지 -> 무엇이 문제인지 -> 법원이 왜 그렇게 판단했는지"의 흐름에 따라 여러 문장으로 나누십시오.

### 4. 문체 및 분량 제약 (Style & Length Constraints)
* **평서형 강제:** 모든 문장은 '~다', '~했다', '~보았다', '~판단했다'로 마무리하십시오. 존댓말은 사용하지 마십시오.
* **금지 표현:** "이 판례는"으로 시작하지 마십시오. 소송, 신고, 상담 등 행동을 권유하지 마십시오. 사건번호, 법원명, 선고일자를 포함하지 마십시오.
* **분량:** 일반 판례는 150~450자로 작성하십시오. 입력이 매우 짧은 경우에는 150자 미만도 허용합니다. 쟁점이 여러 개인 복합 판례는 최대 600자까지 허용하며, 필요한 경우 두 문단으로 나누십시오. 분량을 채우기 위해 내용을 반복하지 마십시오.

### 5. 출력 형식 (JSON Output Only)
* **엄격한 JSON 강제:** 어떠한 경우에도 마크다운 코드 블록, 제목, 설명, 인사말을 출력하지 마십시오. 오직 아래 구조의 유효한 JSON 객체 하나만 텍스트로 반환하십시오.
* 문단을 구분할 때는 JSON 문자열 내부에 \\n\\n을 사용하십시오.

{{
  "판례일련번호": "입력과 동일한 값",
  "{field_name}": "법적 의미를 유지하면서 생활어로 풀어쓴 쉬운 판례 요약 텍스트"
}}

---
[입력 데이터]
판례일련번호: {precedent_id}
생성요약: {generated_summary}
""".strip()


def build_prompt_v12(case: dict[str, Any], field_name: str) -> str:
    """법률용어를 생활어로 우선 변환하도록 강화한 v12 프롬프트를 만든다."""
    prompt = build_prompt_v11(case, field_name)
    old = """* **생활어 변환:** 어려운 법률용어는 법적 의미가 훼손되지 않는 선에서 자연스러운 생활어로 변경하십시오.
* **예외 (핵심 용어 유지):** 임대인, 임차인, 보증금, 몰취 등 문맥상 이해 가능한 용어나, 생활어로 바꿀 경우 법적 의미가 달라질 위험이 있는 핵심 용어는 원래 표현을 유지하십시오.
* **문맥적 풀이:** 외부 지식으로 사전적 정의를 만들지 말고, [생성요약]에 나타난 당사자 간의 관계와 효과를 주변 서술어를 통해 쉽게 풀어주십시오."""
    new = """* **법률용어 최소화:** 어려운 법률용어는 최대한 사용하지 말고, 입력 생성요약에 나타난 당사자의 행동·관계·효과를 바탕으로 쉬운 생활어로 풀어쓰십시오. 필요한 경우 쉬운 설명을 먼저 쓴 뒤 원래 법률용어를 괄호 안에 한 번만 표시하십시오. 입력에 없는 정의나 법률 지식을 추가해서 설명하지 마십시오."""
    if old not in prompt:
        raise RuntimeError("v12 프롬프트 수정 대상 문구를 찾지 못했습니다.")
    return prompt.replace(old, new)


def build_prompt(case: dict[str, Any], field_name: str, prompt_profile: str) -> str:
    """선택한 버전의 쉬운요약 프롬프트를 만든다."""
    if prompt_profile == "v7":
        return build_prompt_v7(case, field_name)
    if prompt_profile == "v9":
        return build_prompt_v9(case, field_name)
    if prompt_profile == "v11":
        return build_prompt_v11(case, field_name)
    if prompt_profile == "v12":
        return build_prompt_v12(case, field_name)
    return build_prompt_v10(case, field_name)


def selected_prompt_version(args: argparse.Namespace) -> str:
    """실행에 실제 사용된 프롬프트 버전을 반환한다."""
    return PROMPT_VERSIONS[args.prompt_profile]


def parse_json_object(text: str) -> dict[str, Any]:
    """LLM 응답에서 JSON 객체만 안전하게 뽑아 파싱한다."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or start >= end:
            raise
        return json.loads(stripped[start : end + 1])


def get_gemini_api_key() -> str:
    """Gemini API 키를 환경변수에서 읽는다."""
    load_env_file()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(".env 또는 환경변수에 GEMINI_API_KEY를 설정해야 Gemini 쉬운요약 생성을 실행할 수 있다.")
    return api_key


def extract_gemini_text(response_payload: dict[str, Any]) -> str:
    """Gemini generateContent 응답에서 텍스트 파트를 합쳐 반환한다."""
    candidates = response_payload.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini 응답에 candidates가 없습니다: {response_payload}")
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    texts = [str(part.get("text", "")) for part in parts if part.get("text")]
    if not texts:
        raise RuntimeError(f"Gemini 응답에 text part가 없습니다: {response_payload}")
    return "\n".join(texts)


def call_gemini(prompt: str, model: str, timeout: float) -> tuple[str, dict[str, int]]:
    """Gemini API에 프롬프트를 보내고 원문 응답 텍스트를 받는다."""
    api_key = get_gemini_api_key()
    model_name = normalize_gemini_model_name(model)
    endpoint = GEMINI_GENERATE_URL_TEMPLATE.format(model=model_name)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.15,
            "responseMimeType": "application/json",
        },
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini 호출 실패: HTTP {exc.code}; body={error_body[:1200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Gemini 호출 실패: {exc}") from exc
    response_payload = json.loads(body)
    usage = response_payload.get("usageMetadata") or {}
    prompt_tokens = int(usage.get("promptTokenCount") or 0)
    total_tokens = int(usage.get("totalTokenCount") or 0)
    output_tokens = max(0, total_tokens - prompt_tokens)
    return extract_gemini_text(response_payload), {
        "input_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def call_ollama(prompt: str, args: argparse.Namespace) -> tuple[str, dict[str, int]]:
    """Ollama 호환 API에 프롬프트를 보내고 원문 응답 텍스트를 받는다."""
    endpoint = args.ollama_url.rstrip("/") + "/api/generate"
    payload = {
        "model": args.model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "num_ctx": args.ollama_num_ctx,
            "num_predict": args.ollama_num_predict,
        },
    }
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "curl/8.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=args.timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama 호출 실패: HTTP {exc.code}; body={error_body[:1200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Ollama 호출 실패: {exc}") from exc

    response_payload = json.loads(body)
    input_tokens = int(response_payload.get("prompt_eval_count") or 0)
    output_tokens = int(response_payload.get("eval_count") or 0)
    return str(response_payload.get("response") or ""), {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def generate_raw_response(prompt: str, args: argparse.Namespace) -> tuple[str, dict[str, int]]:
    """설정된 provider로 쉬운요약 생성 원문 응답을 받는다."""
    if args.provider == "gemini":
        return call_gemini(prompt, args.gemini_model, args.timeout)
    if args.provider == "ollama":
        return call_ollama(prompt, args)
    raise RuntimeError(f"지원하지 않는 provider입니다: {args.provider}")


def validate_easy_summary(
    case_id: str,
    easy_summary: str,
    prompt_profile: str,
) -> tuple[list[str], list[str]]:
    """쉬운요약 저장 전 실패 사유와 경고를 나누어 확인한다."""
    errors = []
    warnings = []
    if prompt_profile in {"v7", "v9"}:
        if len(easy_summary) < LEGACY_HARD_MIN_EASY_SUMMARY_CHARS:
            errors.append(f"too_short:{len(easy_summary)}")
        elif len(easy_summary) < LEGACY_RECOMMENDED_MIN_EASY_SUMMARY_CHARS:
            warnings.append(f"recommended_too_short:{len(easy_summary)}")
        if len(easy_summary) > LEGACY_RECOMMENDED_MAX_EASY_SUMMARY_CHARS:
            warnings.append(f"recommended_too_long:{len(easy_summary)}")
        if len(easy_summary) > LEGACY_MAX_EASY_SUMMARY_CHARS:
            errors.append(f"too_long:{len(easy_summary)}")
    else:
        if len(easy_summary) < REVIEW_MIN_EASY_SUMMARY_CHARS:
            warnings.append(f"review_too_short:{len(easy_summary)}")
        if len(easy_summary) > MAX_EASY_SUMMARY_CHARS:
            errors.append(f"too_long:{len(easy_summary)}")
    if easy_summary.startswith("이 판례는"):
        errors.append("starts_with_this_precedent")
    if "입니다" in easy_summary or "합니다" in easy_summary or "했습니다" in easy_summary:
        errors.append("formal_polite_style")
    if not case_id:
        errors.append("missing_case_id")
    return errors, warnings


def is_terminal_gemini_error(error: Exception) -> bool:
    """잔액·쿼터·결제처럼 같은 실행에서 계속 실패할 Gemini 오류인지 판단한다."""
    text = str(error).lower()
    return any(
        signal in text
        for signal in [
            "resource_exhausted",
            "insufficient",
            "quota",
            "billing",
            "credit",
            "permission_denied",
        ]
    )


def is_terminal_ollama_proxy_error(error: Exception) -> bool:
    """RunPod 프록시·Cloudflare처럼 재시도해도 같은 실행에서 계속 실패할 오류인지 판단한다."""
    text = str(error).lower()
    return any(
        signal in text
        for signal in [
            "browser_signature_banned",
            "error 1010",
            "access denied",
            "cloudflare",
            "403",
        ]
    )


def write_manifest(paths: RunPaths, args: argparse.Namespace, stats: RunStats) -> None:
    """실행 통계와 설정을 manifest로 저장한다."""
    pricing = GEMINI_PRICING_USD_PER_MILLION.get(selected_model_name(args), {})
    estimated_cost_usd = None
    if args.provider == "gemini" and pricing:
        estimated_cost_usd = round(
            stats.input_token_count / 1_000_000 * pricing["input"]
            + stats.output_token_count / 1_000_000 * pricing["output"],
            6,
        )
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "started_at": stats.started_at,
        "finished_at": now_utc_iso(),
        "provider": args.provider,
        "model": selected_model_name(args),
        "prompt_version": selected_prompt_version(args),
        "ollama_url": args.ollama_url if args.provider == "ollama" else None,
        "ollama_options": (
            {"num_ctx": args.ollama_num_ctx, "num_predict": args.ollama_num_predict}
            if args.provider == "ollama"
            else None
        ),
        "field_name": args.field_name,
        "skip_final_write": args.skip_final_write,
        "ignore_final_field": args.ignore_final_field,
        "sample_size": args.sample_size,
        "total_targets": stats.total_targets,
        "generated_count": stats.generated_count,
        "skipped_existing_count": stats.skipped_existing_count,
        "failure_count": stats.failure_count,
        "token_usage": {
            "input_tokens": stats.input_token_count,
            "output_tokens": stats.output_token_count,
            "total_tokens": stats.total_token_count,
        },
        "pricing_usd_per_million_tokens": pricing or None,
        "estimated_cost_usd": estimated_cost_usd,
        "stopped_reason": stats.stopped_reason,
        "results_path": str(paths.results),
        "failures_path": str(paths.failures),
    }
    paths.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def generate_one(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    """단일 판례의 쉬운요약을 생성하고 final case JSON에 저장한다."""
    case = load_final_case(path)
    case_id = normalize_text(case.get("판례일련번호"))
    prompt = build_prompt(case, args.field_name, args.prompt_profile)
    raw_response, token_usage = generate_raw_response(prompt, args)
    parsed = parse_json_object(raw_response)
    easy_summary = normalize_easy_summary(parsed.get(args.field_name))
    validation_errors, validation_warnings = validate_easy_summary(
        case_id,
        easy_summary,
        args.prompt_profile,
    )
    if validation_errors:
        raise ValueError(f"쉬운요약 검증 실패: {validation_errors}; summary={easy_summary[:300]}")

    if not args.skip_final_write:
        case[args.field_name] = easy_summary
        write_final_case(path, case)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "판례일련번호": case_id,
        "사건명": normalize_text(case.get("사건명")),
        "생성요약": normalize_text(case.get("생성요약")),
        args.field_name: easy_summary,
        "provider": args.provider,
        "model": selected_model_name(args),
        "prompt_version": selected_prompt_version(args),
        "token_usage": token_usage,
        "검증경고": validation_warnings,
        "source_path": str(path.relative_to(PROJECT_ROOT)),
        "final_case_updated": not args.skip_final_write,
    }


def main() -> None:
    """생성요약이 있는 final case를 순회하며 쉬운요약을 생성한다."""
    args = parse_args()
    paths = build_run_paths(build_run_name(args))
    target_paths = collect_target_paths(args)
    processed_ids = load_processed_ids(paths.results)
    stats = RunStats(started_at=now_utc_iso(), total_targets=len(target_paths))

    print(f"쉬운요약 생성 대상: {len(target_paths)}건")
    print(f"결과 파일: {paths.results}")
    if args.dry_run:
        if target_paths:
            case = load_final_case(target_paths[0])
            print("\n--- 첫 대상 프롬프트 미리보기 ---")
            print(build_prompt(case, args.field_name, args.prompt_profile)[:3000])
        print("dry-run이므로 API를 호출하지 않습니다.")
        write_manifest(paths, args, stats)
        return

    start = time.time()
    for index, path in enumerate(target_paths, start=1):
        case = load_final_case(path)
        case_id = normalize_text(case.get("판례일련번호"))
        final_field_exists = normalize_text(case.get(args.field_name))
        if not args.overwrite and (
            case_id in processed_ids or (final_field_exists and not args.ignore_final_field)
        ):
            stats.skipped_existing_count += 1
            continue

        try:
            row = generate_one(path, args)
            append_jsonl(paths.results, row)
            stats.generated_count += 1
            token_usage = row.get("token_usage") or {}
            stats.input_token_count += int(token_usage.get("input_tokens") or 0)
            stats.output_token_count += int(token_usage.get("output_tokens") or 0)
            stats.total_token_count += int(token_usage.get("total_tokens") or 0)
        except Exception as exc:
            stats.failure_count += 1
            append_jsonl(
                paths.failures,
                {
                    "failed_at": now_utc_iso(),
                    "판례일련번호": case_id,
                    "source_path": str(path.relative_to(PROJECT_ROOT)),
                    "error": str(exc),
                },
            )
            print(f"[{index}/{len(target_paths)}] 실패: {case_id} {exc}", flush=True)
            if args.provider == "gemini" and is_terminal_gemini_error(exc):
                stats.stopped_reason = f"terminal_gemini_error: {str(exc)[:500]}"
                break
            if args.provider == "ollama" and is_terminal_ollama_proxy_error(exc):
                stats.stopped_reason = f"terminal_ollama_proxy_error: {str(exc)[:500]}"
                break
        else:
            if stats.generated_count % max(args.progress_every, 1) == 0:
                elapsed = time.time() - start
                print(
                    f"진행 {index}/{len(target_paths)} 생성 {stats.generated_count}건 "
                    f"스킵 {stats.skipped_existing_count}건 실패 {stats.failure_count}건 "
                    f"경과 {elapsed:.1f}초",
                    flush=True,
                )
            time.sleep(args.delay)

    write_manifest(paths, args, stats)
    print(
        f"완료: 생성 {stats.generated_count}건, 스킵 {stats.skipped_existing_count}건, "
        f"실패 {stats.failure_count}건, 중단사유 {stats.stopped_reason}"
    )


if __name__ == "__main__":
    main()
