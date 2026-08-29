import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / "ml" / "data_collection" / "statutes"
sys.path.insert(0, str(SCRIPTS_DIR))

from collect_discovered_statutes import load_new_candidates  # noqa: E402
from law_api_common import write_json  # noqa: E402


class DiscoveredStatuteCollectionTest(unittest.TestCase):
    def test_load_new_candidates_excludes_existing_laws(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discovery.json"
            write_json(
                path,
                {
                    "candidates": [
                        {
                            "law_id": "001",
                            "law_name": "기존법",
                            "existing_candidate": True,
                        },
                        {
                            "law_id": "002",
                            "law_name": "신규법",
                            "existing_candidate": False,
                        },
                    ]
                },
            )
            candidates = load_new_candidates(path)
            self.assertEqual([item["law_id"] for item in candidates], ["002"])


if __name__ == "__main__":
    unittest.main()
