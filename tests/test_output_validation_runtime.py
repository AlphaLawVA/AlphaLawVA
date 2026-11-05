"""
Description: 서비스 실행 중 출력 검증이 문제 표현을 찾아 재작성 안내 문구로 바꾸는지 검증한다.
평가 하네스와 같은 규칙을 쓰되 실행 경로에서는 실패 등급 규칙만 차단 대상으로 삼는다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 출력 규칙이 평가 하네스에만 있어 서비스 실행 중에는 검증되지 않던 상태.
After:
    - 차단 대상 규칙의 이슈와 재작성용 안내 문구를 회귀 검증할 수 있음.
"""

import unittest

from ml.agent.output_validation import find_output_issues

EVIDENCE = {"law:1": "조문: 제551조(해지, 해제와 손해배상)\n해지 또는 해제는 손해배상의 청구에 영향을 미치지 아니한다."}
QUERY = "jeonse 계약 특약 검토: 임차인이 계약을 해제하면 보증금의 20%를 위약금으로 지급한다."


def _review(**overrides):
    item = {
        "summary": "위약금 비율을 확인하세요.",
        "review_status": "check",
        "revision": "임차인이 계약을 해제하면 보증금의 20%를 위약금으로 지급한다.",
        "negotiation": "위약금 비율과 지급 시기를 협의하세요.",
        "evidence_source_ids": ["law:1"],
    }
    item.update(overrides)
    return item


def _issues(item, kind="review", category="special_term"):
    return find_output_issues(
        kind=kind,
        item_id="term-1",
        query=QUERY,
        category=category,
        evidence=EVIDENCE,
        item=item,
    )


class RuntimeIssueTests(unittest.TestCase):
    def test_clean_output_has_no_issues(self) -> None:
        self.assertEqual(_issues(_review()), [])

    def test_blocking_violations_become_issues_with_feedback(self) -> None:
        cases = {
            "no_legal_conclusion": _review(summary="이 조항은 무효입니다."),
            "no_unsupported_article": _review(summary="민법 제999조를 확인하세요."),
            "no_unsupported_case_number": _review(summary="2006다62492 판결을 참고하세요."),
            "no_unsupported_figure": _review(revision="위약금은 300만원으로 한다."),
        }
        for check, item in cases.items():
            issues = _issues(item)
            self.assertEqual([i.check for i in issues], [check], check)
            self.assertTrue(issues[0].message)
            self.assertEqual(issues[0].item_id, "term-1")

    def test_warnings_do_not_block(self) -> None:
        item = _review(summary="위험할 수 있으니 통상 확인하세요.", negotiation="효력이 없을 수 있습니다.")
        self.assertEqual(_issues(item), [])

    def test_labeled_example_figure_does_not_block(self) -> None:
        item = _review(negotiation="예시: 위약금을 보증금의 10%로 협의해 볼 수 있습니다.")
        self.assertEqual(_issues(item), [])

    def test_placeholder_in_ready_clause_blocks(self) -> None:
        item = {
            "status": "draft_ready",
            "draft_text": "임대인은 ○○일까지 이행한다.",
            "title": "제목입니다",
            "reason": "이유입니다",
            "strategy": "전략입니다",
            "needs_input": [],
            "evidence_source_ids": ["law:1"],
        }
        issues = _issues(item, kind="clause", category="special_clause_request")
        self.assertEqual([i.check for i in issues], ["no_placeholder_in_ready"])

    def test_checks_already_enforced_by_models_are_not_repeated(self) -> None:
        # 근거 ID·길이·필수 항목은 생성기 검증이 이미 처리하므로 실행 검증은 보지 않는다.
        item = _review(evidence_source_ids=["invented"], revision="")
        self.assertEqual(_issues(item), [])


if __name__ == "__main__":
    unittest.main()
