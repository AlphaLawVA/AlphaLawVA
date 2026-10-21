# test_registry_document_ai_extractor.py
"""
Description: Document AI 좌표 기반 등기부 추출기의 섹션·항목 구조화를 검증한다.
Author: ooheunsu
Date: 2026-10-19
Before:
    - 등기부 추출기가 저장된 Document AI JSON을 입력받음.
After:
    - 표제부·갑구·을구와 사용자 확인 전 상태가 단위 테스트로 검증됨.
"""

import unittest

from ml.document_processing.contracts.registry_document_ai_extractor import (
    extract_registry_document_ai,
)


def document_ai_json(page_lines):
    text = ""
    pages = []
    for page_number, lines in enumerate(page_lines, 1):
        items = []
        for line_text, box in lines:
            start = len(text)
            text += line_text + "\n"
            end = start + len(line_text)
            x_min, y_min, x_max, y_max = box
            items.append(
                {
                    "layout": {
                        "textAnchor": {
                            "textSegments": [
                                {"startIndex": str(start), "endIndex": str(end)}
                            ]
                        },
                        "boundingPoly": {
                            "normalizedVertices": [
                                {"x": x_min, "y": y_min},
                                {"x": x_max, "y": y_min},
                                {"x": x_max, "y": y_max},
                                {"x": x_min, "y": y_max},
                            ]
                        },
                    }
                }
            )
        pages.append({"pageNumber": page_number, "lines": items})
    return {"text": text, "pages": pages}


class RegistryDocumentAIExtractorTests(unittest.TestCase):
    def test_extracts_sections_and_preserves_unknown_status(self):
        document = document_ai_json(
            [
                [
                    ("등기사항전부증명서(현재 유효사항)", (0.2, 0.03, 0.8, 0.06)),
                    ("[건물] 서울특별시 성동구 성수동", (0.05, 0.08, 0.5, 0.1)),
                    ("고유번호 1101-2026-000001", (0.7, 0.08, 0.95, 0.1)),
                    ("[ 표 제 부 ]", (0.05, 0.15, 0.25, 0.18)),
                    ("3", (0.05, 0.25, 0.08, 0.27)),
                    ("철근콘크리트조 공동주택", (0.35, 0.25, 0.6, 0.3)),
                    (
                        "[도로명주소] 서울특별시 성동구 테스트로 1",
                        (0.35, 0.31, 0.7, 0.34),
                    ),
                    ("[ 갑 구 ]", (0.05, 0.4, 0.25, 0.43)),
                    ("2", (0.05, 0.5, 0.08, 0.52)),
                    ("공유자전원지분전부이전", (0.15, 0.5, 0.3, 0.55)),
                    ("2013년 6월 20일", (0.34, 0.5, 0.46, 0.55)),
                    ("2012년 11월 16일 매매", (0.5, 0.5, 0.6, 0.55)),
                    ("소유자 홍길동 2분의 1", (0.65, 0.5, 0.9, 0.55)),
                    ("[ 을 구 ]", (0.05, 0.65, 0.25, 0.68)),
                    ("4", (0.05, 0.75, 0.08, 0.77)),
                    ("근저당권설정", (0.15, 0.75, 0.3, 0.78)),
                    ("2015년 8월 20일", (0.34, 0.75, 0.46, 0.78)),
                    ("설정계약", (0.5, 0.75, 0.6, 0.78)),
                    (
                        "채권최고액 금300,000,000원 채무자 홍길동",
                        (0.65, 0.75, 0.95, 0.82),
                    ),
                    (
                        "열람일시 : 2026년06월26일 14시17분29초",
                        (0.05, 0.95, 0.4, 0.98),
                    ),
                ],
                [
                    ("[건물] 서울특별시 성동구 성수동", (0.05, 0.05, 0.5, 0.08)),
                    ("4-1", (0.05, 0.2, 0.09, 0.22)),
                    ("4번근저당권공동담보변경", (0.15, 0.2, 0.3, 0.25)),
                    ("공동담보 토지 서울특별시", (0.65, 0.2, 0.95, 0.25)),
                    ("-- 이 하 여 백 --", (0.4, 0.6, 0.6, 0.63)),
                ],
            ]
        )

        result = extract_registry_document_ai(document)

        self.assertEqual(result.address.value, "서울특별시 성동구 성수동")
        self.assertEqual(
            result.detail_address.value, "서울특별시 성동구 테스트로 1"
        )
        self.assertEqual(result.unique_property_number.value, "1101-2026-000001")
        self.assertEqual(result.issued_at.value.isoformat(), "2026-06-26T14:17:29+09:00")
        self.assertEqual(result.owners[0].name.value, "홍길동")
        self.assertEqual(result.owners[0].registration_cause.value, "매매")
        self.assertEqual(result.owners[0].status.value, "unknown")
        self.assertEqual(
            [entry.rank_number.value for entry in result.encumbrances], ["4", "4-1"]
        )
        self.assertEqual(
            result.encumbrances[0].maximum_claim_amount.value, 300_000_000
        )
        self.assertTrue(
            all(entry.status.value == "unknown" for entry in result.encumbrances)
        )

    def test_missing_owner_and_rights_remain_review_warnings(self):
        document = document_ai_json(
            [
                [
                    ("등기사항전부증명서", (0.2, 0.03, 0.8, 0.06)),
                    ("[건물] 서울특별시 성동구 성수동", (0.05, 0.08, 0.5, 0.1)),
                    ("[ 표 제 부 ]", (0.05, 0.15, 0.25, 0.18)),
                    ("[ 갑 구 ]", (0.05, 0.4, 0.25, 0.43)),
                    ("[ 을 구 ]", (0.05, 0.65, 0.25, 0.68)),
                ]
            ]
        )

        result = extract_registry_document_ai(document)

        self.assertIsNone(result.issued_at.value)
        self.assertIsNone(result.detail_address.value)
        self.assertTrue(any("owner_missing" in item for item in result.warnings))
        self.assertTrue(any("encumbrance_missing" in item for item in result.warnings))


if __name__ == "__main__":
    unittest.main()
