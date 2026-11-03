# test_registry_document_ai_extractor_layout.py
"""
Description: 실제 등본 양식의 열 좌표와 OCR 조각·병합 특성을 반영한 합성 데이터로
등기부 추출기의 열 배정, 섹션 분리, 쪽 이어짐, 마스킹, 위험 등기 보존을 검증한다.
Author: ooheunsu
Date: 2026-11-04
Before:
    - 등기부 추출기가 줄 단위·고정 열 가정의 합성 데이터로만 검증됨.
After:
    - 갑구·을구 열 배정과 을구 표식 분리, 부기 항목, 법인등록번호 마스킹이 회귀 테스트됨.
"""

import unittest

from ml.document_processing.contracts.registry_document_ai_extractor import (
    extract_registry_document_ai,
)


def document_ai_json(pages):
    """페이지별 (텍스트, x_min, y, x_max) 목록을 줄·토큰 좌표가 같은 JSON으로 만든다."""

    text = ""
    result_pages = []
    for page_number, items in enumerate(pages, 1):
        entries = []
        for item_text, x_min, y, x_max in items:
            start = len(text)
            text += item_text + "\n"
            end = start + len(item_text)
            entries.append(
                {
                    "layout": {
                        "textAnchor": {
                            "textSegments": [
                                {"startIndex": str(start), "endIndex": str(end)}
                            ]
                        },
                        "boundingPoly": {
                            "normalizedVertices": [
                                {"x": x_min, "y": y - 0.006},
                                {"x": x_max, "y": y - 0.006},
                                {"x": x_max, "y": y + 0.006},
                                {"x": x_min, "y": y + 0.006},
                            ]
                        },
                    }
                }
            )
        result_pages.append(
            {"pageNumber": page_number, "lines": entries, "tokens": entries}
        )
    return {"text": text, "pages": result_pages}


def first_page():
    return [
        ("[건물] 서울특별시 성동구 성수동", 0.06, 0.23, 0.28),
        ("고유번호 1101-2026-000001", 0.74, 0.20, 0.93),
        ("【 표제부 】", 0.12, 0.26, 0.29),
        ("표시번호", 0.07, 0.285, 0.13),
        ("1", 0.10, 0.311, 0.11),
        ("철근", 0.52, 0.314, 0.55),
        ("콘크리트구조", 0.553, 0.314, 0.65),
        ("단독주택(다가구주택),", 0.52, 0.352, 0.72),
        ("제2", 0.52, 0.366, 0.55),
        ("종근생(사무소)", 0.553, 0.366, 0.65),
        ("【갑구 】", 0.12, 0.467, 0.29),
        ("순위번호", 0.07, 0.493, 0.13),
        ("1", 0.09, 0.519, 0.11),
        ("소유권이전", 0.15, 0.519, 0.24),
        ("2020년1월2일", 0.32, 0.519, 0.43),
        ("제100호", 0.32, 0.532, 0.39),
        ("매매", 0.46, 0.532, 0.49),
        ("소유자", 0.62, 0.519, 0.66),
        ("홍길동", 0.665, 0.519, 0.71),
        ("850101-*******", 0.72, 0.519, 0.85),
        ("2", 0.09, 0.56, 0.11),
        ("가압류", 0.15, 0.56, 0.20),
        ("2023년3월4일", 0.32, 0.56, 0.43),
        ("채권자", 0.62, 0.56, 0.66),
        ("김채권", 0.665, 0.56, 0.71),
        ("【을", 0.12, 0.596, 0.17),
        ("구】", 0.24, 0.596, 0.29),
        ("4", 0.09, 0.64, 0.11),
        ("근저당권", 0.15, 0.64, 0.22),
        ("설정", 0.223, 0.64, 0.25),
        ("2015년8월20일", 0.32, 0.64, 0.43),
        ("채권최고액", 0.62, 0.64, 0.69),
        ("금300,000,000원", 0.695, 0.64, 0.80),
        ("채무자", 0.62, 0.655, 0.66),
        ("홍길동", 0.665, 0.655, 0.71),
        ("근저당권자", 0.62, 0.67, 0.69),
        ("가나다은행", 0.695, 0.67, 0.77),
        ("111136-0000166", 0.78, 0.67, 0.92),
        ("열람일시 : 2026년06월26일 14시17분29초", 0.06, 0.85, 0.5),
    ]


def second_page():
    return [
        ("[건물] 서울특별시 성동구 성수동", 0.06, 0.12, 0.28),
        ("순위번호", 0.07, 0.147, 0.13),
        ("4-1", 0.09, 0.19, 0.12),
        ("4번근저당권", 0.15, 0.19, 0.25),
        ("변경", 0.253, 0.19, 0.28),
        ("2018년5월28일", 0.32, 0.19, 0.43),
        ("-- 이 하 여 백 --", 0.40, 0.31, 0.60),
        ("열람일시 : 2026년06월26일 14시17분29초", 0.06, 0.85, 0.5),
    ]


class RegistryExtractorLayoutTests(unittest.TestCase):
    def setUp(self):
        self.result = extract_registry_document_ai(
            document_ai_json([first_page(), second_page()])
        )

    def test_reads_header_fields_and_title_description(self):
        result = self.result

        self.assertEqual(result.address.value, "서울특별시 성동구 성수동")
        self.assertEqual(result.unique_property_number.value, "1101-2026-000001")
        self.assertEqual(result.issued_at.value.isoformat(), "2026-06-26T14:17:29+09:00")
        description = result.property_description[0].original_text
        self.assertIn("철근콘크리트구조", description)
        self.assertIn("종근생(사무소)", description)

    def test_assigns_owner_columns_and_status_stays_unknown(self):
        owner = self.result.owners[0]

        self.assertEqual(owner.name.value, "홍길동")
        self.assertEqual(owner.registration_date.value.isoformat(), "2020-01-02")
        self.assertEqual(owner.registration_cause.value, "매매")
        self.assertEqual(owner.status.value, "unknown")

    def test_splits_eulgu_marker_and_reads_mortgage_columns(self):
        mortgage = next(
            item for item in self.result.encumbrances if item.rank_number.value == "4"
        )

        self.assertEqual(mortgage.section.value, "eulgu")
        self.assertEqual(mortgage.registration_purpose.value, "근저당권설정")
        self.assertEqual(mortgage.registration_date.value.isoformat(), "2015-08-20")
        self.assertEqual(mortgage.maximum_claim_amount.value, 300000000)
        self.assertEqual(mortgage.holder.value, "가나다은행")
        self.assertEqual(mortgage.debtor.value, "홍길동")

    def test_continues_section_on_next_page_and_masks_registration_numbers(self):
        numbers = [item.rank_number.value for item in self.result.encumbrances]

        self.assertIn("4-1", numbers)
        dumped = self.result.model_dump_json()
        self.assertNotIn("111136-0000166", dumped)
        self.assertIn("등록번호 마스킹", dumped)

    def test_keeps_risk_entries_in_gapgu_as_encumbrances_not_owners(self):
        seizure = next(
            item for item in self.result.encumbrances if item.rank_number.value == "2"
        )

        self.assertEqual(seizure.section.value, "gapgu")
        self.assertEqual(seizure.registration_purpose.value, "가압류")
        self.assertEqual(len(self.result.owners), 1)


if __name__ == "__main__":
    unittest.main()
