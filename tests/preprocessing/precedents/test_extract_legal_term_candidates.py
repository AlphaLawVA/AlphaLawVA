# test_extract_legal_term_candidates.py
"""
Description: 공식 법령용어 표기 정규화와 판례 필드 매칭 통계를 검증한다.
Author: choeminju
Date: 2026-09-20
Before:
    - 판례 법률용어 후보 추출기의 표기 변형과 중복 집계 검증이 필요한 상태.

After:
    - 공백 변형 그룹화와 다중 패턴 매칭의 핵심 동작을 단위 테스트로 확인 가능.
"""

import json
import tempfile
import unittest
from pathlib import Path

from ml.preprocessing.precedents.extract_legal_term_candidates import (
    AhoCorasickMatcher,
    build_candidate_rows,
    load_term_groups,
    normalize_match_text,
    normalize_source_text,
)


class ExtractLegalTermCandidatesTest(unittest.TestCase):
    def test_normalize_match_text_removes_whitespace_and_html(self) -> None:
        self.assertEqual(
            normalize_match_text(" 근저당권<br> 설정 "),
            "근저당권설정",
        )
        self.assertEqual(
            normalize_source_text(" 근저당권<br> 설정 "),
            "근저당권 설정",
        )

    def test_load_term_groups_merges_whitespace_variants(self) -> None:
        rows = [
            {
                "term": "기간제 근로자",
                "source_term_ids": ["1"],
                "dictionary_type_code": "011402",
                "law_type_code": "010102",
            },
            {
                "term": "기간제근로자",
                "source_term_ids": ["2"],
                "dictionary_type_code": "011402",
                "law_type_code": "010102",
            },
            {
                "term": "IMC",
                "source_term_ids": ["3"],
                "dictionary_type_code": "011402",
                "law_type_code": "010102",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "terms.jsonl"
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            groups, excluded = load_term_groups(path)
        self.assertEqual(set(groups), {"기간제근로자"})
        self.assertEqual(groups["기간제근로자"]["source_term_ids"], ["1", "2"])
        self.assertEqual(excluded["no_hangul"], 1)

    def test_aho_corasick_keeps_longest_overlap(self) -> None:
        matcher = AhoCorasickMatcher(["임대차", "임대차계약", "보증금"])
        counts = matcher.count("임대차계약의보증금과임대차")
        self.assertEqual(counts["임대차"], 1)
        self.assertEqual(counts["임대차계약"], 1)
        self.assertEqual(counts["보증금"], 1)

    def test_build_candidate_rows_counts_cases_and_fields(self) -> None:
        term_groups = {
            "근저당권": {
                "match_key": "근저당권",
                "term": "근저당권",
                "official_variants": ["근저당권"],
                "source_term_ids": ["1"],
                "dictionary_type_codes": ["011402"],
                "law_type_codes": ["010102"],
                "official_entry_count": 1,
            },
            "보증금": {
                "match_key": "보증금",
                "term": "보증금",
                "official_variants": ["보증금"],
                "source_term_ids": ["2"],
                "dictionary_type_codes": ["011402"],
                "law_type_codes": ["010102"],
                "official_entry_count": 1,
            },
        }
        cases = [
            {
                "판례일련번호": "1",
                "사건번호": "1다1",
                "사건명": "보증금",
                "생성요약": "근저당권 뒤의 보증금을 판단했다.",
            },
            {
                "판례일련번호": "2",
                "사건번호": "2다2",
                "사건명": "보증금",
                "생성요약": "보증금과 보증금 반환이 문제 되었다.",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for case in cases:
                path = Path(directory) / f"{case['판례일련번호']}.json"
                path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
                paths.append(path)
            rows, case_rows, stats = build_candidate_rows(term_groups, paths)
        by_term = {row["term"]: row for row in rows}
        self.assertEqual(by_term["보증금"]["total_case_count"], 2)
        self.assertEqual(by_term["보증금"]["total_occurrence_count"], 5)
        self.assertEqual(by_term["근저당권"]["total_case_count"], 1)
        self.assertEqual(len(case_rows), 2)
        self.assertEqual(stats["matched_case_count"], 2)


if __name__ == "__main__":
    unittest.main()
