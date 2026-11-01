# test_extraction_schema.py
"""
Description: 세 문서 추출 스키마의 계약 유형별 필수값, 주민등록번호 차단,
출처 좌표와 결합 스냅샷 검증 및 JSON Schema 내보내기를 확인한다.
Author: ooheunsu
Date: 2026-09-20
Before:
    - 계약 검토용 Pydantic 추출 스키마와 내보내기 함수가 구현된 상태.
After:
    - ERD JSON 컬럼에 잘못된 형식과 민감정보가 저장되지 않도록 검증됨.
"""

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from ml.document_processing.contracts.export_extraction_schemas import (
    export_extraction_schemas,
)
from ml.document_processing.contracts.extraction_schema import (
    CONTRACT_SCHEMA_VERSION,
    EXPLANATION_SCHEMA_VERSION,
    REGISTRY_SCHEMA_VERSION,
    SNAPSHOT_SCHEMA_VERSION,
    ContractExtraction,
    ExplanationSheetExtraction,
    RegistryExtraction,
    ReviewExtractionSnapshot,
    extraction_json_schemas,
)


def source(page=1):
    return {
        "page_number": page,
        "bounding_box": {
            "x_min": 0.1,
            "y_min": 0.2,
            "x_max": 0.8,
            "y_max": 0.3,
        },
    }


def extracted(value, raw_text=None, page=1):
    return {
        "value": value,
        "raw_text": raw_text if raw_text is not None else str(value),
        "source": source(page),
        "confidence": 0.95,
        "status": "extracted",
    }


def missing():
    return {"value": None, "status": "missing"}


def property_data():
    return {
        "address": extracted("서울시 성동구 성수동 101-12"),
        "detail_address": extracted("B동 802호"),
        "leased_part_description": extracted("802호 전부"),
        "leased_area_m2": extracted(84.51, "84.51㎡"),
    }


def monthly_contract_data():
    return {
        "contract_type": extracted("monthly_rent", "월세"),
        "contract_date": extracted("2026-09-20"),
        "start_date": extracted("2026-10-01"),
        "end_date": extracted("2028-09-30"),
        "property": property_data(),
        "parties": [
            {"role": "lessor", "name": extracted("임대인가")},
            {"role": "lessee", "name": extracted("임차인가")},
        ],
        "payment": {
            "deposit": extracted(50_000_000, "금 오천만원정"),
            "monthly_rent": extracted(1_200_000, "금 일백이십만원정"),
            "down_payment": extracted(5_000_000),
            "remaining_balance": extracted(45_000_000),
        },
        "articles": [
            {
                "item_order": 1,
                "clause_number": "제2조",
                "clause_title": "존속기간",
                "original_text": "임대차 기간은 2028년 9월 30일까지로 한다.",
                "source": source(),
                "confidence": 0.93,
            }
        ],
        "special_terms": [],
    }


