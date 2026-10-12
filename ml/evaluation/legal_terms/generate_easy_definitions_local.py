# generate_easy_definitions_local.py
"""
Description: 확정된 우리말샘 원 정의를 로컬 Ollama 모델로 쉽게 풀어 쓰고,
다중 정의의 1:1 대응과 출력 형식을 검증하여 비교용 결과를 저장한다.
Author: choeminju
Date: 2026-10-05
Before:
    - 단일 법률 정의 용어와 다중 법률 정의 검토 데이터가 준비된 상태.
    - 로컬 Ollama에 실행할 모델이 설치되어 있고 API가 실행 중인 상태.
After:
    - 층화 표본, 쉬운 정의 결과 JSONL·CSV, 실행 manifest가 생성.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOCAL_DATA_ROOT = PROJECT_ROOT / "local_data"
DEFAULT_SINGLE_PATH = (
    LOCAL_DATA_ROOT
    / "legal_terms"
    / "reviews"
    / "legal_category_terms_v01"
    / "easy_definition_ready_terms.jsonl"
)
DEFAULT_REVIEW_PATH = (
    LOCAL_DATA_ROOT
    / "legal_terms"
    / "reviews"
    / "legal_category_terms_v01"
    / "legal_category_term_review.jsonl"
)
DEFAULT_OUTPUT_ROOT = LOCAL_DATA_ROOT / "legal_terms" / "evaluation"
DEFAULT_SAMPLE_PATH = (
    LOCAL_DATA_ROOT / "legal_terms" / "final_v01" / "easy_definition_sample30.jsonl"
)
DEFAULT_MODEL = "qwen3.5:9b"
PROMPT_VERSION = "easy_legal_definition.v0.3"

SYSTEM_PROMPT = """/no_think

너는 법률용어를 처음 접하는 20대 성인에게 뜻을 설명하는 법률 전문가다.
입력된 법률용어의 원 정의를 읽고, 법적 의미는 그대로 두고 더 쉬운 문장으로 바꿔 써라.

[규칙]
1. 먼저 원 정의에서 어려운 단어를 찾아 hard_words에 적는다.
   (한자어, 법률 전문어, 추상적 표현. 없으면 빈 배열 [])
2. easy_definition에서 hard_words의 각 단어는 쉬운 말로 바꾼다.
   쉬운 말로 바꾸기 어려운 단어는 그대로 쓰고 바로 뒤 괄호에 짧은 뜻풀이를 붙인다.
   - 뜻풀이는 그 단어의 일반적인 뜻만 쓴다.
   - 사례, 요건, 효과 등 원 정의에 없는 사실은 새로 넣지 않는다.
3. 아래는 원 정의와 똑같이 유지한다.
   - 주체와 대상, 권리·의무 관계 (누가 누구에게 무엇을 하는지 뒤바꾸지 않는다)
   - 조건, 예외, 범위, 법적 효과
   - "~하지 않거나", "또는", "및" 같은 논리 표현
   - 강도: "~할 수 있다"를 "~해야 한다"로 바꾸지 않는다. 일부에 해당하는 내용을 전체로 넓히지 않는다.
4. 서로 다른 개념으로 바꾸지 않는다. (소유→거주, 권리→행동, 절차→결과 등)
5. hard_words가 비어 있으면 원문을 거의 그대로 쓴다.
   hard_words가 있으면 긴 문장을 짧은 문장 2~3개로 나누고, 뜻의 순서는 유지한다.
6. 차분한 설명체로 쓴다. 같은 말을 반복하거나 분량을 늘리지 않는다.
   용어 자체를 되풀이하는 것으로 설명을 대신하지 않는다.
7. 정의가 여러 개면 각각 따로 하나씩 쓴다. 합치거나 빼지 않고, definition_id와 순서를 그대로 유지한다.


