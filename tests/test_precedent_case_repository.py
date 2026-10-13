# test_precedent_case_repository.py
"""
Description: final_cases 판례 저장소의 정상 조회와 경로·데이터 검증을 확인한다.
임시 데이터만 사용하여 실제 판례 파일을 수정하지 않고 오류 경계를 검증한다.
Author: choeminju
Date: 2026-10-13
Before:
    - final_cases JSON을 안전하게 읽는 판례 저장소가 정의된 상태.
After:
    - 정상 조회와 누락·손상·식별자 불일치·경로 조작 차단이 자동 검증됨.
"""

import json
import tempfile
import unittest
from pathlib import Path

from api.services.precedent_case_repository import (
    InvalidPrecedentIdError,
    PrecedentCaseDataError,
    PrecedentCaseNotFoundError,
    PrecedentCaseRepository,
)


class PrecedentCaseRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.cases_dir = Path(self.temporary_directory.name)
        self.repository = PrecedentCaseRepository(self.cases_dir)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write_case(self, filename: str, data: object) -> None:
        (self.cases_dir / filename).write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_reads_case_by_validated_precedent_id(self):
        self.write_case(
            "64441.json",
            {
                "판례일련번호": "64441",
                "사건번호": "2006다62492,62508",
                "쉬운요약": "경매 신청과 임대차 종료를 다룬 사건입니다.",
            },
        )

        case_data = self.repository.get(" 64441 ")

        self.assertEqual(case_data["판례일련번호"], "64441")
        self.assertIn("쉬운요약", case_data)

    def test_rejects_path_traversal_and_non_ascii_digits(self):
        for invalid_id in ("../64441", "64441.json", "", "６４４４１"):
            with self.subTest(precedent_id=invalid_id):
                with self.assertRaises(InvalidPrecedentIdError):
                    self.repository.get(invalid_id)

    def test_reports_missing_case(self):
        with self.assertRaises(PrecedentCaseNotFoundError):
            self.repository.get("99999")

    def test_rejects_malformed_or_non_object_json(self):
        (self.cases_dir / "111.json").write_text("{broken", encoding="utf-8")
        self.write_case("222.json", ["not", "an", "object"])

        with self.assertRaises(PrecedentCaseDataError):
            self.repository.get("111")
        with self.assertRaises(PrecedentCaseDataError):
            self.repository.get("222")

    def test_rejects_mismatched_stored_id(self):
        self.write_case("111.json", {"판례일련번호": "222"})

        with self.assertRaises(PrecedentCaseDataError):
            self.repository.get("111")


if __name__ == "__main__":
    unittest.main()
