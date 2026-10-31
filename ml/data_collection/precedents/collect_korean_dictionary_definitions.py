# collect_korean_dictionary_definitions.py
"""
Description: 정제된 판례 법률용어 후보를 온용어에서 조회하고, 정확 일치가
없을 때 표준국어대사전으로 보완하여 분야·출처가 포함된 정의 후보를 만든다.
Author: choeminju
Date: 2026-09-21
Before:
    - 정제된 판례 법률용어 후보와 국립국어원 API 인증키가 준비된 상태.

After:
    - 온용어·표준국어대사전 원본 응답과 정의별 카테고리·출처·관련어가 저장.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CANDIDATES_PATH = (
    PROJECT_ROOT
    / "local_data"
    / "precedents"
    / "legal_terms"
    / "matched_candidates_noise_filtered"
    / "matched_legal_terms.jsonl"
)
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT
    / "local_data"
    / "precedents"
    / "legal_terms"
    / "korean_dictionary_sample30"
)
ONTERM_URL = "https://kli.korean.go.kr/term/api/search.do"
STDICT_URL = "https://stdict.korean.go.kr/api/search.do"
RESULT_FILENAME = "term_definition_candidates.jsonl"
REVIEW_FILENAME = "term_definition_candidates_review.csv"
MANIFEST_FILENAME = "manifest.json"
SCHEMA_VERSION = "precedent_korean_dictionary_definitions.v0.1"
DEFAULT_SAMPLE_SIZE = 30
DEFAULT_DELAY_SECONDS = 0.15
DEFAULT_REQUIRED_TERMS = ("근저당권", "대항력", "보증금")
SAFE_RELATED_VARIANTS = {
    "근저당권": ("근저당",),
}
DOMAIN_KEYWORDS = (
    "임대",
    "임차",
    "전세",
    "월세",
    "주택",
    "부동산",
    "토지",
    "건물",
    "등기",
    "소유권",
    "보증금",
    "담보",
    "채권",
    "채무",
    "매매",
    "매수",
    "매도",
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
SEPARATOR_RE = re.compile(r"[\s\-\^·ㆍ]+")


def parse_args() -> argparse.Namespace:
    """국립국어원 사전 정의 수집 옵션을 정의한다."""
    parser = argparse.ArgumentParser(
        description="판례 법률용어의 국립국어원 정의 후보를 수집합니다."
    )
    parser.add_argument(
        "--candidates-path",
        type=Path,
        default=DEFAULT_CANDIDATES_PATH,
        help="정제된 판례 법률용어 후보 JSONL 경로.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="원본 응답과 정리 결과를 저장할 폴더.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help="빈도 구간에 걸쳐 고르게 뽑을 파일럿 용어 수. 0이면 전체.",
    )
    parser.add_argument(
        "--required-terms",
        default=",".join(DEFAULT_REQUIRED_TERMS),
        help="파일럿에 반드시 포함할 쉼표 구분 용어.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="새 API 요청 사이 대기 시간(초).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="저장된 원본 응답을 사용하지 않고 다시 요청.",
    )
    return parser.parse_args()


def now_utc_iso() -> str:
    """현재 UTC 시각을 ISO 문자열로 반환한다."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_env_file(path: Path) -> dict[str, str]:
    """간단한 KEY=VALUE 형식의 로컬 환경 파일을 읽는다."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_api_key(name: str) -> str:
    """환경 변수 또는 .env에서 API 인증키를 읽는다."""
    value = os.getenv(name) or read_env_file(PROJECT_ROOT / ".env").get(name)
    if not value:
        raise RuntimeError(f"{name}이 .env에 설정되어 있지 않습니다.")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSONL 객체 목록을 읽는다."""
    rows = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL {line_number}번째 줄이 객체가 아닙니다: {path}")
            rows.append(value)
    return rows


def write_json(path: Path, value: Any) -> None:
    """JSON을 원자적으로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """JSONL을 원자적으로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary_path.replace(path)


