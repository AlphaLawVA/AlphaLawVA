import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from collect_statutes import (  # noqa: E402
    extract_search_items,
    find_exact_match,
    load_candidates,
    select_candidates,
    validate_detail,
)
from law_api_common import redact_secret  # noqa: E402


class StatuteCollectionTest(unittest.TestCase):
    def test_extract_search_items_accepts_single_object(self) -> None:
        payload = {"LawSearch": {"law": {"법령명한글": "민법"}}}
        self.assertEqual(extract_search_items(payload), [{"법령명한글": "민법"}])

    def test_find_exact_match_does_not_choose_similar_law(self) -> None:
        items = [
            {"법령명한글": "주택임대차보호법", "법령ID": "001248"},
            {"법령명한글": "주택임대차보호법 시행령", "법령ID": "004950"},
        ]
        match = find_exact_match(items, "주택임대차보호법")
        self.assertEqual(match["법령ID"], "001248")

    def test_find_exact_match_rejects_duplicate_exact_matches(self) -> None:
        items = [
            {"법령명한글": "민법", "법령ID": "one"},
            {"법령명한글": "민법", "법령ID": "two"},
        ]
        self.assertIsNone(find_exact_match(items, "민법"))

    def test_validate_detail_requires_matching_name_and_articles(self) -> None:
        payload = {
            "법령": {
                "기본정보": {"법령명_한글": "민법"},
                "조문": {"조문단위": [{"조문번호": "1"}]},
            }
        }
        self.assertEqual(validate_detail(payload, "민법"), (True, "ok"))

    def test_select_candidates_reports_unknown_name(self) -> None:
        candidates = [{"law_name": "민법"}]
        with self.assertRaises(ValueError):
            select_candidates(candidates, ["없는 법"], False)

    def test_load_candidates_requires_law_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates.json"
            path.write_text(
                json.dumps({"candidates": [{"areas": ["매매"]}]}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_candidates(path)

    def test_redact_secret_removes_raw_and_url_encoded_value(self) -> None:
        secret = "user@example.com"
        message = "OC=user%40example.com raw=user@example.com"
        redacted = redact_secret(message, secret)
        self.assertNotIn(secret, redacted)
        self.assertNotIn("user%40example.com", redacted)


if __name__ == "__main__":
    unittest.main()
