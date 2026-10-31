# test_collect_legal_terms.py
"""
Description: 공식 법령용어 목록 수집기의 응답 파싱, 정규화와
페이지 원본 통합 동작을 검증한다.
Author: choeminju
Date: 2026-09-20
Before:
    - 법령용어 목록 수집 코드의 API 응답 변형과 중복 처리 검증이 필요한 상태.

After:
    - 단일·복수 응답과 통합 목록의 핵심 동작을 단위 테스트로 확인 가능.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODULE_DIR = PROJECT_ROOT / "ml" / "data_collection" / "precedents"
sys.path.insert(0, str(MODULE_DIR))

from collect_legal_terms import (  # noqa: E402
    build_catalog,
    extract_terms,
    extract_total_count,
    normalize_term,
    page_path,
    redact_payload_secrets,
)


def payload(items: object, total: object = "2") -> dict:
    return {
        "LsTrmSearch": {
            "resultCode": "00",
            "totalCnt": total,
            "lstrm": items,
        }
    }


class CollectLegalTermsTest(unittest.TestCase):
    def test_extract_total_count_accepts_string(self) -> None:
        self.assertEqual(extract_total_count(payload({}, "73436")), 73436)

    def test_extract_terms_accepts_single_object(self) -> None:
        item = {"법령용어ID": "1", "법령용어명": "대항력"}
        self.assertEqual(extract_terms(payload(item)), [item])

    def test_extract_terms_ignores_non_object_items(self) -> None:
        item = {"법령용어ID": "1", "법령용어명": "대항력"}
        self.assertEqual(extract_terms(payload([item, None, "오류"])), [item])

    def test_normalize_term_preserves_source_metadata(self) -> None:
        item = {
            "법령용어ID": "100",
            "법령용어명": "근저당권",
            "사전구분코드": "011401",
            "법령종류코드": "010101",
            "법령용어상세검색": "detail",
            "법령용어상세링크": "https://example.com/100",
        }
        result = normalize_term(item)
        self.assertEqual(result["term"], "근저당권")
        self.assertEqual(result["source_term_ids"], ["100"])
        self.assertEqual(result["dictionary_type_code"], "011401")
        self.assertEqual(result["detail_url"], "https://example.com/100")

    def test_build_catalog_deduplicates_same_term_id(self) -> None:
        item = {
            "법령용어ID": "100",
            "법령용어명": "대항력",
            "사전구분코드": "011401",
        }
        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory)
            for page in (1, 2):
                page_path(raw_dir, page).write_text(
                    json.dumps(payload(item), ensure_ascii=False),
                    encoding="utf-8",
                )
            catalog = build_catalog(raw_dir, [1, 2])
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0]["term"], "대항력")

    def test_build_catalog_preserves_spelling_variants_for_same_term_ids(self) -> None:
        items = [
            {
                "id": "1",
                "법령용어ID": "100,200",
                "법령용어명": "기간제 근로자",
                "사전구분코드": "011402",
            },
            {
                "id": "2",
                "법령용어ID": "100,200",
                "법령용어명": "기간제근로자",
                "사전구분코드": "011402",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory)
            page_path(raw_dir, 1).write_text(
                json.dumps(payload(items), ensure_ascii=False),
                encoding="utf-8",
            )
            catalog = build_catalog(raw_dir, [1])
        self.assertEqual(len(catalog), 2)
        self.assertEqual(
            {row["term"] for row in catalog},
            {"기간제 근로자", "기간제근로자"},
        )
        self.assertEqual(catalog[0]["source_term_ids"], ["100", "200"])

    def test_redact_payload_secrets_handles_nested_values(self) -> None:
        source = {
            "url": "https://example.com?OC=secret-key",
            "items": ["secret-key", {"value": "safe"}],
        }
        result = redact_payload_secrets(source, "secret-key")
        self.assertNotIn("secret-key", json.dumps(result))
        self.assertEqual(result["items"][1]["value"], "safe")


if __name__ == "__main__":
    unittest.main()
