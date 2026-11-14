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
    build_reviewed_manifest,
    classify_case,
    is_distribution_case,
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

    def search_many(self, queries, *, top_k=10):
        self.calls.append((tuple(queries), top_k))
        return [self.results[:top_k] for _query in queries]


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

    def test_filters_each_batch_for_certified_letter_search_many(self):
        delegate = FakeRetriever(
            [search_result(index, str(index)) for index in range(1, 9)]
        )
        retriever = EligibilityFilteredPrecedentRetriever(
            delegate,
            frozenset({"2", "5", "7"}),
            candidate_multiplier=4,
        )

        results = retriever.search_many(["질문 1", "질문 2"], top_k=2)

        self.assertEqual(delegate.calls, [(('질문 1', '질문 2'), 8)])
        self.assertEqual(
            [[result.precedent_id for result in batch] for batch in results],
            [["2", "5"], ["2", "5"]],
        )

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

    def test_builds_reviewed_manifest_and_excludes_distribution_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases_dir = root / "cases"
            cases_dir.mkdir()
            cases = {
                "100": {
                    "판례일련번호": "100",
                    "사건번호": "2026가단100",
                    "사건명": "임대차보증금",
                    "생성요약": "주택 임차인의 보증금 반환 사건이다.",
                    "쉬운요약": "세입자가 보증금을 돌려받지 못한 사건이다.",
                },
                "200": {
                    "판례일련번호": "200",
                    "사건번호": "2026가단200",
                    "사건명": "소유권이전등기",
                    "생성요약": "임대주택 우선 분양전환 자격 사건이다.",
                    "쉬운요약": "세입자가 집을 먼저 살 수 있는지 다투었다.",
                },
                "300": {
                    "판례일련번호": "300",
                    "사건번호": "2026가단300",
                    "사건명": "배당이의",
                    "생성요약": "주택 임차인의 우선변제권 사건이다.",
                    "쉬운요약": "세입자가 보증금을 먼저 받을 수 있는지 다투었다.",
                    "이유": "전문에는 분양이라는 단어가 단순 언급되었다.",
                },
            }
            for precedent_id, case in cases.items():
                (cases_dir / f"{precedent_id}.json").write_text(
                    json.dumps(case, ensure_ascii=False),
                    encoding="utf-8",
                )
            reviewed_ids_path = root / "eligible.json"
            reviewed_ids_path.write_text(
                json.dumps(["100", "200", "300"]),
                encoding="utf-8",
            )

            manifest, exclusions = build_reviewed_manifest(
                cases_dir,
                reviewed_ids_path,
            )

            self.assertEqual(manifest["eligible_precedent_ids"], ["100", "300"])
            self.assertEqual(manifest["distribution_excluded_case_count"], 1)
            self.assertEqual(exclusions["excluded_case_count"], 1)
            self.assertEqual(
                exclusions["excluded_precedents"][0]["precedent_id"],
                "200",
            )
            self.assertTrue(is_distribution_case(cases["200"]))
            self.assertFalse(is_distribution_case(cases["300"]))

    def test_adds_manually_reviewed_residential_sale_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases_dir = root / "cases"
            cases_dir.mkdir()
            cases = {
                "100": {
                    "판례일련번호": "100",
                    "사건번호": "2026가단100",
                    "사건명": "임대차보증금",
                    "생성요약": "주택 임차인의 보증금 반환 사건이다.",
                    "쉬운요약": "세입자가 보증금을 돌려받지 못한 사건이다.",
                },
                "200": {
                    "판례일련번호": "200",
                    "사건번호": "2026가단200",
                    "사건명": "소유권이전등기",
                    "생성요약": "아파트 매수인의 소유권이전등기 사건이다.",
                    "쉬운요약": "집을 산 사람이 명의를 넘겨 달라고 한 사건이다.",
                },
            }
            for precedent_id, case in cases.items():
                (cases_dir / f"{precedent_id}.json").write_text(
                    json.dumps(case, ensure_ascii=False),
                    encoding="utf-8",
                )
            reviewed_ids_path = root / "eligible.json"
            reviewed_ids_path.write_text(json.dumps(["100"]), encoding="utf-8")
            additions_path = root / "sale_additions.json"
            additions_path.write_text(
                json.dumps(
                    {
                        "schema_version": (
                            "precedent_search_residential_sale_additions.v1"
                        ),
                        "added_case_count": 1,
                        "added_precedents": [
                            {
                                "precedent_id": "200",
                                "case_number": "2026가단200",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            manifest, _ = build_reviewed_manifest(
                cases_dir,
                reviewed_ids_path,
                additions_path,
            )

            self.assertEqual(manifest["eligible_precedent_ids"], ["100", "200"])
            self.assertEqual(manifest["residential_lease_eligible_case_count"], 1)
            self.assertEqual(manifest["residential_sale_added_case_count"], 1)

    def test_versioned_manifest_contains_reviewed_691_case_allowlist(self):
        root = Path(__file__).resolve().parents[1]
        manifest_path = (
            root
            / "data"
            / "precedents"
            / "metadata"
            / "precedent_search_eligibility_v01.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        eligible_ids = manifest["eligible_precedent_ids"]

        self.assertEqual(
            manifest["policy_version"],
            "residential_real_estate_manual_review_v3",
        )
        self.assertEqual(manifest["base_reviewed_eligible_case_count"], 750)
        self.assertEqual(manifest["distribution_excluded_case_count"], 72)
        self.assertEqual(manifest["residential_lease_eligible_case_count"], 678)
        self.assertEqual(manifest["residential_sale_added_case_count"], 13)
        self.assertEqual(manifest["eligible_case_count"], 691)
        self.assertEqual(len(eligible_ids), 691)
        self.assertEqual(len(eligible_ids), len(set(eligible_ids)))
        self.assertIn("607217", eligible_ids)
        self.assertIn("606789", eligible_ids)
        self.assertIn("170892", eligible_ids)
        self.assertIn("205340", eligible_ids)
        self.assertNotIn("186226", eligible_ids)
        self.assertNotIn("167949", eligible_ids)
        self.assertNotIn("169234", eligible_ids)


if __name__ == "__main__":
    unittest.main()
