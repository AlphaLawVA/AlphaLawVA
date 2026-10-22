# test_certified_letter_api_models.py
"""
Description: 내용증명 생성 API의 계약 입력과 비동기 작업 상태를 검증한다.
잘못된 계약 조합과 일관되지 않은 작업 결과가 공개 I/O에 들어오지 못하게 한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 내용증명 생성 API의 공개 스키마만 정의된 상태.
After:
    - 정상 요청과 주요 실패·상태 조합을 자동 테스트로 검증할 수 있음.
"""

import unittest
from datetime import datetime, timezone
from uuid import UUID

from pydantic import ValidationError

from api.models.certified_letters import (
    CertifiedLetterDocument,
    CertifiedLetterDraftAccepted,
    CertifiedLetterDraftJobResponse,
    CertifiedLetterDraftRequest,
    CertifiedLetterDraftResult,
    CertifiedLetterJobError,
    CertifiedLetterJobStatus,
    CertifiedLetterOutcome,
    CertifiedLetterParty,
    FollowUpAction,
    SimilarPrecedent,
    StatuteEvidence,
)


DRAFT_ID = UUID("12345678-1234-5678-1234-567812345678")
CREATED_AT = datetime(2026, 10, 22, 5, 30, tzinfo=timezone.utc)
COMPLETED_AT = datetime(2026, 10, 22, 5, 31, tzinfo=timezone.utc)


def make_request_data(*, contract_type: str = "jeonse") -> dict:
    monthly_rent = None if contract_type == "jeonse" else 800000
    return {
        "purpose": "  계약 종료 후 보증금을 반환받고 싶습니다.  ",
        "facts": (
            "  계약이 종료되었고 임대인에게 보증금 반환을 요청했지만 "
            "아직 받지 못했습니다.  "
        ),
        "requested_deadline": "2026-11-15",
        "contract": {
            "contract_type": contract_type,
            "property": {
                "address": "서울특별시 중구 세종대로 1",
                "detail_address": "101동 101호",
                "building_type": "apartment",
            },
            "period": {
                "start_date": "2024-10-01",
                "end_date": "2026-09-30",
            },
            "payment": {
                "deposit": 100_000_000,
                "monthly_rent": monthly_rent,
            },
            "lessor": {
                "name": "홍길동",
                "address": "서울특별시 종로구 사직로 1",
            },
            "lessee": {
                "name": "김민지",
                "address": "서울특별시 중구 세종대로 1",
                "detail_address": "101동 101호",
            },
        },
    }


def make_result() -> CertifiedLetterDraftResult:
    return CertifiedLetterDraftResult(
        document=CertifiedLetterDocument(
            title="임대차보증금 반환 촉구서",
            sender=CertifiedLetterParty(
                name="김민지",
                address="서울특별시 중구 세종대로 1",
                detail_address="101동 101호",
            ),
            recipient=CertifiedLetterParty(
                name="홍길동",
                address="서울특별시 종로구 사직로 1",
            ),
            body="계약 종료에 따라 임대차보증금 반환을 요청합니다.",
        ),
        strategy_summary="보증금 반환 의무와 이행 기한을 명확히 통지합니다.",
        follow_up_actions=[
            FollowUpAction(
                order=1,
                action="발송 전 사실관계와 주소를 다시 확인합니다.",
            )
        ],
        statute_evidence=[
            StatuteEvidence(
                statute_id="001234",
                law_name="주택임대차보호법",
                article_label="제4조",
                content="임대차기간 등에 관한 조문 원문",
                explanation="임대차 종료와 보증금 반환 검토에 참고되는 조문입니다.",
                source_url="https://law.go.kr/example",
            )
        ],
        similar_precedents=[
            SimilarPrecedent(
                precedent_id="64441",
                case_number="2006다62492",
                case_name="임대차보증금등",
                court_name="대법원",
                decision_date="2008-09-25",
                summary="임대차 종료와 보증금 반환이 문제 된 판례입니다.",
                source_url=(
                    "https://law.go.kr/LSW/precInfoP.do?mode=0&precSeq=64441"
                ),
            )
        ],
        warnings=["이 문서는 법률 검토 전 초안입니다."],
    )


