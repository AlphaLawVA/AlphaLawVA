# generate_easy_summaries.py
"""
Description: 최종 판례 JSON의 생성요약을 Claude Sonnet Batch API로 쉬운요약으로 다시 작성한다.
구조화된 결과와 실패 내역, 실행 비용 manifest를 저장하고 선택 시 final_cases를 갱신한다.
Author: choeminju
Date: 2026-09-14
Before:
    - local_data/precedents/processed/final_cases/에 생성요약이 포함된 최종 판례 JSON이 있다.
    - .env에 ANTHROPIC_API_KEY 또는 CLAUDE_API_KEY가 설정되어 있다.
After:
    - easy_summaries/에 배치 입력, 결과, 실패 내역, manifest가 저장된다.
    - --write-final 사용 시 각 final case JSON에 쉬운요약 필드가 추가된다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from precedent_config import PROCESSED_DIR, load_env_file, now_utc_iso


FINAL_CASES_DIR = PROCESSED_DIR / "final_cases"
EASY_SUMMARY_DIR = PROCESSED_DIR / "easy_summaries"
DEFAULT_FIELD_NAME = "쉬운요약"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
ANTHROPIC_BATCHES_URL = "https://api.anthropic.com/v1/messages/batches"
ANTHROPIC_API_VERSION = "2023-06-01"
SCHEMA_VERSION = "precedent_easy_summary.v3"
MANIFEST_SCHEMA_VERSION = "precedent_easy_summary_manifest.v3"
PROMPT_VERSION = (
    "precedent_easy_summary_no_db_natural.v19_definition_in_context_clear_structure"
)
PROMPT_CACHE_TTL = "1h"
OUTPUT_RETRY_REMINDER = """

[재시도 출력 형식 확인]
- precedent_id에는 위 입력의 판례일련번호를 한 글자도 바꾸지 말고 그대로 쓴다.
- easy_summary는 비워 두지 말고 완성된 쉬운요약을 쓴다.
""".rstrip()
MAX_EASY_SUMMARY_CHARS = 700
REVIEW_MIN_EASY_SUMMARY_CHARS = 80
ANTHROPIC_PRICING_USD_PER_MILLION = {
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
}


@dataclass(frozen=True)
class RunPaths:
    inputs: Path
    results: Path
    failures: Path
    manifest: Path


@dataclass
class RunStats:
    started_at: str
    total_targets: int
    generated_count: int = 0
    failure_count: int = 0
    input_token_count: int = 0
    uncached_input_token_count: int = 0
    cache_creation_input_token_count: int = 0
    cache_read_input_token_count: int = 0
    output_token_count: int = 0
    total_token_count: int = 0
    stopped_reason: str | None = None


def parse_args() -> argparse.Namespace:
    """Claude Sonnet Batch 쉬운요약 생성 옵션을 정의한다."""
    load_env_file()
    parser = argparse.ArgumentParser(
        description="Generate easy precedent summaries with Claude Sonnet Batch API."
    )
    parser.add_argument("--final-cases-dir", type=Path, default=FINAL_CASES_DIR)
    parser.add_argument("--output-dir", type=Path, default=EASY_SUMMARY_DIR)
    parser.add_argument(
        "--anthropic-model",
        default=os.environ.get(
            "PRECEDENT_EASY_SUMMARY_ANTHROPIC_MODEL",
            DEFAULT_ANTHROPIC_MODEL,
        ),
    )
    parser.add_argument("--field-name", default=DEFAULT_FIELD_NAME)
    parser.add_argument("--run-name", default=None)
    parser.add_argument(
        "--case-ids",
        default=None,
        help="처리할 쉼표 구분 판례일련번호. 생략하면 생성요약이 있는 전체 판례를 대상으로 한다.",
    )
    parser.add_argument("--case-ids-from-file", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--sample-size",
        type=int,
        default=None,
        help="생성요약 길이 구간별로 섞은 테스트 샘플 개수.",
    )
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=4096,
        help="Claude 응답의 최대 출력 토큰 수.",
    )
    parser.add_argument(
        "--batch-submit",
        action="store_true",
        help="Anthropic Message Batch를 제출하고 즉시 종료한다.",
    )
    parser.add_argument(
        "--batch-collect",
        action="store_true",
        help="완료된 Batch 결과를 수집하고 검증한다.",
    )
    parser.add_argument("--batch-id", default=None)
    parser.add_argument(
        "--output-retry-reminder",
        action="store_true",
        help="출력 ID 오류나 빈 요약 재시도 시 판례별 입력 뒤에 형식 확인 문구를 추가한다.",
    )
    parser.add_argument(
        "--write-final",
        action="store_true",
        help="검증을 통과한 쉬운요약을 final case JSON에 반영한다.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="--write-final 시 기존 쉬운요약이 있어도 덮어쓴다.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="배치 입력과 manifest만 저장하고 유료 API를 호출하지 않는다.",
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


def source_summary_hash(summary: str) -> str:
    """생성요약 변경 여부를 확인할 SHA-256 해시를 만든다."""
    return hashlib.sha256(normalize_text(summary).encode("utf-8")).hexdigest()


def sanitize_run_name(value: str) -> str:
    """실행 이름에 안전한 파일명 문자열만 남긴다."""
    return re.sub(r"[^A-Za-z0-9가-힣_.-]+", "-", value).strip("-")


def build_run_paths(output_dir: Path, run_name: str) -> RunPaths:
    """실행 이름에 대응되는 입출력 파일 경로를 만든다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    return RunPaths(
        inputs=output_dir / f"{run_name}_inputs.jsonl",
        results=output_dir / f"{run_name}_results.jsonl",
        failures=output_dir / f"{run_name}_failures.jsonl",
        manifest=output_dir / f"{run_name}_manifest.json",
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 파일을 행 목록으로 읽는다."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig") as file:
        return [json.loads(line) for line in file if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """행 목록을 JSONL 파일로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """한 행을 JSONL 파일에 추가한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False) + "\n")


