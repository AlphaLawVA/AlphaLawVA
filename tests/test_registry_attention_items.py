# test_registry_attention_items.py
"""
Description: 등본의 확인 필요 항목과 계약서 교차 안내 규칙을 더미 등본 3종 기준으로 검증한다.
모든 안내는 판정 없이 확인을 요청하는 고정 문구여야 한다.
Author: ooheunsu
Date: 2026-11-04
Before:
    - 압류·경매·가등기 등 등본 신호와 특약 제안을 안내하는 규칙이 없는 상태.
After:
    - 신호별 고정 안내와 특약·세금 확인 안내를 회귀 검증할 수 있음.
"""

import unittest
from datetime import UTC, datetime
from uuid import uuid4

from api.models.contract_snapshots import ConfirmedContractSnapshot
from api.models.contracts import ReviewItemStatus
from api.models.registry_snapshots import ConfirmedRegistrySnapshot
from ml.agent.contract_review_graph import ContractReviewGraph
from ml.agent.registry_attention import (
    ATTENTION_DISCLAIMER,
    build_registry_attention_items,
)

FORBIDDEN_WORDS = ("위험", "안전", "불가", "무효", "큰일", "못 받", "의심")


def _confirmation() -> dict:
    return {
        "confirmed_by_user": True,
        "revision": 1,
        "confirmed_at": datetime.now(UTC).isoformat(),
    }


def _contract(
    *,
    contract_type: str = "jeonse",
    special_terms: list[str] | None = None,
) -> ConfirmedContractSnapshot:
    is_sale = contract_type == "sale"
    return ConfirmedContractSnapshot.model_validate(
        {
            "snapshot_id": str(uuid4()),
            "document_id": str(uuid4()),
            "confirmation": _confirmation(),
            "contract": {
                "contract_type": contract_type,
                "property": {"address": "서울특별시 성동구 알파로 27"},
                "parties": [
                    {"role": "seller" if is_sale else "lessor", "name": "박도윤"},
                    {"role": "buyer" if is_sale else "lessee", "name": "김서연"},
                ],
                **(
                    {"sale_price": {"amount": 200000000}}
                    if is_sale
                    else {
                        "lease_deposit": {"amount": 200000000},
                        "start_date": "2026-11-01",
                        "end_date": "2028-10-31",
                    }
                ),
                "special_terms": [
                    {
                        "term_id": f"term-{index}",
                        "order": index,
                        "original_text": text,
                        "source": {"page_number": 3},
                    }
                    for index, text in enumerate(special_terms or [], start=1)
                ],
            },
        }
    )


def _entry(section, rank, purpose, raw="원문", status="unknown", amount=None):
    entry = {
        "section": section,
        "rank_number": rank,
        "registration_purpose": purpose,
        "raw_text": raw,
        "source": {"page_number": 1},
        "status": status,
    }
    if amount is not None:
        entry["maximum_claim_amount"] = amount
    return entry


def _registry(encumbrances=None, description="아파트") -> ConfirmedRegistrySnapshot:
    return ConfirmedRegistrySnapshot.model_validate(
        {
            "snapshot_id": str(uuid4()),
            "document_id": str(uuid4()),
            "confirmation": _confirmation(),
            "issued_at": "2026-10-25T12:00:00+09:00",
            "address": "서울특별시 성동구 알파로 27",
            "property_description": [
                {
                    "order": 1,
                    "original_text": description,
                    "source": {"page_number": 1},
                }
            ],
            "owners": [{"name": "박도윤", "status": "active"}],
            "encumbrances": encumbrances or [],
        }
    )


def _ids(items) -> set[str]:
    return {item.item_id for item in items}


def _by_id(items, item_id):
    return next(item for item in items if item.item_id == item_id)


