# test_contract_review_address_match.py
"""
Description: 계약서 주소와 등본 주소를 도로명·지번 표기 모두로 비교하는 규칙을 검증한다.
더미 등본(지번 소재지 + 도로명주소 표기)과 같은 구성으로 일치·불일치 경우를 확인한다.
Author: ooheunsu
Date: 2026-11-04
Before:
    - 계약서 도로명 주소와 등본 지번 소재지를 글자 그대로 비교해 같은 집도 불일치로 표시됨.
After:
    - 등본의 소재지와 도로명주소 중 하나라도 계약서 주소와 맞으면 일치로 본다.
"""

import unittest

from ml.agent.contract_review_graph import ContractReviewGraph
from tests.test_registry_attention_items import _contract, _registry

ROAD = "서울특별시 성동구 알파로 27"
JIBUN = "서울특별시 성동구 성수동1가 685"


def _address_items(contract_address, registry_address, registry_detail=None):
    contract = _contract()
    contract.contract.property.address = contract_address
    registry = _registry()
    registry = registry.model_copy(
        update={"address": registry_address, "detail_address": registry_detail}
    )
    result = ContractReviewGraph().prepare(contract, registry)
    return [i for i in result.items if i.item_id == "property_address_match"]


class AddressMatchTests(unittest.TestCase):
    def test_road_contract_matches_registry_road_address(self) -> None:
        self.assertEqual(_address_items(ROAD, JIBUN, ROAD), [])

    def test_jibun_contract_matches_registry_jibun(self) -> None:
        self.assertEqual(_address_items(JIBUN, JIBUN, ROAD), [])

    def test_registry_road_address_with_building_name_still_matches(self) -> None:
        self.assertEqual(
            _address_items(ROAD, JIBUN, f"{ROAD} 알파아파트 101동 1203호"), []
        )

    def test_spacing_and_punctuation_are_ignored(self) -> None:
        self.assertEqual(
            _address_items("서울특별시 성동구 알파로27", JIBUN, f"[도로명] {ROAD}"), []
        )

    def test_different_address_still_needs_confirmation(self) -> None:
        items = _address_items(
            "서울특별시 강남구 테헤란로 5", JIBUN, ROAD
        )
        self.assertEqual(len(items), 1)

    def test_road_contract_with_only_jibun_registry_needs_confirmation(self) -> None:
        # 등본에 도로명주소가 없으면 자동 일치로 볼 수 없으므로 사용자 확인으로 남긴다.
        self.assertEqual(len(_address_items(ROAD, JIBUN, None)), 1)


if __name__ == "__main__":
    unittest.main()
