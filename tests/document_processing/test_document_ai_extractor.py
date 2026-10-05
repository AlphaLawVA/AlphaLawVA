# test_document_ai_extractor.py
"""
Description: Google OCR 좌표 어댑터와 불일치 검증 및 평가의 회귀 테스트.
Author: ooheunsu
Date: 2026-10-05
Before:
    - 외부 호출 없는 추출기와 평가기가 구현됨.
After:
    - 숫자 오류 은폐, 날짜 이동, 특약 연결 회귀를 검사함.
"""

import unittest

from ml.document_processing.contracts.document_ai_extractor import (
    Line,
    dates,
    korean_amount,
    load_lines,
    money,
    special_terms,
    validate_amounts,
    brokers_from_lines,
    sale_articles,
)
from ml.evaluation.contracts.evaluate_document_ai import distance, evaluate


class DocumentAiExtractorTests(unittest.TestCase):
    def test_anchor_segments_and_pixel_fallback(self):
        doc = {
            "text": "abcDEF",
            "pages": [
                {
                    "dimension": {"width": 100, "height": 200},
                    "lines": [
                        {
                            "layout": {
                                "textAnchor": {
                                    "textSegments": [
                                        {"endIndex": "3"},
                                        {"startIndex": "3", "endIndex": "6"},
                                    ]
                                },
                                "boundingPoly": {
                                    "vertices": [{"x": 10, "y": 20}, {"x": 50, "y": 40}]
                                },
                            }
                        }
                    ],
                }
            ],
        }
        line = load_lines(doc)[0]
        assert line.text == "abcDEF"
        assert line.box == (0.1, 0.1, 0.5, 0.2)

    def test_amount_conflict_not_corrected(self):
        warnings = []
        lines = [Line("보증금 금 이억2천만 원정(₩250,000,000)", 1, (0, 0, 1, 0.1))]
        result = money(lines, "보증금", warnings)
        assert result["value"] == 250000000
        assert result["status"] == "low_confidence"
        assert warnings[0].startswith("money_conflict")
        assert korean_amount("삼억팔천") == 300008000
        assert korean_amount("이안") is None

    def test_sum_warns_without_repair(self):
        result = {
            "warnings": [],
            "payment": {
                "sale_price": {"value": 100},
                "down_payment": {"value": 10},
                "remaining_balance": {"value": 20},
                "intermediate_payments": [],
            },
        }
        validate_amounts(result)
        assert result["warnings"]
        assert result["payment"]["remaining_balance"]["value"] == 20

    def test_dates_join_horizontal_fragments_and_reject_invalid(self):
        lines = [
            Line("2026년", 1, (0.1, 0.1, 0.2, 0.12)),
            Line("10월 20일", 1, (0.3, 0.102, 0.5, 0.122)),
        ]
        assert dates(lines) == ["2026-10-20"]
        assert dates([Line("2026년 2월 31일", 1, (0, 0, 1, 0.1))]) == []

    def test_sale_number_and_body_share_row(self):
        lines = [
            Line("제7조", 3, (0, 0.1, 0.5, 0.12)),
            Line("내용이다.", 3, (0.2, 0.2, 0.8, 0.22)),
            Line("①", 3, (0.1, 0.205, 0.15, 0.225)),
            Line("2026년 10월 10일", 3, (0.1, 0.3, 0.8, 0.32)),
        ]
        result = special_terms(lines, True, [])
        assert len(result) == 1
        assert result[0]["original_text"] == "내용이다."

    def test_scalar_decimal_never_normalized_away(self):
        gold = {
            "property": {
                "building_area_m2": {"value": 58.32, "status": "user_confirmed"}
            },
            "special_terms": [],
        }
        pred = {
            "property": {
                "building_area_m2": {"value": 5832, "status": "low_confidence"}
            },
            "special_terms": [],
        }
        result = evaluate(gold, pred)
        assert result["exact_count"] == 0
        assert distance("abc", "ac") == 1

    def test_missing_clause_stays_missing(self):
        gold = {"special_terms": [{"item_order": 1, "original_text": "원문"}]}
        result = evaluate(gold, {"special_terms": []})
        assert result["special_terms"][0]["cer"] == 1

    def test_two_column_clause_uses_tokens_without_rewriting(self):
        lines = [
            Line("1. 왼쪽.", 3, (0.1, 0.1, 0.4, 0.12)),
            Line("2. 오른쪽", 3, (0.7, 0.1, 0.9, 0.12)),
            Line("마침. 계속.", 3, (0.1, 0.13, 0.9, 0.15)),
            Line("본 계약을 증명", 3, (0.1, 0.2, 0.9, 0.22)),
        ]
        tokens = [
            Line("마침.", 3, (0.1, 0.13, 0.4, 0.15)),
            Line("계속.", 3, (0.7, 0.13, 0.9, 0.15)),
        ]
        result = special_terms(lines, False, [], tokens)
        assert [item["original_text"] for item in result] == [
            "왼쪽. 마침.",
            "오른쪽 계속.",
        ]

    def test_unreadable_number_is_not_guessed(self):
        warnings = []
        lines = [
            Line("4. 앞 문장.", 3, (0.1, 0.1, 0.4, 0.12)),
            Line("동. 판독불가 문장.", 3, (0.1, 0.13, 0.4, 0.15)),
            Line("6. 뒤 문장.", 3, (0.1, 0.16, 0.4, 0.18)),
            Line("본 계약을 증명", 3, (0.1, 0.2, 0.9, 0.22)),
        ]
        result = special_terms(lines, False, warnings)
        assert [item["item_order"] for item in result] == [4, 6]
        assert any("판독불가 문장" in text for text in warnings)

    def test_broker_registration_preserves_missing_digit(self):
        lines = [
            Line("테스트 공인중개사사무소", 3, (0.2, 0.3, 0.4, 0.32)),
            Line("가상-지역-2026-00전화", 3, (0.2, 0.35, 0.4, 0.37)),
        ]
        result = brokers_from_lines(lines)
        assert result[0]["office_registration_number"]["value"] == "가상-지역-2026-00"
        assert "signature_present" not in result[0]

    def test_articles_preserve_verbatim_text(self):
        lines = [
            Line("제4조(하자)", 2, (0.1, 0.1, 0.4, 0.12)),
            Line("[따로 정하는 기간] 6개월 분견", 2, (0.1, 0.2, 0.8, 0.22)),
        ]
        assert sale_articles(lines)[0]["original_text"] == "6개월 분견"

    def test_full_width_clause_above_columns_is_not_split(self):
        lines = [
            Line("1. 전체 폭 문장.", 3, (0.1, 0.04, 0.9, 0.06)),
            Line("2. 왼쪽", 3, (0.1, 0.1, 0.4, 0.12)),
            Line("3. 오른쪽", 3, (0.7, 0.1, 0.9, 0.12)),
            Line("본 계약을 증명", 3, (0.1, 0.2, 0.9, 0.22)),
        ]
        tokens = [
            Line("1. 전체 폭", 3, (0.1, 0.04, 0.6, 0.06)),
            Line("문장.", 3, (0.7, 0.04, 0.9, 0.06)),
        ]
        result = special_terms(lines, False, [], tokens)
        assert result[0]["original_text"] == "전체 폭 문장."