class RegistryAttentionItemsTests(unittest.TestCase):
    def test_clean_registry_has_no_signal_items(self) -> None:
        items = build_registry_attention_items(_contract(), _registry())

        signal_ids = {i for i in _ids(items) if i.startswith("attention_")}
        self.assertEqual(signal_ids, set())

    def test_risk_collection_dummy_lists_every_signal(self) -> None:
        registry = _registry(
            [
                _entry("gapgu", "2", "가압류"),
                _entry("gapgu", "3", "임의경매개시결정"),
                _entry("gapgu", "4", "소유권이전청구권가등기"),
                _entry("eulgu", "2", "임차권설정", amount=None),
            ],
            description="제2종근린생활시설(사무소)",
        )

        items = build_registry_attention_items(_contract(), registry)

        self.assertTrue(
            {
                "attention_seizure",
                "attention_auction",
                "attention_provisional_registration",
                "attention_lease_registration",
                "attention_commercial_use",
            }
            <= _ids(items)
        )

    def test_all_attention_items_ask_for_confirmation_without_verdict(self) -> None:
        registry = _registry(
            [
                _entry("gapgu", "2", "가압류"),
                _entry("gapgu", "3", "임의경매개시결정"),
                _entry("gapgu", "4", "소유권이전청구권가등기"),
                _entry("eulgu", "1", "근저당권설정", amount=150000000),
                _entry("eulgu", "2", "임차권설정"),
            ],
            description="제2종근린생활시설(사무소)",
        )

        items = build_registry_attention_items(_contract(), registry)

        self.assertTrue(items)
        for item in items:
            self.assertEqual(item.status, ReviewItemStatus.NEEDS_INPUT)
            self.assertIn(ATTENTION_DISCLAIMER, item.message)
            for word in FORBIDDEN_WORDS:
                self.assertNotIn(word, item.message, item.item_id)

    def test_cancelled_entries_are_not_reported(self) -> None:
        registry = _registry([_entry("gapgu", "2", "가압류", status="cancelled")])

        items = build_registry_attention_items(_contract(), registry)

        self.assertNotIn("attention_seizure", _ids(items))

    def test_mortgage_shows_maximum_claim_next_to_deposit(self) -> None:
        registry = _registry(
            [_entry("eulgu", "1", "근저당권설정", amount=150000000)]
        )

        items = build_registry_attention_items(_contract(), registry)

        mortgage = _by_id(items, "attention_mortgage_1")
        self.assertIn("150,000,000원", mortgage.message)
        self.assertIn("200,000,000원", mortgage.message)

    def test_mortgage_without_restriction_term_suggests_clause(self) -> None:
        registry = _registry(
            [_entry("eulgu", "1", "근저당권설정", amount=150000000)]
        )

        items = build_registry_attention_items(_contract(), registry)

        self.assertIn("suggest_rights_change_restriction", _ids(items))
        self.assertIn("mortgage_without_restriction_term", _ids(items))

    def test_mortgage_with_restriction_term_skips_cross_notice(self) -> None:
        registry = _registry(
            [_entry("eulgu", "1", "근저당권설정", amount=150000000)]
        )
        contract = _contract(
            special_terms=[
                "임대인은 잔금 지급 다음 날까지 저당권 설정 등 권리 변동을 일으키지 않는다."
            ]
        )

        items = build_registry_attention_items(contract, registry)

        self.assertNotIn("mortgage_without_restriction_term", _ids(items))

    def test_lease_always_gets_clause_suggestions_and_tax_notice(self) -> None:
        items = build_registry_attention_items(_contract(), _registry())

        self.assertTrue(
            {
                "suggest_rights_change_restriction",
                "suggest_guarantee_insurance",
                "tax_arrears_check",
            }
            <= _ids(items)
        )
        restriction = _by_id(items, "suggest_rights_change_restriction")
        self.assertIn("계약을 해제할 수 있고", restriction.message)
        self.assertIn("계약금, 보증금 등 일체의 금액", restriction.message)

    def test_sale_contract_skips_lease_only_suggestions(self) -> None:
        items = build_registry_attention_items(
            _contract(contract_type="sale"), _registry()
        )

        self.assertNotIn("suggest_guarantee_insurance", _ids(items))
        self.assertNotIn("suggest_rights_change_restriction", _ids(items))

    def test_commercial_use_with_residential_lease_asks_use_check(self) -> None:
        registry = _registry(description="제2종근린생활시설(사무소)")

        items = build_registry_attention_items(_contract(), registry)

        self.assertIn("commercial_use_with_residential_lease", _ids(items))
        commercial = _by_id(items, "attention_commercial_use")
        self.assertIn("HUG", commercial.message)

    def test_graph_precheck_includes_attention_items(self) -> None:
        registry = _registry([_entry("gapgu", "2", "가압류")])

        result = ContractReviewGraph().prepare(_contract(), registry)

        self.assertIn("attention_seizure", _ids(result.items))


if __name__ == "__main__":
    unittest.main()
