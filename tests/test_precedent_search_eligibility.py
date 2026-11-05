# test_precedent_search_eligibility.py
"""
Description: 판례 검색 허용 목록 검증과 후보 필터링·재순위화를 검사한다.
외부 모델과 벡터 DB 없이 판례 검색 전용 필터의 경계를 검증한다.
Author: choeminju
Date: 2026-11-06
Before:
    - 판례 검색 API에 주택 임대차 판례 허용 목록 필터가 없음.
After:
    - 제외 판례 건너뛰기, 결과 수 제한, manifest 오류가 자동 검증됨.
"""

import json
import tempfile
import unittest
from pathlib import Path

from api.services.precedent_search_eligibility import (
    ELIGIBILITY_SCHEMA_VERSION,
    EligibilityFilteredPrecedentRetriever,
    load_eligible_precedent_ids,
)
from ml.preprocessing.precedents.build_search_eligibility_manifest import (
    classify_case,
)
from ml.rag.precedent_retriever import PrecedentSearchResult


def search_result(rank: int, precedent_id: str) -> PrecedentSearchResult:
    return PrecedentSearchResult(
        rank=rank,
        precedent_id=precedent_id,
        distance=rank / 10,
        similarity=1 - rank / 10,
        text=f"판례 {precedent_id}",
        case_no=None,
        case_no_list=None,
        case_name=None,
        court_name=None,
        decision_date=None,
        decision_year=None,
        case_type=None,
        judgment_type=None,
        source_path=None,
        matched_chunks=[],
        metadata={},
    )


class FakeRetriever:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, *, top_k=10):
        self.calls.append((query, top_k))
        return self.results[:top_k]


class PrecedentSearchEligibilityTests(unittest.TestCase):
    def test_filters_candidates_and_reassigns_public_ranks(self):
        delegate = FakeRetriever(
            [search_result(index, str(index)) for index in range(1, 9)]
        )
        retriever = EligibilityFilteredPrecedentRetriever(
            delegate,
            frozenset({"2", "5", "7"}),
            candidate_multiplier=4,
        )

        results = retriever.search("보증금을 돌려받지 못했습니다", top_k=2)

        self.assertEqual(delegate.calls, [("보증금을 돌려받지 못했습니다", 8)])
        self.assertEqual([result.precedent_id for result in results], ["2", "5"])
        self.assertEqual([result.rank for result in results], [1, 2])

    def test_returns_available_results_without_error(self):
        delegate = FakeRetriever([search_result(1, "1"), search_result(2, "2")])
        retriever = EligibilityFilteredPrecedentRetriever(
            delegate,
            frozenset({"2"}),
        )

        results = retriever.search("질문", top_k=5)

        self.assertEqual([result.precedent_id for result in results], ["2"])

    def test_loads_and_validates_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "eligibility.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": ELIGIBILITY_SCHEMA_VERSION,
                        "eligible_precedent_ids": ["100", "200"],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                load_eligible_precedent_ids(path),
                frozenset({"100", "200"}),
            )

            path.write_text(
                json.dumps(
                    {
                        "schema_version": ELIGIBILITY_SCHEMA_VERSION,
                        "eligible_precedent_ids": ["100", "100"],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "중복"):
                load_eligible_precedent_ids(path)

    def test_classifies_scope_and_summary_quality(self):
        residential = {
            "판결요지": "주택임대차보호법에 따른 임차인의 대항력",
            "쉬운요약": "세입자가 보증금을 돌려받지 못한 사건이다.",
        }
        commercial = {
            "판결요지": "상가건물 임대차보호법 적용 여부",
            "쉬운요약": "상가 세입자의 권리금 사건이다.",
        }
        bad_summary = {
            "판결요지": "주택임대차보호법 적용 여부",
            "쉬운요약": "세入자가 보증금을 요구했다.",
        }

        self.assertEqual(classify_case(residential), (True, "HOUSING_LEASE_ACT"))
        self.assertEqual(
            classify_case(commercial),
            (False, "COMMERCIAL_OR_BUSINESS"),
        )
        self.assertEqual(
            classify_case(bad_summary),
            (False, "EASY_SUMMARY_HANJA"),
        )


if __name__ == "__main__":
    unittest.main()
