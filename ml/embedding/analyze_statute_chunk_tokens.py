# analyze_statute_chunk_tokens.py
"""
Description: 법령 조문 청크를 후보 임베딩 모델별 토크나이저로 변환해
토큰 길이 분포와 입력 한도 초과 청크를 비교한다.
Author: ooheunsu
Date: 2026-08-29
Before:
    - 조문 단위 retrieval_text 청크가 data/statutes/chunks/에 생성된 상태.

After:
    - reports/statute_chunk_token_analysis_v01.json에 모델별 토큰 분석 결과가 생성.
"""

import argparse
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ml.data_collection.statutes.law_api_common import (
    PROJECT_ROOT,
    read_json,
    write_json,
)


DEFAULT_INPUT_DIR = PROJECT_ROOT / "data/statutes/chunks"
DEFAULT_REPORT = (
    PROJECT_ROOT
    / "data/statutes/reports/statute_chunk_token_analysis_v01.json"
)
DEFAULT_CACHE_DIR = PROJECT_ROOT / "data/statutes/reports/tokenizer_cache"
REPORT_VERSION = "0.1"
THRESHOLDS = (512, 1024)

MODEL_SPECS = {
    "kure-v1": {
        "model_name": "nlpai-lab/KURE-v1",
        "tokenizer_type": "huggingface",
        "max_input_tokens": 8192,
    },
    "bge-m3": {
        "model_name": "BAAI/bge-m3",
        "tokenizer_type": "huggingface",
        "max_input_tokens": 8192,
    },
    "text-embedding-3-large": {
        "model_name": "text-embedding-3-large",
        "tokenizer_type": "tiktoken",
        "encoding_name": "cl100k_base",
        "max_input_tokens": 8191,
    },
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def project_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def percentile_nearest_rank(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, int((percentile * len(ordered) + 0.999999)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def length_summary(values: list[int]) -> dict:
    if not values:
        return {
            "min": 0,
            "median": 0,
            "p95": 0,
            "p99": 0,
            "max": 0,
            "mean": 0.0,
        }
    ordered = sorted(values)
    count = len(ordered)
    if count % 2:
        median = ordered[count // 2]
    else:
        median = (ordered[count // 2 - 1] + ordered[count // 2]) / 2
    return {
        "min": ordered[0],
        "median": median,
        "p95": percentile_nearest_rank(ordered, 0.95),
        "p99": percentile_nearest_rank(ordered, 0.99),
        "max": ordered[-1],
        "mean": round(sum(ordered) / count, 2),
    }


def load_chunks(input_dir: Path) -> list[dict]:
    input_files = sorted(input_dir.glob("*.json"))
    if not input_files:
        raise FileNotFoundError(f"법령 청크 JSON이 없습니다: {input_dir}")

    chunks: list[dict] = []
    chunk_ids: set[str] = set()
    for input_file in input_files:
        document = read_json(input_file)
        file_chunks = document.get("chunks")
        if not isinstance(file_chunks, list):
            raise ValueError(f"chunks 배열이 없습니다: {input_file}")
        for chunk in file_chunks:
            if not isinstance(chunk, dict):
                raise ValueError(f"청크가 객체가 아닙니다: {input_file}")
            chunk_id = chunk.get("chunk_id")
            text = chunk.get("retrieval_text")
            if not isinstance(chunk_id, str) or not chunk_id:
                raise ValueError(f"chunk_id가 없습니다: {input_file}")
            if chunk_id in chunk_ids:
                raise ValueError(f"중복 chunk_id: {chunk_id}")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"retrieval_text가 비어 있습니다: {chunk_id}")
            chunk_ids.add(chunk_id)
            chunks.append(chunk)
    return chunks


def load_huggingface_counter(
    model_name: str,
    cache_dir: Path,
) -> Callable[[str], int]:
    try:
        from transformers import AutoTokenizer
    except ImportError as error:
        raise RuntimeError(
            "Hugging Face 토큰 분석에는 transformers가 필요합니다. "
            "python -m pip install transformers 를 실행하세요."
        ) from error

    tokenizer_cache = cache_dir / "huggingface"
    cached_snapshot = find_cached_huggingface_snapshot(
        model_name,
        tokenizer_cache,
    )
    if cached_snapshot is not None:
        tokenizer = AutoTokenizer.from_pretrained(
            cached_snapshot,
            local_files_only=True,
            use_fast=True,
        )
    else:
        tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            cache_dir=tokenizer_cache,
            use_fast=True,
        )

    def count_tokens(text: str) -> int:
        token_ids = tokenizer.encode(
            text,
            add_special_tokens=True,
            truncation=False,
        )
        return len(token_ids)

    return count_tokens


def find_cached_huggingface_snapshot(
    model_name: str,
    tokenizer_cache: Path,
) -> Path | None:
    model_cache = tokenizer_cache / (
        "models--" + model_name.replace("/", "--")
    )
    snapshots_dir = model_cache / "snapshots"
    snapshots = [
        path for path in snapshots_dir.glob("*") if path.is_dir()
    ]
    if not snapshots:
        return None
    return max(snapshots, key=lambda path: path.stat().st_mtime)


def load_tiktoken_counter(
    encoding_name: str,
    cache_dir: Path,
) -> Callable[[str], int]:
    try:
        import tiktoken
    except ImportError as error:
        raise RuntimeError(
            "OpenAI 토큰 분석에는 tiktoken이 필요합니다. "
            "python -m pip install tiktoken 을 실행하세요."
        ) from error

    tiktoken_cache = cache_dir / "tiktoken"
    tiktoken_cache.mkdir(parents=True, exist_ok=True)
    os.environ["TIKTOKEN_CACHE_DIR"] = str(tiktoken_cache)
    encoding = tiktoken.get_encoding(encoding_name)
    return lambda text: len(encoding.encode(text, disallowed_special=()))


def token_counter(spec: dict, cache_dir: Path) -> Callable[[str], int]:
    tokenizer_type = spec["tokenizer_type"]
    if tokenizer_type == "huggingface":
        return load_huggingface_counter(spec["model_name"], cache_dir)
    if tokenizer_type == "tiktoken":
        return load_tiktoken_counter(spec["encoding_name"], cache_dir)
    raise ValueError(f"지원하지 않는 tokenizer_type: {tokenizer_type}")


def analyze_model(
    chunks: list[dict],
    spec: dict,
    count_tokens: Callable[[str], int],
) -> dict:
    measured: list[dict] = []
    token_counts: list[int] = []
    for chunk in chunks:
        count = count_tokens(chunk["retrieval_text"])
        token_counts.append(count)
        metadata = chunk.get("metadata", {})
        measured.append(
            {
                "chunk_id": chunk["chunk_id"],
                "law_name": metadata.get("law_name"),
                "article_label": metadata.get("article_label"),
                "article_title": metadata.get("article_title"),
                "character_count": len(chunk["retrieval_text"]),
                "token_count": count,
            }
        )

    max_input_tokens = spec["max_input_tokens"]
    count_digest = hashlib.sha256(
        ",".join(str(value) for value in token_counts).encode("ascii")
    ).hexdigest()
    return {
        "model_name": spec["model_name"],
        "tokenizer_type": spec["tokenizer_type"],
        "encoding_name": spec.get("encoding_name"),
        "max_input_tokens": max_input_tokens,
        "chunk_count": len(chunks),
        "token_count": length_summary(token_counts),
        "threshold_exceeded": {
            str(threshold): sum(value > threshold for value in token_counts)
            for threshold in THRESHOLDS
        },
        "max_input_exceeded": sum(
            value > max_input_tokens for value in token_counts
        ),
        "token_count_sha256": count_digest,
        "longest_chunks": sorted(
            measured,
            key=lambda item: item["token_count"],
            reverse=True,
        )[:24],
    }


def build_report(
    input_dir: Path,
    cache_dir: Path,
    model_keys: list[str],
) -> dict:
    chunks = load_chunks(input_dir)
    model_results = {}
    for model_key in model_keys:
        spec = MODEL_SPECS[model_key]
        print(f"토큰 분석 중: {spec['model_name']}")
        model_results[model_key] = analyze_model(
            chunks,
            spec,
            token_counter(spec, cache_dir),
        )

    shared_counts = {}
    keys = list(model_results)
    for index, left_key in enumerate(keys):
        for right_key in keys[index + 1 :]:
            pair = f"{left_key}__{right_key}"
            shared_counts[pair] = (
                model_results[left_key]["token_count_sha256"]
                == model_results[right_key]["token_count_sha256"]
            )

    return {
        "report_version": REPORT_VERSION,
        "generated_at": utc_timestamp(),
        "input_dir": project_path(input_dir),
        "chunk_count": len(chunks),
        "thresholds": list(THRESHOLDS),
        "models": model_results,
        "identical_token_counts": shared_counts,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="법령 청크의 모델별 토큰 길이를 분석합니다."
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument(
        "--models",
        nargs="+",
        choices=sorted(MODEL_SPECS),
        default=list(MODEL_SPECS),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report(
        args.input_dir.resolve(),
        args.cache_dir.resolve(),
        args.models,
    )
    write_json(args.output.resolve(), report)
    print(f"토큰 분석 보고서: {args.output.resolve()}")


if __name__ == "__main__":
    main()