def file_sha256(path: Path) -> str:
    """파일의 SHA-256을 계산한다."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_headword(value: Any) -> str:
    """사전 표제어의 구분 기호와 공백을 제거해 비교용 문자열을 만든다."""
    text = unicodedata.normalize("NFKC", html.unescape(str(value or ""))).lower()
    return SEPARATOR_RE.sub("", text)


def clean_text(value: Any) -> str:
    """사전 응답의 HTML 표시와 중복 공백을 제거한다."""
    text = html.unescape(str(value or ""))
    text = HTML_TAG_RE.sub(" ", text)
    return " ".join(text.split())


def select_sample_candidates(
    candidates: list[dict[str, Any]],
    sample_size: int,
    required_terms: Iterable[str],
) -> list[dict[str, Any]]:
    """필수 용어를 넣고 빈도순 목록 전 구간에서 표본을 고르게 선택한다."""
    if sample_size == 0 or sample_size >= len(candidates):
        return list(candidates)
    if sample_size < 1:
        raise ValueError("sample-size는 0 또는 1 이상이어야 합니다.")

    by_key = {row["match_key"]: row for row in candidates}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for term in required_terms:
        candidate = by_key.get(term)
        if candidate and candidate["match_key"] not in seen:
            selected.append(candidate)
            seen.add(candidate["match_key"])

    slots = sample_size - len(selected)
    if slots <= 0:
        return selected[:sample_size]
    denominator = max(slots - 1, 1)
    for index in range(slots):
        source_index = round(index * (len(candidates) - 1) / denominator)
        candidate = candidates[source_index]
        if candidate["match_key"] in seen:
            continue
        selected.append(candidate)
        seen.add(candidate["match_key"])

    if len(selected) < sample_size:
        for candidate in candidates:
            if candidate["match_key"] in seen:
                continue
            selected.append(candidate)
            seen.add(candidate["match_key"])
            if len(selected) == sample_size:
                break
    return selected


def cache_path(raw_dir: Path, provider: str, query: str) -> Path:
    """제공처와 검색어에 대응하는 안전한 원본 캐시 경로를 만든다."""
    signature = hashlib.sha256(query.encode("utf-8")).hexdigest()[:20]
    return raw_dir / provider / f"{signature}.json"


def fetch_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """인증값을 로그에 노출하지 않고 JSON API를 호출한다."""
    request_url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        request_url,
        headers={"User-Agent": "AlphaLawVA/1.0 legal-term-collector"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("사전 API 응답이 JSON 객체가 아닙니다.")
    return value


def load_or_fetch(
    *,
    path: Path,
    url: str,
    params: dict[str, Any],
    refresh: bool,
) -> tuple[dict[str, Any], str]:
    """원본 응답 캐시를 사용하거나 새 응답을 받아 저장한다."""
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8")), "cache"
    payload = fetch_json(url, params)
    write_json(path, payload)
    return payload, "api"


def extract_onterm_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """온용어 문자열 검색 응답을 정의 목록으로 정규화한다."""
    channel = payload.get("channel")
    if not isinstance(channel, dict):
        raise ValueError("온용어 응답에 channel 객체가 없습니다.")
    return_code = str(channel.get("returnCode") or "")
    if return_code and return_code != "1":
        raise ValueError(
            f"온용어 API 오류: {return_code} {channel.get('return_object', '')}".strip()
        )
    objects = channel.get("return_object", [])
    if isinstance(objects, dict):
        objects = [objects]
    rows: list[dict[str, Any]] = []
    for result_object in objects if isinstance(objects, list) else []:
        if not isinstance(result_object, dict):
            continue
        result_list = result_object.get("resultlist", [])
        if isinstance(result_list, dict):
            result_list = [result_list]
        rows.extend(row for row in result_list if isinstance(row, dict))
    return rows


def normalize_onterm_definition(
    item: dict[str, Any],
    source_term: str,
) -> dict[str, Any]:
    """온용어 정의 한 건을 공통 스키마로 변환한다."""
    headword = clean_text(item.get("word"))
    normalized_source = normalize_headword(source_term)
    normalized_headword = normalize_headword(headword)
    exact = normalized_source == normalized_headword
    safe_variants = {
        normalize_headword(term) for term in SAFE_RELATED_VARIANTS.get(source_term, ())
    }
    match_type = "exact" if exact else "related_variant" if normalized_headword in safe_variants else "search_related"
    license_type = clean_text(item.get("kr_gvrn_lcns_ty"))
    return {
        "provider": "onterm",
        "lookup_term": source_term,
        "headword": headword,
        "match_type": match_type,
        "definition": clean_text(item.get("definition")),
        "category_main": clean_text(item.get("category_main")),
        "category_sub": clean_text(item.get("category_sub")),
        "source": clean_text(item.get("source")),
        "glossary": clean_text(item.get("glossary")),
        "license_type": license_type,
        "transform_restricted": license_type in {"3", "4"},
        "related_words": clean_text(item.get("relate_word")),
        "usage_example": clean_text(item.get("use_ex")),
    }


def extract_stdict_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """표준국어대사전 검색 응답을 결과 목록으로 정규화한다."""
    if not payload:
        return []
    channel = payload.get("channel")
    if not isinstance(channel, dict):
        raise ValueError("표준국어대사전 응답에 channel 객체가 없습니다.")
    items = channel.get("item", []) or []
    if isinstance(items, dict):
        items = [items]
    return [item for item in items if isinstance(item, dict)]


def normalize_stdict_definition(
    item: dict[str, Any],
    source_term: str,
    lookup_term: str,
) -> dict[str, Any]:
    """표준국어대사전 정의 한 건을 공통 스키마로 변환한다."""
    sense = item.get("sense") if isinstance(item.get("sense"), dict) else {}
    headword = clean_text(item.get("word"))
    return {
        "provider": "stdict",
        "lookup_term": lookup_term,
        "headword": headword,
        "match_type": (
            "exact_fallback"
            if normalize_headword(source_term) == normalize_headword(headword)
            else "related_variant"
        ),
        "definition": clean_text(sense.get("definition")),
        "category_main": "",
        "category_sub": clean_text(sense.get("cat")),
        "source": "국립국어원",
        "glossary": "표준국어대사전",
        "license_type": "",
        "transform_restricted": False,
        "related_words": "",
        "usage_example": "",
    }


def score_definition(row: dict[str, Any]) -> tuple[int, list[str]]:
    """표제어 일치, 분야와 부동산 문맥을 기준으로 검토 순서를 정한다."""
    score = 0
    reasons = []
    if row["match_type"] in {"exact", "exact_fallback"}:
        score += 100
        reasons.append("표제어 정확 일치")
    elif row["match_type"] == "related_variant":
        score += 60
        reasons.append("검토된 관련 표기")

    category = row["category_sub"]
    category_scores = {"법률": 30, "경제": 18, "건설": 12}
    if category in category_scores:
        score += category_scores[category]
        reasons.append(f"{category} 분야")

    context = " ".join(
        [row["definition"], row["usage_example"], row["glossary"]]
    )
    matched_keywords = sorted({word for word in DOMAIN_KEYWORDS if word in context})
    if matched_keywords:
        score += min(len(matched_keywords) * 3, 24)
        reasons.append("부동산 문맥: " + ", ".join(matched_keywords[:6]))
    if row["transform_restricted"]:
        reasons.append("변형 제한 이용허락 검토 필요")
    return score, reasons


def rank_definitions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """동일 정의를 제거하고 검토 우선순위 점수와 순위를 붙인다."""
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["headword"], row["definition"], row["source"])
        if row["definition"]:
            unique.setdefault(key, row)
    ranked = []
    for row in unique.values():
        score, reasons = score_definition(row)
        ranked.append({**row, "relevance_score": score, "score_reasons": reasons})
    ranked.sort(
        key=lambda row: (
            -row["relevance_score"],
            row["transform_restricted"],
            row["source"],
            row["definition"],
        )
    )
    return [{**row, "rank": index} for index, row in enumerate(ranked, start=1)]


def write_review_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """정의 후보를 사람이 확인하기 쉬운 CSV로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "term",
        "total_case_count",
        "lookup_status",
        "rank",
        "relevance_score",
        "headword",
        "match_type",
        "category_main",
        "category_sub",
        "definition",
        "source",
        "glossary",
        "license_type",
        "transform_restricted",
        "related_words",
        "usage_example",
        "score_reasons",
        "review_status",
        "review_note",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            definitions = row["ranked_definitions"] or [{}]
            for definition in definitions:
                csv_row = {key: definition.get(key, "") for key in columns}
                csv_row.update(
                    {
                        "term": row["term"],
                        "total_case_count": row["total_case_count"],
                        "lookup_status": row["lookup_status"],
                        "score_reasons": " | ".join(
                            definition.get("score_reasons", [])
                        ),
                        "review_status": "pending",
                        "review_note": "",
                    }
                )
                writer.writerow(csv_row)


