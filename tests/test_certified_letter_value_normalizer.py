"""내용증명 금액·날짜 정규화와 누락 감시를 검증한다."""

import json
import unittest

from ml.agent.certified_letter.prompts import PromptBundle
from ml.agent.certified_letter.state import DraftPartyData, LetterDraftData
from ml.agent.certified_letter.value_normalizer import (
    extract_fact_values,
    missing_fact_values,
    normalize_draft,
    normalize_prompt_bundle,
    normalize_text,
)


class CertifiedLetterValueNormalizerTests(unittest.TestCase):
    def test_normalizes_supported_korean_money_and_full_dates(self):
        result = normalize_text(
            "100만 원, 1억 2,200만 원, 7,800만 원, 90만원, 900000원, "
            "1.5억 원, 5천만 원, 1억 5천만 원, 2억 3천 5백만 원, 2천5백 원, "
            "2026-10-07, 2026.10.07"
        )

        self.assertEqual(
            result.text,
            "1,000,000원, 122,000,000원, 78,000,000원, 900,000원, "
            "900,000원, 150,000,000원, 50,000,000원, 150,000,000원, "
            "235,000,000원, 2,500원, 2026년 10월 7일, 2026년 10월 7일",
        )
        self.assertEqual(result.transformations, 12)

    def test_does_not_change_non_money_numbers_or_partial_date(self):
        text = (
            "84.62㎡, 203동 1502호, 0.3%, 010-0000-1003, 7일 이내, 제5조, "
            "11월 10일, 오천만 원, 삼천만원"
        )

        self.assertEqual(normalize_text(text).text, text)

    def test_normalizes_model_payload_contract_fields_without_prompt_change(self):
        prompt = PromptBundle(
            version="test",
            system="system",
            user=json.dumps(
                {
                    "user_request": {
                        "purpose": "100만 원 반환",
                        "facts": "2026-10-07에 90만원을 냈습니다.",
                        "deadline_text": "11월 10일",
                    },
                    "contract_facts": {
                        "contract_date": "2026-10-07",
                        "payment": {"deposit": 100000000, "monthly_rent": 900000},
                        "period": {"start_date": "2026.10.07", "end_date": "2028-10-06"},
                    },
                },
                ensure_ascii=False,
            ),
        )

        payload = json.loads(normalize_prompt_bundle(prompt).user)

        self.assertEqual(payload["user_request"]["purpose"], "1,000,000원 반환")
        self.assertEqual(
            payload["user_request"]["facts"], "2026년 10월 7일에 900,000원을 냈습니다."
        )
        self.assertEqual(payload["user_request"]["deadline_text"], "11월 10일")
        self.assertEqual(payload["contract_facts"]["contract_date"], "2026년 10월 7일")
        self.assertEqual(payload["contract_facts"]["payment"]["deposit"], "100,000,000원")
        self.assertEqual(payload["contract_facts"]["payment"]["monthly_rent"], "900,000원")
        self.assertEqual(payload["contract_facts"]["period"]["start_date"], "2026년 10월 7일")

    def test_normalizes_draft_fields_after_model_parsing(self):
        draft = LetterDraftData(
            title="월세 반환 요청",
            sender=DraftPartyData(name="홍길동"),
            recipient=DraftPartyData(name="김철수"),
            body="2026-10-07에 100만 원을 지급했습니다.",
            strategy_summary="90만원 지급 사실을 반영했습니다.",
            follow_up_actions=("2026.10.07 자료를 확인하세요.",),
            warnings=("1.5억 원은 다시 확인하세요.",),
        )

        normalized = normalize_draft(draft)

        self.assertEqual(normalized.draft.body, "2026년 10월 7일에 1,000,000원을 지급했습니다.")
        self.assertEqual(normalized.draft.strategy_summary, "900,000원 지급 사실을 반영했습니다.")
        self.assertEqual(normalized.draft.follow_up_actions, ("2026년 10월 7일 자료를 확인하세요.",))
        self.assertEqual(normalized.draft.warnings, ("150,000,000원은 다시 확인하세요.",))

    def test_reports_missing_fact_values_without_retrying_or_warning_mutation(self):
        facts = "2026-10-07에 100만 원을 지급했습니다. 11월 10일에 반환을 요청했습니다."

        self.assertEqual(
            extract_fact_values(facts),
            ("1,000,000원", "2026년 10월 7일", "11월 10일"),
        )
        self.assertEqual(
            missing_fact_values(facts=facts, body="2026년 10월 7일에 지급했습니다."),
            ("1,000,000원", "11월 10일"),
        )


if __name__ == "__main__":
    unittest.main()