class CertifiedLetterDraftRequestTests(unittest.TestCase):
    def test_accepts_and_normalizes_jeonse_request(self):
        request = CertifiedLetterDraftRequest.model_validate(make_request_data())

        self.assertEqual(
            request.purpose,
            "계약 종료 후 보증금을 반환받고 싶습니다.",
        )
        self.assertEqual(request.contract.payment.deposit, 100_000_000)
        self.assertIsNone(request.contract.payment.monthly_rent)
        self.assertEqual(request.requested_deadline.isoformat(), "2026-11-15")

    def test_accepts_monthly_rent_contract_with_positive_rent(self):
        request = CertifiedLetterDraftRequest.model_validate(
            make_request_data(contract_type="monthly_rent")
        )

        self.assertEqual(request.contract.payment.monthly_rent, 800_000)

    def test_rejects_unknown_fields(self):
        payload = make_request_data()
        payload["user_id"] = "legacy-user"

        with self.assertRaises(ValidationError):
            CertifiedLetterDraftRequest.model_validate(payload)

    def test_rejects_invalid_contract_period(self):
        payload = make_request_data()
        payload["contract"]["period"] = {
            "start_date": "2026-09-30",
            "end_date": "2026-09-30",
        }

        with self.assertRaises(ValidationError):
            CertifiedLetterDraftRequest.model_validate(payload)

    def test_rejects_monthly_rent_on_jeonse_contract(self):
        payload = make_request_data()
        payload["contract"]["payment"]["monthly_rent"] = 800_000

        with self.assertRaises(ValidationError):
            CertifiedLetterDraftRequest.model_validate(payload)

    def test_rejects_missing_or_zero_monthly_rent(self):
        for rent in (None, 0):
            with self.subTest(rent=rent):
                payload = make_request_data(contract_type="monthly_rent")
                payload["contract"]["payment"]["monthly_rent"] = rent
                with self.assertRaises(ValidationError):
                    CertifiedLetterDraftRequest.model_validate(payload)

    def test_rejects_negative_money_and_short_facts(self):
        payload = make_request_data()
        payload["contract"]["payment"]["deposit"] = -1
        with self.assertRaises(ValidationError):
            CertifiedLetterDraftRequest.model_validate(payload)

        payload = make_request_data()
        payload["facts"] = "너무 짧음"
        with self.assertRaises(ValidationError):
            CertifiedLetterDraftRequest.model_validate(payload)


class CertifiedLetterJobResponseTests(unittest.TestCase):
    def test_accepts_queued_submission_with_utc_timestamp(self):
        accepted = CertifiedLetterDraftAccepted(
            draft_id=DRAFT_ID,
            created_at=CREATED_AT,
        )

        self.assertEqual(accepted.status, CertifiedLetterJobStatus.QUEUED)
        self.assertEqual(
            accepted.model_dump(mode="json")["created_at"],
            "2026-10-22T05:30:00Z",
        )

    def test_rejects_non_utc_submission_and_allows_existing_job_status(self):
        with self.assertRaises(ValidationError):
            CertifiedLetterDraftAccepted(
                draft_id=DRAFT_ID,
                created_at=datetime(2026, 10, 22, 5, 30),
            )
        existing = CertifiedLetterDraftAccepted(
            draft_id=DRAFT_ID,
            status=CertifiedLetterJobStatus.COMPLETED,
            created_at=CREATED_AT,
        )
        self.assertEqual(existing.status, CertifiedLetterJobStatus.COMPLETED)

    def test_accepts_completed_draft(self):
        response = CertifiedLetterDraftJobResponse(
            draft_id=DRAFT_ID,
            status=CertifiedLetterJobStatus.COMPLETED,
            outcome=CertifiedLetterOutcome.DRAFT_READY,
            result=make_result(),
            created_at=CREATED_AT,
            updated_at=COMPLETED_AT,
            completed_at=COMPLETED_AT,
        )

        self.assertEqual(response.result.similar_precedents[0].precedent_id, "64441")
        self.assertEqual(response.result.statute_evidence[0].statute_id, "001234")

    def test_accepts_needs_information_without_draft(self):
        response = CertifiedLetterDraftJobResponse(
            draft_id=DRAFT_ID,
            status=CertifiedLetterJobStatus.COMPLETED,
            outcome=CertifiedLetterOutcome.NEEDS_INFORMATION,
            missing_information=["임대인의 송달 주소"],
            message="내용증명을 작성하려면 추가 정보가 필요합니다.",
            created_at=CREATED_AT,
            updated_at=COMPLETED_AT,
            completed_at=COMPLETED_AT,
        )

        self.assertIsNone(response.result)

    def test_accepts_failed_job_with_safe_error(self):
        response = CertifiedLetterDraftJobResponse(
            draft_id=DRAFT_ID,
            status=CertifiedLetterJobStatus.FAILED,
            error=CertifiedLetterJobError(
                code="generation_service_unavailable",
                message="내용증명 생성 서비스를 사용할 수 없습니다.",
            ),
            created_at=CREATED_AT,
            updated_at=COMPLETED_AT,
            completed_at=COMPLETED_AT,
        )

        self.assertIsNone(response.outcome)

    def test_rejects_inconsistent_job_states(self):
        invalid_payloads = [
            {
                "status": CertifiedLetterJobStatus.PROCESSING,
                "result": make_result(),
            },
            {
                "status": CertifiedLetterJobStatus.COMPLETED,
                "outcome": CertifiedLetterOutcome.DRAFT_READY,
                "completed_at": COMPLETED_AT,
            },
            {
                "status": CertifiedLetterJobStatus.COMPLETED,
                "outcome": CertifiedLetterOutcome.NEEDS_INFORMATION,
                "message": "추가 정보가 필요합니다.",
                "completed_at": COMPLETED_AT,
            },
            {
                "status": CertifiedLetterJobStatus.FAILED,
                "completed_at": COMPLETED_AT,
            },
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    CertifiedLetterDraftJobResponse(
                        draft_id=DRAFT_ID,
                        created_at=CREATED_AT,
                        updated_at=COMPLETED_AT,
                        **payload,
                    )

    def test_rejects_non_sequential_follow_up_actions(self):
        payload = make_result().model_dump()
        payload["follow_up_actions"][0]["order"] = 2

        with self.assertRaises(ValidationError):
            CertifiedLetterDraftResult.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
