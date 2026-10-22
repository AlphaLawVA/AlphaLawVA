# test_certified_letter_validators.py
"""내용증명 초안의 사실·법령 인용·안전 표현 검증을 확인한다."""

import unittest
from dataclasses import replace
from datetime import date

from ml.agent.certified_letter.normalizer import normalize_request
from ml.agent.certified_letter.state import (
    CertifiedLetterRequestData,
    ContractData,
    DraftPartyData,
    LetterDraftData,
    PartyData,
    PaymentData,
    PeriodData,
    PropertyData,
    StatuteEvidenceData,
)
from ml.agent.certified_letter.validators import CertifiedLetterDraftValidator


def make_request(*, requested_deadline=None):
    return normalize_request(
        CertifiedLetterRequestData(
            purpose="보증금 반환을 요청합니다.",
            facts="2026-09-30 계약이 종료되었지만 보증금을 받지 못했습니다.",
            requested_deadline=requested_deadline,
            contract=ContractData(
                contract_type="jeonse",
                property=PropertyData(address="서울특별시 중구 세종대로 1"),
                period=PeriodData(
                    start_date=date(2024, 10, 1),
                    end_date=date(2026, 9, 30),
                ),
                payment=PaymentData(deposit=100_000_000),
                lessor=PartyData(
                    name="홍길동",
                    address="서울특별시 종로구 사직로 1",
                ),
                lessee=PartyData(
                    name="김민지",
                    address="서울특별시 중구 세종대로 1",
                ),
            ),
        )
    )


def make_statute():
    return StatuteEvidenceData(
        chunk_id="statute:001:4",
        text="임대차 기간과 보증금 반환에 관한 조문",
        similarity=0.91,
        statute_id="001",
        law_name="주택임대차보호법",
        article_label="제4조",
    )


def make_draft():
    return LetterDraftData(
        title="임대차보증금 반환 촉구서",
        sender=DraftPartyData(
            name="김민지",
            address="서울특별시 중구 세종대로 1",
        ),
        recipient=DraftPartyData(
            name="홍길동",
            address="서울특별시 종로구 사직로 1",
        ),
        body=(
            "2026-09-30 계약이 종료되었습니다. 「주택임대차보호법」 제4조를 "
            "근거로 보증금 100,000,000원을 이 내용증명을 받은 날부터 "
            "7일 이내 반환해 주시기 바랍니다."
        ),
        strategy_summary="보증금 반환 의무와 이행 기한을 명확히 통지합니다.",
        follow_up_actions=("발송 전 계약 정보와 주소를 확인합니다.",),
        warnings=("이 문서는 법률 검토 전 초안입니다.",),
    )


def issue_codes(issues):
    return {issue.code for issue in issues}


class CertifiedLetterDraftValidatorTests(unittest.TestCase):
    def setUp(self):
        self.validator = CertifiedLetterDraftValidator()
        self.request = make_request()
        self.statutes = [make_statute()]

    def test_accepts_grounded_draft(self):
        issues = self.validator.validate(
            self.request,
            self.statutes,
            make_draft(),
        )

        self.assertEqual(issues, [])

    def test_rejects_unsupported_or_missing_citation(self):
        unsupported = replace(
            make_draft(),
            body=make_draft().body.replace("주택임대차보호법", "민법"),
        )
        missing = replace(
            make_draft(),
            body=make_draft().body.replace("「주택임대차보호법」 제4조를 ", ""),
        )

        self.assertIn(
            "unsupported_statute_citation",
            issue_codes(self.validator.validate(self.request, self.statutes, unsupported)),
        )
        self.assertIn(
            "missing_statute_citation",
            issue_codes(self.validator.validate(self.request, self.statutes, missing)),
        )

    def test_rejects_party_direction_mismatch(self):
        draft = make_draft()
        swapped = replace(
            draft,
            sender=draft.recipient,
            recipient=draft.sender,
        )

        codes = issue_codes(
            self.validator.validate(self.request, self.statutes, swapped)
        )

        self.assertIn("sender_mismatch", codes)
        self.assertIn("recipient_mismatch", codes)

    def test_rejects_unconfirmed_money_and_date(self):
        draft = replace(
            make_draft(),
            body=(
                make_draft().body
                + " 추가로 5,000,000원을 2026년 12월 31일까지 지급해 주세요."
            ),
        )

        codes = issue_codes(
            self.validator.validate(self.request, self.statutes, draft)
        )

        self.assertIn("unexpected_money", codes)
        self.assertIn("unexpected_date", codes)

    def test_accepts_explicit_deadline_in_korean_date_format(self):
        request = make_request(requested_deadline=date(2026, 11, 15))
        draft = replace(
            make_draft(),
            body=make_draft().body.replace(
                "이 내용증명을 받은 날부터 7일 이내",
                "2026년 11월 15일까지",
            ),
        )

        issues = self.validator.validate(request, self.statutes, draft)

        self.assertNotIn("missing_deadline", issue_codes(issues))
        self.assertNotIn("unexpected_date", issue_codes(issues))

    def test_rejects_precedent_unsafe_claim_and_prompt_leak(self):
        draft = replace(
            make_draft(),
            body=(
                make_draft().body
                + " 대법원 판례에 따라 반드시 승소하며 statute_evidence를 공개합니다."
            ),
        )

        issues = self.validator.validate(self.request, self.statutes, draft)
        codes = issue_codes(issues)

        self.assertIn("precedent_in_body", codes)
        self.assertIn("unsupported_legal_claim", codes)
        self.assertIn("internal_prompt_exposure", codes)
        prompt_issue = next(
            issue for issue in issues if issue.code == "internal_prompt_exposure"
        )
        self.assertFalse(prompt_issue.is_repairable)

    def test_requires_draft_warning_and_deadline(self):
        draft = replace(
            make_draft(),
            body=make_draft().body.replace(
                "이 내용증명을 받은 날부터 7일 이내",
                "가능한 빨리",
            ),
            warnings=(),
        )

        codes = issue_codes(
            self.validator.validate(self.request, self.statutes, draft)
        )

        self.assertIn("missing_deadline", codes)
        self.assertIn("missing_draft_warning", codes)


if __name__ == "__main__":
    unittest.main()