[출력]
JSON 객체 하나만 출력한다. 코드 블록, 설명, 인사말은 쓰지 않는다.
{"term": "입력 용어", "easy_definitions": [{"definition_id": "입력과 같은 id", "hard_words": ["..."], "easy_definition": "쉬운 정의"}]}
"""

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "term": {"type": "string"},
        "easy_definitions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "definition_id": {"type": "string"},
                    "easy_definition": {"type": "string"},
                    "hard_words": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["definition_id", "hard_words", "easy_definition"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["term", "easy_definitions"],
    "additionalProperties": False,
}


def parse_args() -> argparse.Namespace:
    """실험 입력, 모델과 출력 옵션을 정의한다."""
    parser = argparse.ArgumentParser(description="로컬 모델 쉬운 법률 정의 실험")
    parser.add_argument("--sample-path", type=Path, default=DEFAULT_SAMPLE_PATH)
    parser.add_argument("--single-path", type=Path, default=DEFAULT_SINGLE_PATH)
    parser.add_argument("--review-path", type=Path, default=DEFAULT_REVIEW_PATH)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--single-count", type=int, default=10)
    parser.add_argument("--multi-count", type=int, default=20)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--num-predict", type=int, default=800)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="층화 표본만 저장하고 Ollama는 호출하지 않는다.",
    )
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 파일을 행 목록으로 읽는다."""
    with path.open("r", encoding="utf-8-sig") as file:
        return [json.loads(line) for line in file if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """행 목록을 UTF-8 JSONL로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_prepared_sample(path: Path) -> list[dict[str, Any]]:
    """확정 표본을 실험 공통 구조로 정규화하고 정의 구성을 검증한다."""
    sample = []
    for source_row in read_jsonl(path):
        definitions = [
            {
                "definition_id": str(item["definition_id"]),
                "category": str(item.get("category", "")),
                "source_definition": str(item["source_definition"]).strip(),
                "source_link": str(item.get("source_link", "")),
            }
            for item in source_row.get("definitions", [])
        ]
        if not definitions:
            raise ValueError(f"원 정의가 없는 표본 용어입니다: {source_row.get('term')}")
        sample.append(
            {
                **source_row,
                "definition_count": len(definitions),
                "sample_group": "multi" if len(definitions) >= 2 else "single",
                "source_type": source_row.get("source_type", ""),
                "definitions": definitions,
            }
        )
    return sample


def definition_id(definition: dict[str, Any]) -> str:
    """우리말샘 정의의 안정적인 식별자를 반환한다."""
    target_code = str(definition.get("target_code", "")).strip()
    sense_no = str(definition.get("sense_no", "")).strip()
    if target_code:
        return target_code
    digest_input = "|".join(
        [
            str(definition.get("headword", "")),
            sense_no,
            str(definition.get("definition", "")),
        ]
    )
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]


def normalize_term(row: dict[str, Any]) -> dict[str, Any]:
    """검토 행을 모델 입력에 필요한 공통 구조로 정규화한다."""
    definitions = []
    for item in row.get("legal_definitions", []):
        definitions.append(
            {
                "definition_id": definition_id(item),
                "category": str(item.get("category", "")),
                "source_definition": str(item.get("definition", "")).strip(),
                "source_link": str(item.get("source_link", "")),
            }
        )
    return {
        "match_key": row["match_key"],
        "term": row["term"],
        "definition_count": len(definitions),
        "sample_group": "multi" if len(definitions) >= 2 else "single",
        "source_type": row.get("source_type", ""),
        "source_domains": row.get("source_domains", []),
        "definitions": definitions,
    }


def evenly_spaced(rows: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """정렬된 목록의 앞·중간·뒤가 포함되도록 균등 간격으로 선택한다."""
    if count <= 0:
        return []
    if len(rows) < count:
        raise ValueError(f"표본 후보가 부족합니다: 필요 {count}개, 후보 {len(rows)}개")
    if count == 1:
        return [rows[len(rows) // 2]]
    indexes = [round(index * (len(rows) - 1) / (count - 1)) for index in range(count)]
    return [rows[index] for index in indexes]


def select_multi_sample(rows: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """정의 수가 많은 용어를 포함하고 나머지는 정의 길이별로 고르게 뽑는다."""
    candidates = [
        normalize_term(row)
        for row in rows
        if row.get("include_in_glossary")
        and row.get("review_status") == "sense_selection_required"
        and int(row.get("legal_definition_count", 0)) >= 2
    ]
    candidates.sort(
        key=lambda row: (
            -row["definition_count"],
            sum(len(item["source_definition"]) for item in row["definitions"]),
            row["match_key"],
        )
    )
    high_count = [row for row in candidates if row["definition_count"] >= 3]
    selected = high_count[: min(len(high_count), count)]
    remaining_count = count - len(selected)
    if remaining_count:
        two_definition_rows = [row for row in candidates if row["definition_count"] == 2]
        two_definition_rows.sort(
            key=lambda row: (
                sum(len(item["source_definition"]) for item in row["definitions"]),
                row["match_key"],
            )
        )
        selected.extend(evenly_spaced(two_definition_rows, remaining_count))
    return selected


def select_single_sample(rows: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """원 정의 길이가 짧은 것부터 긴 것까지 고르게 단일 정의 표본을 뽑는다."""
    candidates = [normalize_term(row) for row in rows if row.get("easy_definition_ready")]
    candidates.sort(
        key=lambda row: (len(row["definitions"][0]["source_definition"]), row["match_key"])
    )
    return evenly_spaced(candidates, count)


def build_sample(
    single_rows: list[dict[str, Any]],
    review_rows: list[dict[str, Any]],
    single_count: int,
    multi_count: int,
) -> list[dict[str, Any]]:
    """단일 정의와 다중 정의를 지정 개수로 결합한다."""
    multi = select_multi_sample(review_rows, multi_count)
    single = select_single_sample(single_rows, single_count)
    return multi + single


def build_user_prompt(row: dict[str, Any]) -> str:
    """원 정의와 식별자를 유지한 모델 입력 JSON을 만든다."""
    payload = {
        "term": row["term"],
        "definitions": [
            {
                "definition_id": item["definition_id"],
                "category": item["category"],
                "source_definition": item["source_definition"],
            }
            for item in row["definitions"]
        ],
    }
    return "[입력]\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def call_ollama(row: dict[str, Any], args: argparse.Namespace) -> tuple[str, float]:
    """Ollama 구조화 출력 API를 호출하고 원 응답과 소요 시간을 반환한다."""
    endpoint = args.ollama_url.rstrip("/") + "/api/chat"
    payload = {
        "model": args.model,
        "stream": False,
        "think": False,
        "format": OUTPUT_SCHEMA,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(row)},
        ],
        "options": {
            "temperature": args.temperature,
            "num_ctx": args.num_ctx,
            "num_predict": args.num_predict,
        },
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=args.timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    elapsed = time.perf_counter() - started
    return str(body.get("message", {}).get("content", "")), elapsed


def validate_output(row: dict[str, Any], parsed: dict[str, Any]) -> list[str]:
    """용어와 정의 ID가 입력과 정확히 1:1 대응하는지 검증한다."""
    errors = []
    if parsed.get("term") != row["term"]:
        errors.append("term_mismatch")
    outputs = parsed.get("easy_definitions")
    if not isinstance(outputs, list):
        return errors + ["easy_definitions_not_list"]
    expected_ids = [item["definition_id"] for item in row["definitions"]]
    actual_ids = [str(item.get("definition_id", "")) for item in outputs]
    if actual_ids != expected_ids:
        errors.append("definition_id_order_or_count_mismatch")
    for index, item in enumerate(outputs):
        if not isinstance(item.get("hard_words"), list):
            errors.append(f"hard_words_not_list:{index}")
        if not str(item.get("easy_definition", "")).strip():
            errors.append(f"empty_easy_definition:{index}")
    return errors


def run_generation(
    sample: list[dict[str, Any]],
    args: argparse.Namespace,
    results_path: Path,
) -> list[dict[str, Any]]:
    """표본 전체를 생성하고 검증된 각 응답을 즉시 저장한다."""
    existing_results: dict[str, dict[str, Any]] = {}
    if results_path.exists():
        for result in read_jsonl(results_path):
            if result.get("model") == args.model and result.get("status") == "success":
                existing_results[str(result.get("match_key", ""))] = result

    results = []
    total = len(sample)
    for index, row in enumerate(sample, start=1):
        if row["match_key"] in existing_results:
            results.append(existing_results[row["match_key"]])
            print(
                f"[{index}/{total}] {row['term']} ({row['definition_count']}개 정의) "
                "기존 성공 결과 사용",
                flush=True,
            )
            continue
        last_error = ""
        raw_response = ""
        elapsed = 0.0
        parsed: dict[str, Any] | None = None
        for attempt in range(1, args.max_retries + 2):
            try:
                raw_response, elapsed = call_ollama(row, args)
                parsed = json.loads(raw_response)
                validation_errors = validate_output(row, parsed)
                if validation_errors:
                    raise ValueError(",".join(validation_errors))
                last_error = ""
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
                last_error = str(exc)
                parsed = None
        result = {
            **row,
            "model": args.model,
            "prompt_version": PROMPT_VERSION,
            "status": "success" if parsed is not None else "failed",
            "attempts": attempt,
            "elapsed_seconds": round(elapsed, 3),
            "easy_definitions": parsed.get("easy_definitions", []) if parsed else [],
            "raw_response": raw_response,
            "error": last_error,
        }
        results.append(result)
        write_jsonl(results_path, results)
        outcome = "성공" if parsed is not None else f"실패: {last_error}"
        print(
            f"[{index}/{total}] {row['term']} ({row['definition_count']}개 정의) {outcome}",
            flush=True,
        )
    return results


def write_csv(path: Path, results: list[dict[str, Any]]) -> None:
    """원 정의와 쉬운 정의를 한 행씩 펼쳐 비교용 CSV로 저장한다."""
    headers = [
        "sample_group",
        "term",
        "match_key",
        "definition_count",
        "definition_id",
        "category",
        "source_definition",
        "hard_words",
        "easy_definition",
        "model",
        "status",
        "elapsed_seconds",
        "error",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for result in results:
            output_by_id = {
                str(item.get("definition_id", "")): item
                for item in result.get("easy_definitions", [])
            }
            for definition in result["definitions"]:
                output = output_by_id.get(definition["definition_id"], {})
                writer.writerow(
                    {
                        "sample_group": result["sample_group"],
                        "term": result["term"],
                        "match_key": result["match_key"],
                        "definition_count": result["definition_count"],
                        "definition_id": definition["definition_id"],
                        "category": definition["category"],
                        "source_definition": definition["source_definition"],
                        "hard_words": json.dumps(
                             output.get("hard_words", []),
                            ensure_ascii=False,
                        ),
                        "easy_definition": str(output.get("easy_definition", "")),
                        "model": result["model"],
                        "status": result["status"],
                        "elapsed_seconds": result["elapsed_seconds"],
                        "error": result["error"],
                    }
                )


def sha256_file(path: Path) -> str:
    """재현 기록용 파일 SHA-256을 계산한다."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    """표본 준비, 로컬 생성, 결과 저장을 순서대로 수행한다."""
    args = parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir or (
        DEFAULT_OUTPUT_ROOT / f"easy_definition_{args.model.replace(':', '_')}_{timestamp}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    sample = load_prepared_sample(args.sample_path)
    sample_path = output_dir / "sample_terms.jsonl"
    write_jsonl(sample_path, sample)
    print(f"표본 저장: {sample_path}")
    print(f"- 다중 정의 용어: {sum(row['sample_group'] == 'multi' for row in sample)}개")
    print(f"- 단일 정의 용어: {sum(row['sample_group'] == 'single' for row in sample)}개")

    results: list[dict[str, Any]] = []
    if not args.prepare_only:
        results_path = output_dir / "results.jsonl"
        results = run_generation(sample, args, results_path)
        write_csv(output_dir / "results.csv", results)

    manifest = {
        "schema_version": "easy_definition_experiment.v0.1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": args.model,
        "prompt_version": PROMPT_VERSION,
        "prompt": SYSTEM_PROMPT,
        "single_count": sum(row["sample_group"] == "single" for row in sample),
        "multi_count": sum(row["sample_group"] == "multi" for row in sample),
        "sample_term_count": len(sample),
        "sample_definition_count": sum(row["definition_count"] for row in sample),
        "success_count": sum(row.get("status") == "success" for row in results),
        "failed_count": sum(row.get("status") == "failed" for row in results),
        "sample_source": str(args.sample_path),
        "sample_source_sha256": sha256_file(args.sample_path),
        "temperature": args.temperature,
        "num_ctx": args.num_ctx,
        "num_predict": args.num_predict,
        "prepare_only": args.prepare_only,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"결과 폴더: {output_dir}")


if __name__ == "__main__":
    main()
