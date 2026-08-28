# test_create_statute_article_chunks.py
"""
Description: 조문 단위 청크가 법령 계층과 검색 메타데이터를 빠짐없이
결합하고 중복 식별자를 차단하는지 테스트한다.
Author: ooheunsu
Date: 2026-08-25
Before:
    - 정규화 법령에서 조문 단위 청크를 생성하는 코드가 작성된 상태.

After:
    - 1차 청킹 규칙과 조·항·호·목 무결성을 자동 테스트로 확인 가능.
"""

import copy
import unittest

from ml.preprocessing.statutes.create_statute_article_chunks import (
    create_article_chunk,
    create_law_chunks,
    validate_all_chunks,
)


def text(value: str | None) -> dict:
    return {"raw_text": value, "normalized_text": value}


SAMPLE_DOCUMENT = {
    "source": {"raw_file": "sample.xml"},
    "law": {
        "law_id": "001248",
        "name": {"ko": "주택임대차보호법"},
        "effective_date": "20260102",
        "headings": [
            {
                "node_id": "law:001248:heading:0001",
                "order": 1,
                "title": "제2장 주택임대차의 효력",
            }
        ],
        "articles": [
            {
                "node_id": "law:001248:article:0003",
                "order": 3,
                "label": "제3조",
                "title": "대항력",
                "has_excluded_image": False,
                "effective_date": "20260102",
                "heading_path": ["law:001248:heading:0001"],
                "text": text("제3조(대항력)"),
                "paragraphs": [
                    {
                        "order": 1,
                        "text": text("① 임차인이 주택을 인도받는다."),
                        "subparagraphs": [
                            {
                                "order": 1,
                                "text": text("1. 주민등록을 마친다."),
                                "items": [
                                    {
                                        "order": 1,
                                        "text": text("가. 전입신고를 한다."),
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    },
}


class CreateStatuteArticleChunksTests(unittest.TestCase):
    def test_combines_one_article_into_one_ordered_chunk(self):
        article = SAMPLE_DOCUMENT["law"]["articles"][0]

        chunk = create_article_chunk(SAMPLE_DOCUMENT, article)

        self.assertEqual(chunk["chunk_id"], "law:001248:article:0003")
        self.assertEqual(chunk["chunk_type"], "article")
        self.assertEqual(
            chunk["metadata"]["heading_path"],
            ["제2장 주택임대차의 효력"],
        )
        retrieval_text = chunk["retrieval_text"]
        expected_values = (
            "제3조(대항력)",
            "① 임차인이 주택을 인도받는다.",
            "1. 주민등록을 마친다.",
            "가. 전입신고를 한다.",
        )
        positions = [retrieval_text.index(value) for value in expected_values]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(retrieval_text.count("제3조(대항력)"), 1)
        self.assertIn("본문:\n① 임차인이 주택을 인도받는다.", retrieval_text)

    def test_creates_exactly_one_chunk_per_article(self):
        chunks, counts, excluded_articles = create_law_chunks(SAMPLE_DOCUMENT)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(excluded_articles, [])
        self.assertEqual(
            counts,
            {
                "articles": 1,
                "paragraphs": 1,
                "subparagraphs": 1,
                "items": 1,
            },
        )

    def test_rejects_missing_heading_reference(self):
        article = dict(SAMPLE_DOCUMENT["law"]["articles"][0])
        article["heading_path"] = ["law:001248:heading:9999"]

        with self.assertRaisesRegex(ValueError, "존재하지 않는 노드"):
            create_article_chunk(SAMPLE_DOCUMENT, article)

    def test_rejects_duplicate_chunk_ids(self):
        chunk = create_article_chunk(
            SAMPLE_DOCUMENT,
            SAMPLE_DOCUMENT["law"]["articles"][0],
        )

        with self.assertRaisesRegex(ValueError, "중복 chunk_id"):
            validate_all_chunks([chunk, chunk], expected_chunks=2)

    def test_excludes_heading_only_article_from_search_chunks(self):
        document = copy.deepcopy(SAMPLE_DOCUMENT)
        article = document["law"]["articles"][0]
        article["text"] = text("제3조(대항력)")
        article["paragraphs"] = []

        chunks, counts, excluded_articles = create_law_chunks(document)

        self.assertEqual(chunks, [])
        self.assertEqual(counts["articles"], 1)
        self.assertEqual(
            excluded_articles[0]["source_node_id"],
            "law:001248:article:0003",
        )
        self.assertIn("이동 조문", excluded_articles[0]["reason"])


if __name__ == "__main__":
    unittest.main()
