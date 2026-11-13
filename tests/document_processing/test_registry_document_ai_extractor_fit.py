# test_registry_document_ai_extractor_fit.py
"""
Description: 표 머리글의 좌우 끝을 기준으로 열 경계를 문서마다 맞추는 보정을 검증한다. 사진 촬영 여백·크기가 달라
모든 글자의 가로 위치가 옮겨져도 같은 값이 추출되어야 하고, 머리글이 부족한 입력은 기존 고정 경계로 읽어야 한다.
Author: ooheunsu
Date: 2026-11-13
Before:
    - 열 경계가 페이지 폭 기준 고정값이라 표 위치가 달라지면 열이 어긋났다.
After:
    - 머리글 좌우 끝을 기준 위치와 비교해 열 경계를 옮겨 쓴다(기준 위치와 같으면 변하지 않음).
"""

import unittest
from unittest.mock import patch

from ml.document_processing.contracts import registry_document_ai_extractor as extractor
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
                            "textSegments": [{"startIndex": str(start), "endIndex": str(end)}]
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
        result_pages.append({"pageNumber": page_number, "lines": entries, "tokens": entries})
    return {"text": text, "pages": result_pages}


def full_page():
    """표 머리글을 실제 등본의 글자 위치로 모두 갖춘 한 쪽이다."""

    return [
        ("등기사항전부증명서(현재 유효사항)", 0.30, 0.15, 0.70),
        ("【 표제부 】", 0.12, 0.26, 0.29),
        ("표시번호", 0.068, 0.285, 0.12),
        ("접수", 0.19, 0.285, 0.23),
        ("소재지번,건물명칭 및 번호", 0.31, 0.285, 0.49),
        ("건물내역", 0.59, 0.285, 0.66),
        ("등기원인 및 기타사항", 0.75, 0.285, 0.932),
        ("1", 0.10, 0.311, 0.11),
        ("서울특별시 성동구 성수동 1", 0.30, 0.314, 0.50),
        ("철근콘크리트구조", 0.52, 0.314, 0.66),
        ("2019년3월4일 등기", 0.76, 0.314, 0.90),
        ("【갑구 】", 0.12, 0.467, 0.29),
        ("순위번호", 0.067, 0.493, 0.13),
        ("등기목적", 0.19, 0.493, 0.26),
        ("접수", 0.36, 0.493, 0.39),
        ("등기원인", 0.47, 0.493, 0.55),
        ("권리자 및 기타사항", 0.70, 0.493, 0.842),
        ("1", 0.09, 0.519, 0.11),
        ("소유권이전", 0.15, 0.519, 0.24),
        ("2020년1월2일", 0.32, 0.519, 0.43),
        ("소유자", 0.62, 0.519, 0.66),
        ("홍길동", 0.665, 0.519, 0.71),
        ("【을구 】", 0.12, 0.596, 0.29),
        ("순위번호", 0.067, 0.62, 0.13),
        ("등기목적", 0.19, 0.62, 0.26),
        ("접수", 0.36, 0.62, 0.39),
        ("등기원인", 0.47, 0.62, 0.55),
        ("권리자 및 기타사항", 0.70, 0.62, 0.842),
        ("1", 0.09, 0.64, 0.11),
        ("근저당권설정", 0.15, 0.64, 0.25),
        ("2015년8월20일", 0.32, 0.64, 0.43),
        ("채권최고액", 0.62, 0.64, 0.69),
        ("금300,000,000원", 0.695, 0.64, 0.80),
        ("근저당권자", 0.62, 0.67, 0.69),
        ("가나다은행", 0.695, 0.67, 0.77),
        ("열람일시 : 2026년06월26일 14시17분29초", 0.06, 0.85, 0.5),
    ]


def apartment_page():
    """집합건물 표제부: 머리글이 있는 하위 표가 둘(1동의 건물, 전유부분)이다."""

    return [
        ("[집합건물] 서울특별시 성동구 성수동1가 685 알파아파트 제101동 제12층 제1203호", 0.06, 0.23, 0.80),
        ("【 표제부 】", 0.12, 0.26, 0.29),
        ("표시번호", 0.068, 0.285, 0.12),
        ("접수", 0.19, 0.285, 0.23),
        ("소재지번,건물명칭 및 번호", 0.31, 0.285, 0.49),
        ("건물내역", 0.59, 0.285, 0.66),
        ("등기원인 및 기타사항", 0.75, 0.285, 0.932),
        ("1", 0.10, 0.311, 0.11),
        ("서울특별시 성동구 성수동1가 685", 0.30, 0.314, 0.50),
        ("[도로명주소]", 0.30, 0.335, 0.40),
        ("서울특별시 성동구 알파로 27", 0.30, 0.352, 0.50),
        ("철근콘크리트조", 0.52, 0.314, 0.66),
        ("( 전유부분의 건물의 표시 )", 0.07, 0.40, 0.28),
        ("표시번호", 0.068, 0.43, 0.12),
        ("접수", 0.19, 0.43, 0.23),
        ("건물번호", 0.31, 0.43, 0.40),
        ("건물내역", 0.59, 0.43, 0.66),
        ("등기원인 및 기타사항", 0.75, 0.43, 0.932),
        ("1", 0.10, 0.456, 0.11),
        ("제12층 제1203호", 0.30, 0.459, 0.45),
        ("철근콘크리트조 84.97㎡", 0.52, 0.459, 0.70),
        ("【갑구 】", 0.12, 0.53, 0.29),
        ("순위번호", 0.067, 0.556, 0.13),
        ("등기목적", 0.19, 0.556, 0.26),
        ("접수", 0.36, 0.556, 0.39),
        ("등기원인", 0.47, 0.556, 0.55),
        ("권리자 및 기타사항", 0.70, 0.556, 0.842),
        ("1", 0.09, 0.582, 0.11),
        ("소유권이전", 0.15, 0.582, 0.24),
        ("소유자", 0.62, 0.582, 0.66),
        ("박도윤", 0.665, 0.582, 0.71),
        ("열람일시 : 2026년06월26일 14시17분29초", 0.06, 0.85, 0.5),
    ]


