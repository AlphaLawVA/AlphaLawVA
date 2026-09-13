# test_create_statute_parent_child_chunks.py
"""
Description: 항 단위 자식과 조문 단위 부모 생성 및 연결 무결성을
외부 모델 호출 없이 검증한다.
Author: ooheunsu
Date: 2026-09-05
Before:
    - 부모-자식 법령 청크 생성 코드가 작성된 상태.
After:
    - 자식 생성, 조문 대체, 제외 및 연결 오류를 자동 검증 가능.
"""

import copy
import unittest

from ml.preprocessing.statutes.create_statute_parent_child_chunks import (
    create_law_parent_child_chunks,
    validate_parent_child_chunks,
)


def text(value: str | None) -> dict:
    return {"raw_text": value, "normalized_text": value}


DOCUMENT = {
    "law": {
        "law_id": "001248",
        "name": {"ko": "주택임대차보호법"},
        "effective_date": "20260102",
        "headings": [],
        "articles": [
            {
                "node_id": "law:001248:article:0003",
                "order": 3,
                "label": "제3조",
                "title": "대항력 등",
                "has_excluded_image": False,
                "effective_date": "20260102",
                "heading_path": [],
                "text": text("제3조(대항력 등)"),
                "paragraphs": [
                    {
                        "node_id": "law:001248:paragraph:0003:0001",
                        "order": 1,
                        "label": "①",
                        "text": text("① 주택을 인도받고 주민등록을 마친다."),
                        "subparagraphs": [
                            {
                                "order": 1,
                                "text": text("1. 전입신고를 포함한다."),
                                "items": [],
                            }
                        ],
                    },
                    {
                        "node_id": "law:001248:paragraph:0003:0002",
                        "order": 2,
                        "label": "②",
                        "text": text("② 양수인은 임대인 지위를 승계한다."),
                        "subparagraphs": [],
                    },
                ],
            }
        ],
    }
}


class CreateStatuteParentChildChunksTests(unittest.TestCase):
    def test_creates_paragraph_children_linked_to_article_parent(self):
        parents, children, exclusions = create_law_parent_child_chunks(DOCUMENT)

        self.assertEqual(len(parents), 1)
        self.assertEqual(len(children), 2)
        self.assertEqual(exclusions, [])
        self.assertEqual(children[0]["chunk_type"], "paragraph_child")
        self.assertEqual(
            children[0]["parent_article_id"],
            "law:001248:article:0003",
        )
        self.assertIn("1. 전입신고를 포함한다.", children[0]["retrieval_text"])
        self.assertNotIn("② 양수인은", children[0]["retrieval_text"])
        self.assertTrue(
            all(
                count == 0
                for count in validate_parent_child_chunks(
                    parents, children
                ).values()
            )
        )

    def test_uses_article_child_when_article_has_no_paragraph(self):
        document = copy.deepcopy(DOCUMENT)
        article = document["law"]["articles"][0]
        article["text"] = text("제3조(대항력 등) 조문 본문")
        article["paragraphs"] = []

        parents, children, exclusions = create_law_parent_child_chunks(document)

        self.assertEqual(len(parents), 1)
        self.assertEqual(children[0]["chunk_type"], "article_child")
        self.assertEqual(children[0]["source_node_id"], article["node_id"])
        self.assertEqual(exclusions, [])

    def test_excludes_article_without_searchable_text(self):
        document = copy.deepcopy(DOCUMENT)
        article = document["law"]["articles"][0]
        article["paragraphs"] = []

        parents, children, exclusions = create_law_parent_child_chunks(document)

        self.assertEqual(parents, [])
        self.assertEqual(children, [])
        self.assertEqual(exclusions[0]["parent_article_id"], article["node_id"])

    def test_rejects_missing_parent_link(self):
        parents, children, _ = create_law_parent_child_chunks(DOCUMENT)
        children[0]["parent_article_id"] = "missing"

        with self.assertRaisesRegex(ValueError, "무결성 검사 실패"):
            validate_parent_child_chunks(parents, children)


if __name__ == "__main__":
    unittest.main()