def reset_run_outputs(paths: RunPaths) -> None:
    """결과 수집 전에 같은 실행의 이전 결과와 실패 기록을 비운다."""
    for path in (paths.results, paths.failures):
        if path.exists():
            path.unlink()


def load_ids_from_file(path: Path) -> list[str]:
    """JSON, JSONL, 일반 텍스트에서 판례 ID를 읽는다."""
    if not path.exists():
        raise FileNotFoundError(f"판례 ID 파일이 없습니다: {path}")
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
        values = payload if isinstance(payload, list) else payload.get("case_ids", [])
        return [str(value).strip() for value in values if str(value).strip()]
    if path.suffix.lower() == ".jsonl":
        ids = []
        for row in read_jsonl(path):
            case_id = row.get("판례일련번호") or row.get("precedent_id") or row.get("id")
            if case_id is not None:
                ids.append(str(case_id).strip())
        return [case_id for case_id in ids if case_id]
    return [value for value in re.split(r"[\s,]+", text) if value]


def requested_case_ids(args: argparse.Namespace) -> list[str] | None:
    """명령행에서 지정한 판례 ID를 중복 없이 읽는다."""
    values = []
    if args.case_ids:
        values.extend(value.strip() for value in args.case_ids.split(","))
    if args.case_ids_from_file:
        values.extend(load_ids_from_file(args.case_ids_from_file))
    if not values:
        return None
    return list(dict.fromkeys(value for value in values if value))


