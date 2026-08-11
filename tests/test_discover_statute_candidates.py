import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from discover_statute_candidates import (  # noqa: E402
    extract_search_items,
    extract_total_count,
    finalize_candidates,
    merge_candidate,
)


class StatuteDiscoveryTest(unittest.TestCase):
    def test_extract_total_count_handles_string(self) -> None:
        payload = {"LawSearch": {"totalCnt": "123"}}
        self.assertEqual(extract_total_count(payload), 123)

    def test_merge_candidate_deduplicates_same_query(self) -> None:
        candidates = {}
        item = {"법령ID": "001", "법령명한글": "테스트법"}
        search = {
            "query": "대항력",
            "search": 2,
            "level": "핵심",
            "areas": ["전세"],
        }
        merge_candidate(candidates, item, search)
        merge_candidate(candidates, item, search)
        self.assertEqual(len(candidates["001"]["matched_queries"]), 1)

    def test_finalize_marks_existing_and_scope_warning(self) -> None:
        candidates = {
            "001": {
                "law_id": "001",
                "law_name": "상가 테스트법",
                "matched_queries": [
                    {"query": "임대차", "search": 2, "level": "핵심"}
                ],
                "areas": ["전세"],
            }
        }
        result = finalize_candidates(candidates, {"001"}, set(), ["상가"])[0]
        self.assertTrue(result["existing_candidate"])
        self.assertEqual(result["scope_warnings"], ["상가"])

    def test_extract_search_items_accepts_missing_result(self) -> None:
        self.assertEqual(extract_search_items({}), [])


if __name__ == "__main__":
    unittest.main()
