# test_handwritten_goldset.py
"""
Description: 손글씨 합성 계약서 정답 JSON이 계약 추출 스키마와 원본
manifest 연결 규칙을 만족하는지 검증한다.
Author: ooheunsu
Date: 2026-10-04
Before:
    - 전세·월세·매매 손글씨 계약서의 사람이 확정한 정답 JSON이 준비된 상태.
After:
    - 정답셋의 스키마, 계약 유형, 파일 연결 및 개인정보 제외가 자동 검증됨.
"""

import json
import unittest
from pathlib import Path

from ml.document_processing.contracts.extraction_schema import ContractExtraction


class HandwrittenContractGoldsetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        evaluation_dir = (
            Path(__file__).resolve().parents[3]
            / "data"
            / "contracts"
            / "evaluation"
        )
        cls.goldsets = []
        for goldset_dir in sorted(evaluation_dir.glob("goldset_v*")):
            manifest = json.loads(
                (goldset_dir / "manifest.json").read_text(encoding="utf-8")
            )
            cls.goldsets.append((goldset_dir, manifest))

    def test_all_manifest_documents_have_valid_gold_json(self):
        self.assertGreaterEqual(len(self.goldsets), 2)
        for goldset_dir, manifest in self.goldsets:
            with self.subTest(goldset=goldset_dir.name):
                self.assertEqual(len(manifest["documents"]), 3)
                for document in manifest["documents"]:
                    gold_path = goldset_dir / document["gold_filename"]
                    payload = json.loads(gold_path.read_text(encoding="utf-8"))
                    contract = ContractExtraction.model_validate(payload)

                    self.assertEqual(
                        contract.contract_type.value.value,
                        document["contract_type"],
                    )
                    self.assertEqual(len(document["source_sha256"]), 64)
                    self.assertEqual(document["page_count"], 3)

    def test_gold_json_excludes_resident_numbers_and_phone_numbers(self):
        for goldset_dir, manifest in self.goldsets:
            for document in manifest["documents"]:
                text = (goldset_dir / document["gold_filename"]).read_text(
                    encoding="utf-8"
                )
                self.assertNotRegex(text, r"\d{6}\s*-?\s*\d{7}")
                self.assertNotRegex(text, r"01[016789]-\d{3,4}-\d{4}")


if __name__ == "__main__":
    unittest.main()