def project_relative_path(path: Path) -> str:
    """프로젝트 내부 경로를 상대경로로 기록한다."""
    resolved = path.resolve()
    if resolved.is_relative_to(PROJECT_ROOT):
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    return resolved.as_posix()


def main() -> None:
    """대표 판례 법률용어의 국립국어원 정의 후보를 수집한다."""
    args = parse_args()
    candidates_path = args.candidates_path.resolve()
    output_dir = args.output_dir.resolve()
    raw_dir = output_dir / "raw_responses"
    required_terms = [
        term.strip() for term in args.required_terms.split(",") if term.strip()
    ]
    candidates = select_sample_candidates(
        read_jsonl(candidates_path),
        args.sample_size,
        required_terms,
    )
    onterm_key = load_api_key("KOREAN_TERMS_API_KEY")
    stdict_key = load_api_key("KOREAN_DICTIONARY_API_KEY")
    started_at = time.monotonic()
    api_requests = Counter()
    cached_requests = Counter()
    result_rows: list[dict[str, Any]] = []

    for index, candidate in enumerate(candidates, start=1):
        term = candidate["match_key"]
        errors = []
        definitions: list[dict[str, Any]] = []
        onterm_path = cache_path(raw_dir, "onterm", term)
        try:
            payload, source = load_or_fetch(
                path=onterm_path,
                url=ONTERM_URL,
                params={
                    "key": onterm_key,
                    "apiSearchWord": term,
                    "start": "1",
                    "num": "100",
                    "sort": "wt",
                },
                refresh=args.refresh,
            )
            api_requests["onterm"] += int(source == "api")
            cached_requests["onterm"] += int(source == "cache")
            definitions.extend(
                normalize_onterm_definition(item, term)
                for item in extract_onterm_results(payload)
                if normalize_headword(item.get("word"))
                in {
                    normalize_headword(term),
                    *{
                        normalize_headword(variant)
                        for variant in SAFE_RELATED_VARIANTS.get(term, ())
                    },
                }
            )
            if source == "api":
                time.sleep(max(args.delay, 0))
        except Exception as exc:
            errors.append({"provider": "onterm", "error": str(exc)})

        has_exact = any(row["match_type"] == "exact" for row in definitions)
        if not has_exact:
            lookup_terms = [term, *SAFE_RELATED_VARIANTS.get(term, ())]
            for lookup_term in lookup_terms:
                stdict_path = cache_path(raw_dir, "stdict", lookup_term)
                try:
                    payload, source = load_or_fetch(
                        path=stdict_path,
                        url=STDICT_URL,
                        params={
                            "key": stdict_key,
                            "q": lookup_term,
                            "req_type": "json",
                            "advanced": "y",
                            "target": "1",
                            "method": "exact",
                            "num": "100",
                        },
                        refresh=args.refresh,
                    )
                    api_requests["stdict"] += int(source == "api")
                    cached_requests["stdict"] += int(source == "cache")
                    definitions.extend(
                        normalize_stdict_definition(item, term, lookup_term)
                        for item in extract_stdict_results(payload)
                        if normalize_headword(item.get("word"))
                        == normalize_headword(lookup_term)
                    )
                    if source == "api":
                        time.sleep(max(args.delay, 0))
                except Exception as exc:
                    errors.append(
                        {
                            "provider": "stdict",
                            "lookup_term": lookup_term,
                            "error": str(exc),
                        }
                    )

        ranked = rank_definitions(definitions)
        if any(row["match_type"] == "exact" for row in ranked):
            status = "exact_onterm"
        elif ranked:
            status = "fallback_only"
        elif errors:
            status = "error"
        else:
            status = "not_found"
        result_rows.append(
            {
                "term": term,
                "total_case_count": candidate.get("total_case_count", 0),
                "total_occurrence_count": candidate.get("total_occurrence_count", 0),
                "field_stats": candidate.get("field_stats", {}),
                "lookup_status": status,
                "raw_definition_count": len(definitions),
                "ranked_definitions": ranked,
                "selection_status": "pending_review" if ranked else "unresolved",
                "collection_errors": errors,
            }
        )
        print(
            f"[{index}/{len(candidates)}] {term}: {status}, 정의 {len(ranked)}건",
            flush=True,
        )

    results_path = output_dir / RESULT_FILENAME
    review_path = output_dir / REVIEW_FILENAME
    manifest_path = output_dir / MANIFEST_FILENAME
    write_jsonl(results_path, result_rows)
    write_review_csv(review_path, result_rows)
    status_counts = Counter(row["lookup_status"] for row in result_rows)
    category_counts = Counter(
        definition["category_sub"] or "미분류"
        for row in result_rows
        for definition in row["ranked_definitions"]
    )
    write_json(
        manifest_path,
        {
            "schema_version": SCHEMA_VERSION,
            "created_at": now_utc_iso(),
            "status": "completed",
            "purpose": "국립국어원 사전 정의 후보 30건 파일럿",
            "candidates_path": project_relative_path(candidates_path),
            "sample_method": "필수 용어 포함 후 빈도순 후보 전 구간 등간격 추출",
            "required_terms": required_terms,
            "sample_count": len(candidates),
            "sample_terms": [row["term"] for row in result_rows],
            "lookup_status_counts": dict(status_counts),
            "definition_category_counts": dict(category_counts),
            "api_request_counts": dict(api_requests),
            "cached_request_counts": dict(cached_requests),
            "elapsed_seconds": round(time.monotonic() - started_at, 1),
            "outputs": {
                "definitions": project_relative_path(results_path),
                "review_csv": project_relative_path(review_path),
                "raw_responses": project_relative_path(raw_dir),
            },
            "output_sha256": {
                results_path.name: file_sha256(results_path),
                review_path.name: file_sha256(review_path),
            },
            "notice": (
                "점수는 사람의 검토 순서를 정하기 위한 보조값이며 최종 정의 선택이 아니다. "
                "공공누리 3·4유형은 변형 제한 여부를 별도로 확인해야 한다."
            ),
        },
    )
    print(f"완료: {results_path}", flush=True)


if __name__ == "__main__":
    main()
