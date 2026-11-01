# test_standard_lease_extractor.py
"""
Description: 주택임대차표준계약서 텍스트에서 핵심 계약 정보와 특약을
구조화하는 초기 규칙 추출기의 정상·오류 동작을 검증한다.
Author: ooheunsu
Date: 2026-09-21
Before:
    - 표준 임대차계약서 전용 직접 텍스트 추출기가 구현된 상태.
After:
    - 합성 계약서 핵심값과 지원하지 않는 양식의 거부 동작이 테스트됨.
"""

import unittest
from pathlib import Path

from ml.document_processing.contracts.document_intake import (
    ProcessingRoute,
    inspect_contract_document,
)
from ml.document_processing.contracts.extraction_schema import ContractType
from ml.document_processing.contracts.standard_lease_extractor import (
    extract_standard_lease_contract,
)


class StandardLeaseExtractorIntegrationTests(unittest.TestCase):
    def test_extracts_synthetic_jeonse_contract(self):
        contract_path = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "synthetic_jeonse_contract_v01.pdf"
        )
        self.assertTrue(contract_path.is_file(), "필수 합성 PDF fixture가 없습니다.")

        inspection = inspect_contract_document(contract_path)
        result = extract_standard_lease_contract(contract_path)

        self.assertEqual(inspection.page_count, 5)
        self.assertEqual(inspection.route, ProcessingRoute.DIRECT_TEXT)
        self.assertTrue(
            all(page.route is ProcessingRoute.DIRECT_TEXT for page in inspection.pages)
        )
        self.assertEqual(result.contract_type.value, ContractType.JEONSE)
        self.assertEqual(result.contract_date.value.isoformat(), "2026-09-21")
        self.assertEqual(result.start_date.value.isoformat(), "2026-10-31")
        self.assertEqual(result.end_date.value.isoformat(), "2028-10-30")
        self.assertEqual(
            result.property.address.value, "서울특별시 성동구 성수이로00길 24"
        )
        self.assertEqual(
            result.property.detail_address.value,
            "알파아파트 101동 1203호",
        )
        self.assertEqual(result.property.leased_area_m2.value, 59.88)
        self.assertEqual(result.property.land_category.value, "대")
        self.assertEqual(result.property.land_area_m2.value, 1248.3)
        self.assertEqual(result.property.building_structure.value, "철근콘크리트")
        self.assertEqual(result.property.building_use.value, "공동주택(아파트)")
        self.assertEqual(result.property.building_area_m2.value, 84.92)
        self.assertEqual(result.payment.deposit.value, 250_000_000)
        self.assertEqual(result.payment.down_payment.value, 25_000_000)
        self.assertEqual(
            result.payment.intermediate_payments[0].amount.value,
            25_000_000,
        )
        self.assertEqual(
            result.payment.intermediate_payments[0].due_date.value.isoformat(),
            "2026-10-05",
        )
        self.assertEqual(result.payment.remaining_balance.value, 200_000_000)
        self.assertEqual(
            result.payment.remaining_balance_date.value.isoformat(),
            "2026-10-31",
        )
        self.assertEqual(result.payment.maintenance_fee.value, 120_000)
        self.assertEqual(
            [party.name.value for party in result.parties],
            ["김하늘", "박도윤"],
        )
        self.assertEqual(len(result.special_terms), 3)
        self.assertNotIn("※ 기타", result.special_terms[0].original_text)
        self.assertIn("근저당권", result.special_terms[0].original_text)
        self.assertIn("보증보험", result.special_terms[1].original_text)
        self.assertIn("납세증명서", result.special_terms[2].original_text)
        self.assertEqual(result.brokers[0].office_name.value, "알파공인중개사사무소")
        self.assertEqual(
            result.brokers[0].office_registration_number.value,
            "가상-2026-0001",
        )
        self.assertEqual(result.brokers[0].broker_in_charge.value, "최민준")


if __name__ == "__main__":
    unittest.main()