def moved(items, scale, offset):
    """모든 글자의 가로 위치를 확대·이동해 촬영 여백·크기가 다른 사진을 흉내 낸다."""

    return [(text, offset + scale * x_min, y, offset + scale * x_max) for text, x_min, y, x_max in items]


def values(result):
    encumbrance = result.encumbrances[0]
    return {
        "owner": result.owners[0].name.value,
        "purpose": encumbrance.registration_purpose.value,
        "amount": encumbrance.maximum_claim_amount.value,
        "holder": encumbrance.holder.value,
        "title": result.property_description[0].original_text,
    }


class RegistryColumnFitTests(unittest.TestCase):
    def test_apartment_title_reads_every_sub_table(self):
        result = extract_registry_document_ai(document_ai_json([apartment_page()]))
        texts = [item.original_text for item in result.property_description]
        self.assertEqual(len(texts), 2)
        self.assertIn("서울특별시 성동구 알파로 27", texts[0])
        self.assertIn("제12층 제1203호", texts[1])
        self.assertEqual(result.detail_address.value, "서울특별시 성동구 알파로 27")
        self.assertNotIn("전유부분", "".join(texts))  # 하위 표 제목은 본문에 섞이지 않는다
        self.assertEqual(result.owners[0].name.value, "박도윤")

    def test_apartment_page_gives_the_same_values_when_moved(self):
        base = extract_registry_document_ai(document_ai_json([apartment_page()]))
        result = extract_registry_document_ai(document_ai_json([moved(apartment_page(), 0.9, 0.04)]))
        self.assertEqual(
            [item.original_text for item in result.property_description],
            [item.original_text for item in base.property_description],
        )
        self.assertEqual(result.detail_address.value, base.detail_address.value)

    def test_reference_layout_is_unchanged(self):
        result = extract_registry_document_ai(document_ai_json([full_page()]))
        self.assertEqual(
            values(result),
            {
                "owner": "홍길동",
                "purpose": "근저당권설정",
                "amount": 300_000_000,
                "holder": "가나다은행",
                "title": values(result)["title"],
            },
        )
        self.assertIn("서울특별시 성동구 성수동 1", values(result)["title"])

    def test_shifted_and_scaled_page_gives_the_same_values(self):
        base = values(extract_registry_document_ai(document_ai_json([full_page()])))
        for scale, offset in ((0.85, 0.09), (1.08, -0.05), (0.92, 0.0)):
            with self.subTest(scale=scale, offset=offset):
                result = extract_registry_document_ai(
                    document_ai_json([moved(full_page(), scale, offset)])
                )
                self.assertEqual(values(result), base)

    def test_without_fitting_the_shifted_page_breaks(self):
        """고정 경계만 쓰면 크게 옮겨진 페이지에서 항목을 찾지 못함을 보여 보정의 효과를 확인한다."""

        shifted = document_ai_json([moved(full_page(), 0.85, 0.09)])
        with patch.object(extractor, "_fit_dividers", lambda tokens, section, default: default):
            result = extract_registry_document_ai(shifted)
        self.assertEqual(result.encumbrances, [])

    def test_too_narrow_header_keeps_default_dividers(self):
        default = extractor._RIGHTS_COLUMNS
        from ml.document_processing.contracts.document_ai_extractor import Line

        one_label = [Line("순위번호", 1, (0.067, 0.49, 0.13, 0.50))]
        self.assertEqual(extractor._fit_dividers(one_label, "gapgu", default), default)
        self.assertEqual(extractor._fit_dividers([], "gapgu", default), default)

    def test_out_of_range_scale_keeps_default_dividers(self):
        from ml.document_processing.contracts.document_ai_extractor import Line

        default = extractor._RIGHTS_COLUMNS
        # 머리글 폭 0.4는 기준(약 0.78)의 절반이라 허용 범위(0.7~1.4배) 밖이다.
        squeezed = [Line("순위번호", 1, (0.20, 0.49, 0.26, 0.50)), Line("권리자", 1, (0.55, 0.49, 0.60, 0.50))]
        self.assertEqual(extractor._fit_dividers(squeezed, "gapgu", default), default)


if __name__ == "__main__":
    unittest.main()
