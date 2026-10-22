# validators.py
"""
Description: 생성된 내용증명의 당사자, 사실, 법령 인용과 안전 표현을 검증한다.
모델 판단 전에 결정적으로 확인할 수 있는 오류를 수집하여 한 차례 수정에 사용한다.
Author: choeminju
Date: 2026-10-22
Before:
    - 생성 결과가 입력 사실과 검색 법령을 지키는지 자동 확인할 방법이 없는 상태.
After:
    - 지원되지 않은 인용과 사실 왜곡을 결과 반환 전에 차단할 수 있음.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Sequence

from ml.agent.certified_letter.state import (
    LetterDraftData,
    NormalizedCertifiedLetterRequest,
    StatuteEvidenceData,
    ValidationIssue,
)


LAW_CITATION_PATTERN = re.compile(
    r"「(?P<law>[^」]+)」\s*(?P<article>제\s*\d+\s*조)"
)
WON_PATTERN = re.compile(r"(?<!\d)(\d[\d,]*)\s*원")
ISO_DATE_PATTERN = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
KOREAN_DATE_PATTERN = re.compile(
    r"\b(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"
)
PRECEDENT_MARKERS = ("판례", "사건번호", "대법원", "고등법원", "지방법원")
UNSAFE_CLAIMS = (
    "법적 효력이 보장",
    "승소가 보장",
    "반드시 승소",
    "형사처벌이 확정",
    "반드시 처벌",
)
PROMPT_LEAK_MARKERS = ("system prompt", "시스템 프롬프트", "statute_evidence")


class CertifiedLetterDraftValidator:
    def validate(
        self,
        request: NormalizedCertifiedLetterRequest,
        statute_evidence: Sequence[StatuteEvidenceData],
        draft: LetterDraftData,
    ) -> list[ValidationIssue]:
        issues = []
        issues.extend(_validate_parties(request, draft))
        issues.extend(_validate_citations(statute_evidence, draft.body))
        issues.extend(_validate_money(request, draft.body))
        issues.extend(_validate_dates(request, draft.body))
        issues.extend(_validate_deadline(request, draft.body))
        issues.extend(_validate_safety(draft))
        return _deduplicate_issues(issues)


def _validate_parties(
    request: NormalizedCertifiedLetterRequest,
    draft: LetterDraftData,
) -> list[ValidationIssue]:
    expected_sender = request.contract.lessee
    expected_recipient = request.contract.lessor
    issues = []
    if (
        draft.sender.name != expected_sender.name
        or draft.sender.address != expected_sender.address
        or draft.sender.detail_address != expected_sender.detail_address
    ):
        issues.append(
            ValidationIssue(
                code="sender_mismatch",
                message="발신인 정보가 임차인 송달 정보와 일치하지 않습니다.",
            )
        )
    if (
        draft.recipient.name != expected_recipient.name
        or draft.recipient.address != expected_recipient.address
        or draft.recipient.detail_address != expected_recipient.detail_address
    ):
        issues.append(
            ValidationIssue(
                code="recipient_mismatch",
                message="수신인 정보가 임대인 송달 정보와 일치하지 않습니다.",
            )
        )
    return issues


def _validate_citations(
    statute_evidence: Sequence[StatuteEvidenceData],
    body: str,
) -> list[ValidationIssue]:
    allowed = {
        (_normalize_law_name(item.law_name), _normalize_article(item.article_label))
        for item in statute_evidence
    }
    citations = {
        (_normalize_law_name(match.group("law")), _normalize_article(match.group("article")))
        for match in LAW_CITATION_PATTERN.finditer(body)
    }
    issues = []
    if not citations:
        issues.append(
            ValidationIssue(
                code="missing_statute_citation",
                message="본문에 검색 근거와 연결되는 법령 인용이 없습니다.",
            )
        )
        return issues
    unsupported = citations - allowed
    if unsupported:
        formatted = ", ".join(f"{law} {article}" for law, article in unsupported)
        issues.append(
            ValidationIssue(
                code="unsupported_statute_citation",
                message=f"검색 근거에 없는 법령 인용이 있습니다: {formatted}",
            )
        )
    return issues


def _validate_money(
    request: NormalizedCertifiedLetterRequest,
    body: str,
) -> list[ValidationIssue]:
    allowed = {
        request.contract.payment.deposit,
        *_extract_won_values(f"{request.purpose} {request.facts}"),
    }
    if request.contract.payment.monthly_rent is not None:
        allowed.add(request.contract.payment.monthly_rent)
    unexpected = sorted(_extract_won_values(body) - allowed)
    if not unexpected:
        return []
    return [
        ValidationIssue(
            code="unexpected_money",
            message=(
                "입력에서 확인되지 않은 금액이 본문에 있습니다: "
                + ", ".join(f"{value:,}원" for value in unexpected)
            ),
        )
    ]


def _validate_dates(
    request: NormalizedCertifiedLetterRequest,
    body: str,
) -> list[ValidationIssue]:
    allowed = {
        request.contract.period.start_date,
        request.contract.period.end_date,
        *_extract_dates(f"{request.purpose} {request.facts}"),
    }
    if request.requested_deadline is not None:
        allowed.add(request.requested_deadline)
    unexpected = sorted(_extract_dates(body) - allowed)
    if not unexpected:
        return []
    return [
        ValidationIssue(
            code="unexpected_date",
            message=(
                "입력에서 확인되지 않은 날짜가 본문에 있습니다: "
                + ", ".join(value.isoformat() for value in unexpected)
            ),
        )
    ]


def _validate_deadline(
    request: NormalizedCertifiedLetterRequest,
    body: str,
) -> list[ValidationIssue]:
    if request.is_deadline_suggested:
        has_deadline = "7일 이내" in body and "받" in body
    else:
        deadline = request.requested_deadline
        assert deadline is not None
        has_deadline = (
            deadline.isoformat() in body
            or f"{deadline.year}년 {deadline.month}월 {deadline.day}일" in body
        )
    if has_deadline:
        return []
    return [
        ValidationIssue(
            code="missing_deadline",
            message="본문에 승인된 이행 기한이 포함되지 않았습니다.",
        )
    ]


def _validate_safety(draft: LetterDraftData) -> list[ValidationIssue]:
    combined = " ".join(
        (
            draft.title,
            draft.body,
            draft.strategy_summary,
            *draft.follow_up_actions,
            *draft.warnings,
        )
    )
    issues = []
    if any(marker in draft.body for marker in PRECEDENT_MARKERS):
        issues.append(
            ValidationIssue(
                code="precedent_in_body",
                message="내용증명 본문에 판례 또는 법원 사건 표현이 포함되어 있습니다.",
            )
        )
    if any(claim in combined for claim in UNSAFE_CLAIMS):
        issues.append(
            ValidationIssue(
                code="unsupported_legal_claim",
                message="승소, 처벌 또는 법적 효력을 보장하는 표현이 있습니다.",
            )
        )
    if not any("초안" in warning for warning in draft.warnings):
        issues.append(
            ValidationIssue(
                code="missing_draft_warning",
                message="법률 검토 전 초안이라는 경고가 없습니다.",
            )
        )
    if any(marker in combined.lower() for marker in PROMPT_LEAK_MARKERS):
        issues.append(
            ValidationIssue(
                code="internal_prompt_exposure",
                message="내부 프롬프트 또는 필드명이 출력에 노출되었습니다.",
                is_repairable=False,
            )
        )
    return issues


def _normalize_law_name(value: str) -> str:
    return "".join(value.split()).replace("ㆍ", "·")


def _normalize_article(value: str) -> str:
    return "".join(value.split())


def _extract_won_values(value: str) -> set[int]:
    return {int(match.replace(",", "")) for match in WON_PATTERN.findall(value)}


def _extract_dates(value: str) -> set[date]:
    values = set()
    for pattern in (ISO_DATE_PATTERN, KOREAN_DATE_PATTERN):
        for year, month, day in pattern.findall(value):
            try:
                values.add(date(int(year), int(month), int(day)))
            except ValueError:
                continue
    return values


def _deduplicate_issues(issues: Sequence[ValidationIssue]) -> list[ValidationIssue]:
    seen = set()
    result = []
    for issue in issues:
        if issue.code in seen:
            continue
        seen.add(issue.code)
        result.append(issue)
    return result