class ExtractionSchemaTests(unittest.TestCase):
    def test_accepts_monthly_rent_and_sale_contracts(self):
        monthly = ContractExtraction.model_validate(monthly_contract_data())
        sale_data = monthly_contract_data()
        sale_data["contract_type"] = extracted("sale", "매매")
        sale_data["payment"] = {
            "sale_price": extracted(700_000_000, "금 칠억원정"),
            "down_payment": extracted(70_000_000),
            "remaining_balance": extracted(630_000_000),
        }
        sale_data["ownership_transfer_date"] = extracted("2026-12-01")
        sale = ContractExtraction.model_validate(sale_data)

        self.assertEqual(monthly.schema_version, CONTRACT_SCHEMA_VERSION)
        self.assertEqual(monthly.payment.deposit.value, 50_000_000)
        self.assertEqual(sale.payment.sale_price.value, 700_000_000)

    def test_requires_contract_specific_payment_fields(self):
        data = monthly_contract_data()
        data["payment"].pop("monthly_rent")

        with self.assertRaisesRegex(ValidationError, "monthly_rent"):
            ContractExtraction.model_validate(data)

    def test_rejects_resident_registration_number_anywhere(self):
        data = monthly_contract_data()
        data["special_terms"] = [
            {
                "item_order": 1,
                "original_text": "임대인 주민번호는 900101-1234567이다.",
                "source": source(2),
            }
        ]

        with self.assertRaisesRegex(ValidationError, "주민등록번호"):
            ContractExtraction.model_validate(data)

    def test_rejects_unknown_fields_and_invalid_bounding_box(self):
        data = monthly_contract_data()
        data["owner_id_number"] = "900101-1******"
        with self.assertRaises(ValidationError):
            ContractExtraction.model_validate(data)

        data = monthly_contract_data()
        data["property"]["address"]["source"]["bounding_box"]["x_min"] = 0.9
        with self.assertRaisesRegex(ValidationError, "최소 좌표"):
            ContractExtraction.model_validate(data)

    def test_accepts_registry_and_optional_explanation_sheet_snapshot(self):
        contract = ContractExtraction.model_validate(monthly_contract_data())
        registry = RegistryExtraction.model_validate(
            {
                "issued_at": extracted("2026-09-20T10:30:00+09:00"),
                "unique_property_number": extracted("1101-2026-000001"),
                "address": extracted("서울시 성동구 성수동 101-12"),
                "detail_address": extracted("B동 802호"),
                "owners": [
                    {
                        "name": extracted("임대인가"),
                        "ownership_share": extracted("1분의 1"),
                        "status": "active",
                    }
                ],
                "encumbrances": [
                    {
                        "section": "eulgu",
                        "rank_number": extracted("1"),
                        "registration_purpose": extracted("근저당권설정"),
                        "maximum_claim_amount": extracted(200_000_000),
                        "raw_text": "채권최고액 금 이억원",
                        "source": source(),
                        "status": "active",
                    }
                ],
            }
        )
        explanation = ExplanationSheetExtraction.model_validate(
            {
                "contract_type": extracted("monthly_rent", "임대"),
                "property": property_data(),
                "evidence_documents": [
                    {
                        "item_order": 1,
                        "original_text": "등기사항증명서",
                        "source": source(),
                    }
                ],
            }
        )
        snapshot = ReviewExtractionSnapshot(
            contract=contract,
            registry=registry,
            explanation_sheet=explanation,
        )

        self.assertEqual(registry.schema_version, REGISTRY_SCHEMA_VERSION)
        self.assertEqual(explanation.schema_version, EXPLANATION_SCHEMA_VERSION)
        self.assertEqual(snapshot.schema_version, SNAPSHOT_SCHEMA_VERSION)
        self.assertEqual(snapshot.registry.owners[0].name.value, "임대인가")

    def test_rejects_mismatched_explanation_sheet_contract_type(self):
        explanation = {
            "contract_type": extracted("sale", "매매"),
            "property": property_data(),
        }

        with self.assertRaisesRegex(ValidationError, "계약 유형"):
            ReviewExtractionSnapshot.model_validate(
                {
                    "contract": monthly_contract_data(),
                    "explanation_sheet": explanation,
                }
            )

    def test_exports_versioned_json_schemas(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = export_extraction_schemas(Path(temp_dir))
            names = {path.name for path in paths}
            contract_schema = json.loads(
                (Path(temp_dir) / "contract_extraction_v01.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(len(paths), 4)
        self.assertIn("review_extraction_snapshot_v01.json", names)
        self.assertEqual(contract_schema["title"], "ContractExtraction")
        self.assertNotIn("resident", json.dumps(contract_schema).lower())

    def test_checked_in_json_schemas_match_pydantic_models(self):
        schema_dir = (
            Path(__file__).resolve().parents[3]
            / "ml"
            / "document_processing"
            / "contracts"
            / "schema"
        )

        for filename, expected_schema in extraction_json_schemas().items():
            stored_schema = json.loads(
                (schema_dir / filename).read_text(encoding="utf-8")
            )
            self.assertEqual(stored_schema, expected_schema, filename)

    def test_missing_status_cannot_keep_a_value(self):
        data = monthly_contract_data()
        data["contract_date"] = {"value": "2026-09-20", "status": "missing"}

        with self.assertRaisesRegex(ValidationError, "value를 저장"):
            ContractExtraction.model_validate(data)


if __name__ == "__main__":
    unittest.main()
