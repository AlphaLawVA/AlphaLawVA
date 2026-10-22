"""가짜 외부 의존성으로 내용증명 API와 실제 LangGraph 전체 경로를 검증한다."""

import json
import unittest

from fastapi.testclient import TestClient

from api.main import create_app
from api.services.certified_letter_service import CertifiedLetterService
from ml.agent.certified_letter.factory import create_certified_letter_workflow
from ml.agent.certified_letter.state import (
    CertifiedLetterScopeDecision,
    DraftPartyData,
    LetterDraftData,
    PrecedentEvidenceData,
    ScopeClassification,
    StatuteEvidenceData,
)


def make_payload():
    return {
        "purpose": "계약 종료 후 보증금을 반환받고 싶습니다.",
        "facts": "계약이 종료되었지만 보증금 1억원을 아직 반환받지 못했습니다.",
        "contract": {
            "contract_type": "jeonse",
            "property": {"address": "서울특별시 중구 세종대로 1"},
            "period": {
                "start_date": "2024-10-01",
                "end_date": "2026-09-30",
            },
            "payment": {"deposit": 100000000, "monthly_rent": None},
            "lessor": {
                "name": "홍길동",
                "address": "서울특별시 종로구 사직로 1",
            },
            "lessee": {
                "name": "김민지",
                "address": "서울특별시 중구 세종대로 1",
            },
        },
    }


class InScopeClassifier:
    def classify_scope(self, _purpose, _facts):
        return ScopeClassification(CertifiedLetterScopeDecision.IN_SCOPE)


class FixedStatuteSearch:
    evidence = StatuteEvidenceData(
        chunk_id="statute:001:article:4",
        text="임대차 기간과 보증금 반환에 관한 조문",
        similarity=0.91,
        statute_id="001",
        law_name="주택임대차보호법",
        article_label="제4조",
        source_url="https://law.go.kr/법령/주택임대차보호법",
    )

    def search(self, _queries, *, candidate_limit):
        self.candidate_limit = candidate_limit
        return [self.evidence]


class FixedPrecedentSearch:
    evidence = PrecedentEvidenceData(
        precedent_id="64441",
        text="임대차 종료와 보증금 반환이 문제 된 판례",
        similarity=0.88,
        case_number="2006다62492",
        case_name="임대차보증금등",
        court_name="대법원",
        decision_date="2008-09-25",
        summary="보증금 반환과 관련된 유사 판례입니다.",
    )

    def search(self, _queries, *, candidate_limit):
        self.candidate_limit = candidate_limit
        return [self.evidence]


class PassThroughReranker:
    def rerank(self, _query, candidates, *, top_k):
        return list(candidates)[:top_k]


class RecordingStructuredModel:
    def __init__(self):
        self.payloads = []

    def invoke(self, prompt):
        payload = json.loads(prompt.user)
        self.payloads.append(payload)
        contract = payload["contract"]
        return LetterDraftData(
            title="임대차보증금 반환 촉구서",
            sender=DraftPartyData(
                name=contract["lessee"]["name"],
                address=contract["lessee"]["address"],
                detail_address=contract["lessee"]["detail_address"],
            ),
            recipient=DraftPartyData(
                name=contract["lessor"]["name"],
                address=contract["lessor"]["address"],
                detail_address=contract["lessor"]["detail_address"],
            ),
            body=(
                "「주택임대차보호법」 제4조를 근거로 보증금 "
                "100,000,000원을 이 내용증명을 받은 날부터 7일 이내 "
                "반환하여 주시기 바랍니다."
            ),
            strategy_summary="계약 종료와 보증금 반환 요구를 명확히 통지합니다.",
            follow_up_actions=("발송 전 계약 정보와 주소를 확인합니다.",),
            warnings=("이 문서는 법률 검토 전 초안입니다.",),
        )


class CertifiedLetterEndToEndTests(unittest.TestCase):
    def make_client(self):
        self.model = RecordingStructuredModel()
        graph = create_certified_letter_workflow(
            model=self.model,
            scope_classifier=InScopeClassifier(),
            statute_search=FixedStatuteSearch(),
            precedent_search=FixedPrecedentSearch(),
            statute_reranker=PassThroughReranker(),
            precedent_reranker=PassThroughReranker(),
        )
        service = CertifiedLetterService(graph=graph)
        return TestClient(
            create_app(
                precedent_search_service=object(),
                certified_letter_service=service,
            )
        )

    def test_api_runs_compiled_graph_and_separates_precedent_from_draft_prompt(self):
        client = self.make_client()

        accepted = client.post(
            "/api/v1/certified-letter-drafts",
            headers={"Idempotency-Key": "end-to-end-1"},
            json=make_payload(),
        )
        result = client.get(
            f"/api/v1/certified-letter-drafts/{accepted.json()['draft_id']}"
        )

        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(accepted.json()["status"], "queued")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "completed")
        self.assertEqual(result.json()["outcome"], "draft_ready")
        self.assertEqual(
            result.json()["result"]["statute_evidence"][0]["law_name"],
            "주택임대차보호법",
        )
        self.assertEqual(
            result.json()["result"]["similar_precedents"][0]["precedent_id"],
            "64441",
        )
        self.assertNotIn("2006다62492", result.json()["result"]["document"]["body"])
        self.assertEqual(len(self.model.payloads), 1)
        self.assertIn("statute_evidence", self.model.payloads[0])
        self.assertNotIn("precedent", json.dumps(self.model.payloads[0]))

    def test_openapi_exposes_async_request_and_job_response_contracts(self):
        schema = self.make_client().get("/openapi.json").json()
        create_operation = schema["paths"]["/api/v1/certified-letter-drafts"][
            "post"
        ]
        get_operation = schema["paths"][
            "/api/v1/certified-letter-drafts/{draft_id}"
        ]["get"]

        self.assertIn("202", create_operation["responses"])
        self.assertIn("409", create_operation["responses"])
        self.assertIn("422", create_operation["responses"])
        self.assertIn("503", create_operation["responses"])
        self.assertIn("200", get_operation["responses"])
        self.assertIn("404", get_operation["responses"])
        self.assertIn(
            "CertifiedLetterDraftRequest",
            schema["components"]["schemas"],
        )
        self.assertIn(
            "CertifiedLetterDraftJobResponse",
            schema["components"]["schemas"],
        )


if __name__ == "__main__":
    unittest.main()