def load_final_case(path: Path) -> dict[str, Any]:
    """최종 판례 JSON을 읽는다."""
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_final_case(path: Path, case: dict[str, Any]) -> None:
    """최종 판례 JSON을 보기 좋은 형식으로 저장한다."""
    path.write_text(
        json.dumps(case, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def collect_target_paths(args: argparse.Namespace) -> list[Path]:
    """생성요약이 있는 처리 대상 판례 파일을 결정한다."""
    case_ids = requested_case_ids(args)
    if case_ids is None:
        paths = sorted(args.final_cases_dir.glob("*.json"))
    else:
        paths = [args.final_cases_dir / f"{case_id}.json" for case_id in case_ids]
        missing = [str(path) for path in paths if not path.exists()]
        if missing:
            raise FileNotFoundError(f"판례 파일이 없습니다: {missing[:10]}")

    targets = []
    for path in paths:
        case = load_final_case(path)
        if not normalize_text(case.get("생성요약")):
            continue
        if (
            args.write_final
            and not args.overwrite
            and normalize_text(case.get(args.field_name))
        ):
            continue
        targets.append(path)
    if args.sample_size is not None:
        targets = build_length_balanced_sample(targets, args.sample_size)
    if args.limit is not None:
        targets = targets[: args.limit]
    return targets


def build_length_balanced_sample(paths: list[Path], sample_size: int) -> list[Path]:
    """생성요약 길이 순서 전체에서 고르게 테스트 판례를 고른다."""
    if sample_size <= 0:
        return []
    if sample_size >= len(paths):
        return paths
    ranked = sorted(
        paths,
        key=lambda path: len(normalize_text(load_final_case(path).get("생성요약"))),
    )
    if sample_size == 1:
        return [ranked[len(ranked) // 2]]
    indexes = {
        round(index * (len(ranked) - 1) / (sample_size - 1))
        for index in range(sample_size)
    }
    return [ranked[index] for index in sorted(indexes)]


def build_prompt_input(case: dict[str, Any]) -> str:
    """판례마다 달라지는 프롬프트 입력 JSON을 만든다."""
    return json.dumps(
        {
            "판례일련번호": str(case.get("판례일련번호") or ""),
            "사건명": normalize_text(case.get("사건명")),
            "생성요약": normalize_text(case.get("생성요약")),
        },
        ensure_ascii=False,
        indent=2,
    )


def build_prompt(case: dict[str, Any]) -> str:
    """용어 DB 없이 생성요약을 자연스럽게 다시 쓰는 프롬프트를 만든다."""
    input_json = build_prompt_input(case)
    return f"""
너는 AlphaLawVA에서 법과 부동산 거래에 익숙하지 않은 20대 사회초년생을 위한 판례 쉬운요약을 작성하는 리걸 에디터다.

[우선순위]
규칙이 충돌하면 다음 순서를 따른다.

1. 정확성(사실, 당사자, 권리·의무의 방향, 조건, 재판절차 상태, 결론의 보존)
2. 쉬운 표현
3. 문체와 분량

[자료와 근거]

- 이 사건의 사실관계, 쟁점, 법원의 판단과 결론은 입력된 생성요약만을 근거로 한다.
- 용어 사전은 제공되지 않는다. 전문용어의 일반적인 뜻을 쉬운 말로 바꿀 때에 한해, 일반적으로 알려진 법률·부동산 지식을 사용할 수 있다.
- 사건명은 문맥을 파악하는 용도로만 쓴다. 사건명에서 사실을 추론하지 않는다.
- 생성요약에 없는 사건의 사실, 당사자의 행동, 시기와 금액, 조건, 인과관계, 동기·목적·의도, 판단 이유와 법적 결론을 추가하지 않는다.

[작업]
생성요약의 의미와 법적 결론을 정확하게 보존하면서, 법률 지식이 없는 독자도 사건의 내용과 법원의 판단을 이해할 수 있는 쉬운요약을 작성한다.

[전문용어를 풀어 쓰는 원칙]

1. 풀어 쓸 대상: 법률, 재판절차, 부동산 거래, 등기, 임대차, 금융 등의 전문용어와 어려운 한자어, 판결문 특유의 표현이다. 일상에서 흔히 쓰는 말(계약, 보증금, 소송 등)은 그대로 써도 된다.
2. 풀어 쓰는 방식: 사전식 정의를 덧붙이지 않는다. 그 용어가 이 문맥에서 가리키는 권리, 의무, 절차, 행동 또는 기능을 문장 안에 자연스럽게 녹여 쓴다. 괄호 안에 설명을 넣거나 용어 설명 목록처럼 쓰지 않는다. 다음 예시는 형식 참고용이며 문구를 그대로 복사하지 않는다.
   - 나쁜 예: "공탁이란 변제할 금전을 공탁소에 맡기는 제도를 말한다."
   - 좋은 예: "갚아야 할 돈을 상대방에게 직접 주지 않고 법원에 맡겨 두었다."
   - 전문용어를 설명하기 위한 별도의 정의 문장을 만들지 않는다.
   - 특히 "○○는/은 … 권리다", "○○란 … 제도다", "○○는 … 절차다" 형식으로 쓰지 않는다.
   - 전문용어의 뜻은 이 사건의 당사자 관계, 행동이나 결과를 설명하는 문장 안에 녹여 쓴다.
3. 일반 지식의 사용 범위: 용어가 가리키는 권리, 의무, 절차, 행동의 일반적인 뜻과 기능을 쉽게 바꾸는 데에만 쓴다. 생성요약에 없는 구체적인 성립 요건, 예외 사유, 당사자의 실제 행동, 이 사건에서의 법적 효과를 새로 만들지 않는다. 이 사건에서의 결과는 생성요약에서 확인되는 범위에서만 설명한다.
4. 안전장치: 정확한 세부 뜻이나 적용 범위를 확신할 수 없으면 이를 추측하지 않는다. 대신 생성요약의 문맥과 해당 용어의 일반적인 의미에서 확인되는 최소한의 기능을 생활어로 설명한다. 정확성을 위해 꼭 필요한 경우에만 원래 용어를 함께 사용할 수 있으며, 원래 용어만 설명 없이 남겨서는 안 된다.
5. 일관성: 같은 용어와 같은 당사자는 요약 전체에서 하나의 표현으로 통일한다.

[당사자 호칭]

- 양수인, 수탁자, 상속인처럼 지위에 따라 권리와 의무가 달라지는 호칭은 그 지위가 드러나게 풀어 쓴다. 양수인처럼 무엇을 넘겨받았는지에 따라 의미가 달라지는 호칭은 생성요약에서 확인되는 대상을 함께 밝혀 풀어 쓴다.
- 예를 들어 채권을 넘겨받았다면 "채권을 넘겨받은 사람"이라고 쓴다. 권리자, 소유자 등 다른 법적 지위로 바꾸지 않는다.

[재판절차 용어]

- 원심, 상고, 항소, 기각, 각하, 인용, 파기, 환송 등은 일상어로 풀어 쓰되, 결과를 최종 승소·패소나 책임 확정처럼 바꾸지 않는다.
- 기각(내용을 따져 받아들이지 않음)과 각하(내용을 따지지 않고 끝냄)는 차이가 드러나게 쓰고 같은 말로 합치지 않는다.
- 파기환송은 "판결을 취소하고 다시 재판하도록 돌려보냈다"처럼 쓰고, 청구가 받아들여진 것처럼 쓰지 않는다.

[쉬운요약 작성 원칙]

- 사건의 상황, 다툰 내용과 법원의 판단이 자연스럽게 이어지도록 작성한다.
- 생성요약이 법원이 밝힌 일반 법리로 시작하면 그 흐름을 유지하되, 일반 법리와 이 사건의 구체적인 사실·적용을 구분해 쓴다. 법리를 이 사건 당사자가 실제로 한 행동처럼 쓰지 않는다.
- 생성요약의 문장을 단순히 줄이거나 그대로 옮기지 말고, 전체 내용을 이해한 뒤 일상적인 문장으로 다시 쓴다.
- 원래 생성요약보다 쉬운 단어와 읽기 편한 문장을 사용한다. 주어와 서술어의 관계가 분명하게 드러나도록 쓰고, 한 문장에 여러 주체, 조건이나 판단을 겹쳐 넣지 않는다. 내용이 복잡해지면 문장을 나눈다.
- 권리가 인정된 경우와 인정되지 않은 경우, 의무가 있는 경우와 없는 경우를 반대로 쓰지 않는다.
- 생성요약에 조건이나 예외가 있으면 임의로 생략하여 결론의 범위를 넓히지 않는다.
- 모든 문장은 '~다', '~했다', '~보았다', '~판단했다'와 같은 평서형으로 작성한다. '~습니다', '~입니다', '~합니다'는 쓰지 않는다.
- '이 판례는'으로 시작하지 않는다. 사건번호, 법원명, 선고일자는 쓰지 않는다. 소송, 신고, 상담 등 독자의 행동을 권하지 않는다.
- 분량은 일반적인 판례는 대체로 150~450자, 쟁점이 여러 개인 복합 판례는 최대 700자로 한다. 핵심 용어를 풀어 쓰느라 450자를 넘기면 700자 이내에서 허용한다. 분량을 맞추려고 내용을 빼거나 새로 만들지 않는다.

[출력 전 자체 점검]
출력하기 전에 다음을 확인한다. 점검 과정은 출력하지 않는다.

- 쉬운요약의 모든 사건 사실과 법원의 판단을 생성요약에서 확인할 수 있는가?
- 당사자, 권리와 의무의 방향, 조건, 예외, 재판절차 상태와 결과가 바뀌지 않았는가?
- 생성요약에 없는 동기, 목적, 이유, 구체적 요건을 추가하지 않았는가?
- 같은 당사자와 같은 용어를 하나의 표현으로 통일했는가?
- 독자가 어려운 전문용어를 설명 없이 그대로 만나지 않는가?
- 정의를 괄호로 붙이거나 사전처럼 나열하지 않았는가?

[출력]
- 유효한 JSON 객체 하나만 출력한다. 마크다운 코드 블록, 제목, 설명은 출력하지 않는다.
- precedent_id에는 입력의 판례일련번호를 그대로 쓴다.
- easy_summary에는 완성된 쉬운요약만 쓴다.
- explained_difficult_terms에는 생성요약에 등장한 전문용어 중 쉬운요약에서 일상적인 말로 의미를 풀어 쓴 원래 용어만 적는다. 풀어 쓴 뒤 원래 용어를 한 번 남긴 경우도 포함한다. 그대로 쓰거나 삭제하기만 한 용어는 제외하고, 같은 용어는 한 번만 적는다.

[입력]
{input_json}
""".strip()


def split_prompt_for_cache(case: dict[str, Any]) -> tuple[str, str]:
    """완성 프롬프트를 동일한 공통 접두부와 판례별 입력으로 나눈다."""
    prompt = build_prompt(case)
    prompt_input = build_prompt_input(case)
    if not prompt.endswith(prompt_input):
        raise ValueError("프롬프트 끝에서 판례별 입력을 찾을 수 없습니다.")
    return prompt[: -len(prompt_input)], prompt_input


def build_input_rows(paths: list[Path]) -> list[dict[str, Any]]:
    """배치 제출과 결과 결합에 사용할 입력 행을 만든다."""
    rows = []
    for path in paths:
        case = load_final_case(path)
        case_id = str(case.get("판례일련번호") or path.stem)
        prompt = build_prompt(case)
        prompt_cache_prefix, prompt_case_input = split_prompt_for_cache(case)
        if prompt_cache_prefix + prompt_case_input != prompt:
            raise ValueError(f"캐시 분리 후 프롬프트가 달라졌습니다: {case_id}")
        rows.append(
            {
                "판례일련번호": case_id,
                "사건명": normalize_text(case.get("사건명")),
                "생성요약": normalize_text(case.get("생성요약")),
                "source_path": str(path),
                "prompt": prompt,
                "prompt_cache_prefix": prompt_cache_prefix,
                "prompt_case_input": prompt_case_input,
            }
        )
    return rows


def add_output_retry_reminder(input_rows: list[dict[str, Any]]) -> None:
    """재시도 대상의 가변 입력에만 ID와 빈 요약 방지 문구를 추가한다."""
    for row in input_rows:
        row["prompt_case_input"] = (
            f"{row['prompt_case_input']}{OUTPUT_RETRY_REMINDER}"
        )
        row["prompt"] = f"{row['prompt_cache_prefix']}{row['prompt_case_input']}"
        row["output_retry_reminder"] = True


def get_anthropic_api_key() -> str:
    """Anthropic API 키를 환경변수에서 읽는다."""
    load_env_file()
    api_key = (
        os.environ.get("ANTHROPIC_API_KEY", "").strip()
        or os.environ.get("CLAUDE_API_KEY", "").strip()
    )
    if not api_key:
        raise RuntimeError(
            ".env 또는 환경변수에 ANTHROPIC_API_KEY 또는 CLAUDE_API_KEY를 설정해야 한다."
        )
    return api_key


def anthropic_request_headers() -> dict[str, str]:
    """Anthropic REST 요청 공통 헤더를 만든다."""
    return {
        "Content-Type": "application/json",
        "x-api-key": get_anthropic_api_key(),
        "anthropic-version": ANTHROPIC_API_VERSION,
    }


def build_output_schema() -> dict[str, Any]:
    """Claude 구조화 출력에 사용할 최소 JSON schema를 만든다."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "precedent_id": {"type": "string"},
            "easy_summary": {"type": "string"},
            "explained_difficult_terms": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "precedent_id",
            "easy_summary",
            "explained_difficult_terms",
        ],
    }


def build_message_params(
    input_row: dict[str, Any],
    model: str,
    max_output_tokens: int,
) -> dict[str, Any]:
    """Anthropic Batch 요청 한 건의 Messages 파라미터를 만든다."""
    prompt = str(input_row["prompt"])
    prompt_cache_prefix = str(input_row["prompt_cache_prefix"])
    prompt_case_input = str(input_row["prompt_case_input"])
    if prompt_cache_prefix + prompt_case_input != prompt:
        raise ValueError(
            f"캐시 메시지가 원본 프롬프트와 다릅니다: {input_row['판례일련번호']}"
        )
    return {
        "model": model.strip(),
        "max_tokens": max_output_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt_cache_prefix,
                        "cache_control": {
                            "type": "ephemeral",
                            "ttl": PROMPT_CACHE_TTL,
                        },
                    },
                    {"type": "text", "text": prompt_case_input},
                ],
            }
        ],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": build_output_schema(),
            }
        },
    }


def submit_batch(
    input_rows: list[dict[str, Any]],
    model: str,
    max_output_tokens: int,
    timeout: float,
) -> dict[str, Any]:
    """Anthropic Message Batch를 제출한다."""
    requests = [
        {
            "custom_id": str(row["판례일련번호"]),
            "params": build_message_params(
                row,
                model,
                max_output_tokens,
            ),
        }
        for row in input_rows
    ]
    request = Request(
        ANTHROPIC_BATCHES_URL,
        data=json.dumps({"requests": requests}, ensure_ascii=False).encode("utf-8"),
        headers=anthropic_request_headers(),
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Anthropic batch 제출 실패: HTTP {exc.code}; body={error_body[:1200]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Anthropic batch 제출 실패: {exc}") from exc


def retrieve_batch(batch_id: str, timeout: float) -> dict[str, Any]:
    """Anthropic Message Batch 상태를 조회한다."""
    request = Request(
        f"{ANTHROPIC_BATCHES_URL}/{batch_id}",
        headers=anthropic_request_headers(),
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Anthropic batch 조회 실패: HTTP {exc.code}; body={error_body[:1200]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Anthropic batch 조회 실패: {exc}") from exc


def fetch_batch_results(results_url: str, timeout: float) -> list[dict[str, Any]]:
    """완료된 Anthropic Message Batch 결과 JSONL을 읽는다."""
    request = Request(results_url, headers=anthropic_request_headers(), method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Anthropic batch 결과 조회 실패: HTTP {exc.code}; body={error_body[:1200]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Anthropic batch 결과 조회 실패: {exc}") from exc
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def extract_anthropic_text(message: dict[str, Any]) -> str:
    """Anthropic 응답에서 구조화된 JSON 텍스트를 꺼낸다."""
    blocks = message.get("content") or []
    texts = [str(block.get("text", "")) for block in blocks if block.get("type") == "text"]
    if not texts:
        raise RuntimeError(f"Anthropic 응답에 text content가 없습니다: {message}")
    return "\n".join(texts)


def parse_json_object(text: str) -> dict[str, Any]:
    """응답에서 JSON 객체를 안전하게 파싱한다."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        if start == -1:
            raise
        decoder = json.JSONDecoder()
        parsed, _ = decoder.raw_decode(stripped[start:])
        return parsed


def validate_output(
    input_row: dict[str, Any],
    parsed: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """판례 ID와 쉬운요약 기본 형식을 검증한다."""
    errors = []
    warnings = []
    if str(parsed.get("precedent_id") or "") != str(input_row["판례일련번호"]):
        errors.append("precedent_id_mismatch")
    easy_summary = normalize_easy_summary(parsed.get("easy_summary"))
    if not easy_summary:
        errors.append("empty_easy_summary")
    if len(easy_summary) > MAX_EASY_SUMMARY_CHARS:
        errors.append(f"too_long:{len(easy_summary)}")
    if easy_summary and len(easy_summary) < REVIEW_MIN_EASY_SUMMARY_CHARS:
        warnings.append(f"review_too_short:{len(easy_summary)}")
    if easy_summary.startswith("이 판례는"):
        errors.append("starts_with_this_precedent")
    if any(value in easy_summary for value in ("입니다", "합니다", "했습니다")):
        errors.append("formal_polite_style")
    if not isinstance(parsed.get("explained_difficult_terms"), list):
        errors.append("explained_difficult_terms_not_list")
    return errors, warnings


def build_result_row(
    input_row: dict[str, Any],
    parsed: dict[str, Any],
    token_usage: dict[str, int],
    args: argparse.Namespace,
    warnings: list[str],
) -> dict[str, Any]:
    """검증을 통과한 결과 한 행을 만든다."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "판례일련번호": input_row["판례일련번호"],
        "사건명": input_row["사건명"],
        "생성요약": input_row["생성요약"],
        "source_summary_hash": source_summary_hash(input_row["생성요약"]),
        args.field_name: normalize_easy_summary(parsed.get("easy_summary")),
        "explained_difficult_terms": parsed.get("explained_difficult_terms", []),
        "provider": "anthropic",
        "model": args.anthropic_model.strip(),
        "prompt_version": PROMPT_VERSION,
        "execution_mode": "anthropic_message_batch",
        "token_usage": token_usage,
        "검증경고": warnings,
        "final_case_updated": False,
    }


def update_final_case(
    input_row: dict[str, Any],
    easy_summary: str,
    args: argparse.Namespace,
) -> bool:
    """선택된 경우 최종 판례 JSON에 쉬운요약을 반영한다."""
    if not args.write_final:
        return False
    path = Path(str(input_row["source_path"]))
    case = load_final_case(path)
    if normalize_text(case.get(args.field_name)) and not args.overwrite:
        return False
    case[args.field_name] = easy_summary
    write_final_case(path, case)
    return True


def collect_batch_results(
    paths: RunPaths,
    args: argparse.Namespace,
    stats: RunStats,
    input_rows: list[dict[str, Any]],
    batch_payload: dict[str, Any],
) -> None:
    """완료된 Batch 결과를 검증하고 결과 파일로 저장한다."""
    results_url = batch_payload.get("results_url")
    if not results_url:
        stats.stopped_reason = f"batch_not_ready:{batch_payload.get('processing_status')}"
        return
    input_by_id = {str(row["판례일련번호"]): row for row in input_rows}
    reset_run_outputs(paths)
    for batch_row in fetch_batch_results(str(results_url), args.timeout):
        case_id = str(batch_row.get("custom_id") or "")
        result = batch_row.get("result") or {}
        if result.get("type") != "succeeded":
            stats.failure_count += 1
            append_jsonl(
                paths.failures,
                {
                    "failed_at": now_utc_iso(),
                    "판례일련번호": case_id,
                    "batch_result": batch_row,
                },
            )
            continue
        message = result.get("message") or {}
        usage = message.get("usage") or {}
        uncached_input_tokens = int(usage.get("input_tokens") or 0)
        cache_creation_input_tokens = int(
            usage.get("cache_creation_input_tokens") or 0
        )
        cache_read_input_tokens = int(usage.get("cache_read_input_tokens") or 0)
        total_input_tokens = (
            uncached_input_tokens
            + cache_creation_input_tokens
            + cache_read_input_tokens
        )
        output_tokens = int(usage.get("output_tokens") or 0)
        token_usage = {
            "input_tokens": total_input_tokens,
            "uncached_input_tokens": uncached_input_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens,
            "cache_read_input_tokens": cache_read_input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_input_tokens + output_tokens,
        }
        stats.input_token_count += token_usage["input_tokens"]
        stats.uncached_input_token_count += token_usage["uncached_input_tokens"]
        stats.cache_creation_input_token_count += token_usage[
            "cache_creation_input_tokens"
        ]
        stats.cache_read_input_token_count += token_usage["cache_read_input_tokens"]
        stats.output_token_count += token_usage["output_tokens"]
        stats.total_token_count += token_usage["total_tokens"]
        raw_response = ""
        try:
            input_row = input_by_id[case_id]
            raw_response = extract_anthropic_text(message)
            parsed = parse_json_object(raw_response)
            errors, warnings = validate_output(input_row, parsed)
            if errors:
                raise ValueError(f"출력 검증 실패: {errors}")
            row = build_result_row(input_row, parsed, token_usage, args, warnings)
            row["final_case_updated"] = update_final_case(
                input_row,
                row[args.field_name],
                args,
            )
            append_jsonl(paths.results, row)
            stats.generated_count += 1
        except Exception as exc:
            stats.failure_count += 1
            append_jsonl(
                paths.failures,
                {
                    "failed_at": now_utc_iso(),
                    "판례일련번호": case_id,
                    "error": str(exc),
                    "token_usage": token_usage,
                    "raw_response": raw_response,
                    "batch_result": batch_row,
                },
            )


def batch_pricing(model: str) -> dict[str, float] | None:
    """모델의 Batch 및 1시간 프롬프트 캐시 가격을 반환한다."""
    pricing = ANTHROPIC_PRICING_USD_PER_MILLION.get(model.strip())
    if not pricing:
        return None
    return {
        "input": round(pricing["input"] * 0.5, 6),
        "cache_creation_input_1h": round(pricing["input"] * 2.0 * 0.5, 6),
        "cache_read_input": round(pricing["input"] * 0.05 * 0.5, 6),
        "output": round(pricing["output"] * 0.5, 6),
    }


def estimate_cost_usd(
    model: str,
    uncached_input_tokens: int,
    cache_creation_input_tokens: int,
    cache_read_input_tokens: int,
    output_tokens: int,
) -> float | None:
    """Batch 및 프롬프트 캐시 가격과 실제 토큰으로 비용을 계산한다."""
    pricing = batch_pricing(model)
    if not pricing:
        return None
    return round(
        uncached_input_tokens / 1_000_000 * pricing["input"]
        + cache_creation_input_tokens
        / 1_000_000
        * pricing["cache_creation_input_1h"]
        + cache_read_input_tokens
        / 1_000_000
        * pricing["cache_read_input"]
        + output_tokens / 1_000_000 * pricing["output"],
        6,
    )


def write_manifest(
    paths: RunPaths,
    args: argparse.Namespace,
    stats: RunStats,
    input_rows: list[dict[str, Any]],
    batch_payload: dict[str, Any] | None,
) -> None:
    """실행 설정, Batch 상태, 사용량과 비용을 manifest에 저장한다."""
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "started_at": stats.started_at,
        "finished_at": now_utc_iso(),
        "provider": "anthropic",
        "model": args.anthropic_model.strip(),
        "execution_mode": "anthropic_message_batch",
        "batch_discount": 0.5,
        "prompt_version": PROMPT_VERSION,
        "field_name": args.field_name,
        "max_output_tokens": args.max_output_tokens,
        "prompt_cache": {
            "enabled": True,
            "ttl": PROMPT_CACHE_TTL,
            "cached_content": "shared_v19_v2_instructions_only",
        },
        "dry_run": args.dry_run,
        "write_final": args.write_final,
        "overwrite": args.overwrite,
        "output_retry_reminder": any(
            bool(row.get("output_retry_reminder")) for row in input_rows
        ),
        "case_ids": [row["판례일련번호"] for row in input_rows],
        "total_targets": stats.total_targets,
        "generated_count": stats.generated_count,
        "failure_count": stats.failure_count,
        "token_usage": {
            "input_tokens": stats.input_token_count,
            "uncached_input_tokens": stats.uncached_input_token_count,
            "cache_creation_input_tokens": stats.cache_creation_input_token_count,
            "cache_read_input_tokens": stats.cache_read_input_token_count,
            "output_tokens": stats.output_token_count,
            "total_tokens": stats.total_token_count,
        },
        "pricing_usd_per_million_tokens": batch_pricing(args.anthropic_model),
        "estimated_cost_usd": estimate_cost_usd(
            args.anthropic_model,
            stats.uncached_input_token_count,
            stats.cache_creation_input_token_count,
            stats.cache_read_input_token_count,
            stats.output_token_count,
        ),
        "stopped_reason": stats.stopped_reason,
        "batch": batch_payload,
        "inputs_path": str(paths.inputs),
        "results_path": str(paths.results),
        "failures_path": str(paths.failures),
    }
    paths.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """쉬운요약 Batch 입력을 만들고 제출 또는 결과 수집을 수행한다."""
    args = parse_args()
    if args.batch_submit and args.batch_collect:
        raise ValueError("--batch-submit과 --batch-collect는 동시에 사용할 수 없습니다.")
    run_name = sanitize_run_name(
        args.run_name or f"easy_summary_{args.anthropic_model}_batch"
    )
    paths = build_run_paths(args.output_dir, run_name)

    if args.batch_collect:
        if not args.batch_id:
            raise ValueError("--batch-collect에는 --batch-id가 필요합니다.")
        input_rows = read_jsonl(paths.inputs)
        if not input_rows:
            raise FileNotFoundError(
                f"제출 당시 입력 파일이 없습니다. 같은 --run-name을 사용해야 합니다: {paths.inputs}"
            )
    else:
        target_paths = collect_target_paths(args)
        input_rows = build_input_rows(target_paths)
        if args.output_retry_reminder:
            add_output_retry_reminder(input_rows)
        write_jsonl(paths.inputs, input_rows)

    stats = RunStats(started_at=now_utc_iso(), total_targets=len(input_rows))
    print(f"대상 판례: {len(input_rows)}건")
    print(f"배치 입력 저장: {paths.inputs}")

    if args.dry_run:
        write_manifest(paths, args, stats, input_rows, None)
        print("dry-run이므로 유료 Anthropic API를 호출하지 않습니다.")
        print(f"manifest 저장: {paths.manifest}")
        return

    if args.batch_submit:
        batch_payload = submit_batch(
            input_rows,
            args.anthropic_model,
            args.max_output_tokens,
            args.timeout,
        )
        write_manifest(paths, args, stats, input_rows, batch_payload)
        print(f"batch 제출: {batch_payload.get('id')}")
        print(f"batch 상태: {batch_payload.get('processing_status')}")
        print(f"manifest 저장: {paths.manifest}")
        return

    if args.batch_collect:
        batch_payload = retrieve_batch(args.batch_id, args.timeout)
        print(f"batch 상태: {batch_payload.get('processing_status')}")
        if batch_payload.get("processing_status") == "ended":
            collect_batch_results(paths, args, stats, input_rows, batch_payload)
            print(
                f"결과 저장: 생성 {stats.generated_count}건, 실패 {stats.failure_count}건, "
                "예상 비용 "
                f"{estimate_cost_usd(args.anthropic_model, stats.uncached_input_token_count, stats.cache_creation_input_token_count, stats.cache_read_input_token_count, stats.output_token_count)} USD"
            )
        else:
            stats.stopped_reason = f"batch_not_ready:{batch_payload.get('processing_status')}"
            print("아직 완료되지 않아 결과를 저장하지 않았습니다.")
        write_manifest(paths, args, stats, input_rows, batch_payload)
        print(f"manifest 저장: {paths.manifest}")
        return

    write_manifest(paths, args, stats, input_rows, None)
    print("유료 호출을 하지 않았습니다. 제출하려면 --batch-submit을 사용하세요.")
    print(f"manifest 저장: {paths.manifest}")


if __name__ == "__main__":
    main()
