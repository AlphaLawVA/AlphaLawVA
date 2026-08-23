# test_normalize_statute_xmls.py
"""
Description: 법령 XML 정규화 과정의 계층 보존, 원문 기호 보존,
노드 개수 및 법령 버전 비교 동작을 테스트한다.
Author: ooheunsu
Date: 2026-08-17
Before:
    - XML 정규화 코드가 있지만 구조 및 무결성 보장 여부가 확인되지 않은 상태.

After:
    - XML 정규화와 무결성 검증의 핵심 동작을 자동 테스트로 확인 가능.
"""

import json
import tempfile
import unittest
from pathlib import Path

from ml.preprocessing.statutes.normalize_statute_xmls import (
    NORMALIZED_STRUCTURE_KEYS,
    normalize_text,
    normalize_xml,
    normalized_counts,
    source_counts,
    validate_document,
)
from xml.etree import ElementTree


SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<법령 법령키="sample-law-key">
  <기본정보>
    <법령ID>001248</법령ID>
    <공포일자>20251001</공포일자>
    <공포번호>21065</공포번호>
    <언어>한글</언어>
    <법종구분 법종구분코드="A0002">법률</법종구분>
    <법령명_한글>주택임대차보호법</법령명_한글>
    <법령명약칭>주택임대차법</법령명약칭>
    <소관부처 소관부처코드="1270000">법무부</소관부처>
    <시행일자>20260102</시행일자>
    <제개정구분>타법개정</제개정구분>
  </기본정보>
  <조문>
    <조문단위 조문키="heading-1">
      <조문여부>전문</조문여부>
      <조문내용>제1장 총칙</조문내용>
    </조문단위>
    <조문단위 조문키="article-1">
      <조문번호>2</조문번호>
      <조문가지번호>2</조문가지번호>
      <조문여부>조문</조문여부>
      <조문제목>적용 범위</조문제목>
      <조문시행일자>20260102</조문시행일자>
      <조문내용>제2조의2(적용 범위) ※ 주거용 건물에 적용한다. &lt;img src="table"&gt;표 계산식&lt;/img&gt; 이후 문장.</조문내용>
      <항>
        <호>
          <호번호>1.</호번호>
          <호내용>1. ① 주택</호내용>
          <목><목번호>가.</목번호><목내용>가. 아파트</목내용></목>
        </호>
      </항>
    </조문단위>
  </조문>
  <부칙><부칙단위 부칙키="sup-1"><부칙공포일자>20251001</부칙공포일자><부칙공포번호>21065</부칙공포번호><부칙내용>부칙 ※ 시행일</부칙내용></부칙단위></부칙>
  <별표><별표단위 별표키="app-1"><별표번호>1</별표번호><별표가지번호>2</별표가지번호><별표제목>서식</별표제목><별표제목문자열>서식 제목</별표제목문자열><별표서식이미지파일링크>image-1</별표서식이미지파일링크><별표서식이미지파일링크>image-2</별표서식이미지파일링크><별표내용>별표 ① 내용</별표내용></별표단위></별표>
</법령>
"""


class NormalizeStatuteXmlsTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.xml_path = Path(self.temporary_directory.name) / "001248.xml"
        self.xml_path.write_text(SAMPLE_XML, encoding="utf-8")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_preserves_xml_hierarchy_and_labels(self):
        document = normalize_xml(self.xml_path)
        article = document["law"]["articles"][0]
        paragraph = article["paragraphs"][0]
        subparagraph = paragraph["subparagraphs"][0]
        item = subparagraph["items"][0]

        self.assertEqual(article["label"], "제2조의2")
        self.assertTrue(paragraph["container_only"])
        self.assertEqual(subparagraph["label"], "1.")
        self.assertEqual(item["label"], "가.")
        self.assertEqual(item["parent_node_id"], subparagraph["node_id"])
        self.assertEqual(article["heading_path"], ["law:001248:heading:0001"])

    def test_preserves_meaningful_symbols_and_raw_text(self):
        document = normalize_xml(self.xml_path)
        article_text = document["law"]["articles"][0]["text"]
        subparagraph_text = document["law"]["articles"][0]["paragraphs"][0][
            "subparagraphs"
        ][0]["text"]

        self.assertIn("※", article_text["raw_text"])
        self.assertIn("※", article_text["normalized_text"])
        self.assertIn("①", subparagraph_text["raw_text"])
        self.assertEqual(normalize_text("  ①  내용  "), "① 내용")

    def test_excludes_image_blocks_and_supplementary_collections(self):
        document = normalize_xml(self.xml_path)
        article = document["law"]["articles"][0]

        self.assertTrue(article["has_excluded_image"])
        self.assertIn("<img", article["text"]["raw_text"])
        self.assertNotIn("<img", article["text"]["normalized_text"])
        self.assertNotIn("표 계산식", article["text"]["normalized_text"])
        self.assertIn("이후 문장", article["text"]["normalized_text"])
        self.assertNotIn("supplementary_provisions", document)
        self.assertNotIn("appendices", document)

    def test_structure_counts_match_before_and_after(self):
        document = normalize_xml(self.xml_path)
        root = ElementTree.parse(self.xml_path).getroot()

        source = source_counts(root)
        normalized = normalized_counts(document)

        self.assertEqual(
            {key: source[key] for key in NORMALIZED_STRUCTURE_KEYS},
            dict(normalized),
        )
        self.assertEqual(source["supplementary_provisions"], 1)
        self.assertEqual(source["appendices"], 1)

    def test_allows_text_difference_when_effective_dates_differ(self):
        document = normalize_xml(self.xml_path)
        json_path = Path(self.temporary_directory.name) / "old.json"
        json_path.write_text(
            json.dumps(
                {
                    "법령": {
                        "기본정보": {"시행일자": "20250101"},
                        "조문": {"조문단위": {"조문내용": "이전 조문"}},
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = validate_document(
            document,
            self.xml_path,
            json_path,
            {
                "law_id": "001248",
                "law_name": "주택임대차보호법",
                "effective_date": "20250101",
            },
        )

        self.assertEqual(result["json_comparison"], "version_mismatch")
        self.assertTrue(result["effective_date_changed"])

    def test_rejects_text_difference_when_effective_dates_match(self):
        document = normalize_xml(self.xml_path)
        json_path = Path(self.temporary_directory.name) / "invalid.json"
        json_path.write_text(
            json.dumps(
                {
                    "법령": {
                        "기본정보": {"시행일자": "20260102"},
                        "조문": {"조문단위": {"조문내용": "변조된 조문"}},
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "XML과 JSON의 조문 텍스트"):
            validate_document(
                document,
                self.xml_path,
                json_path,
                {
                    "law_id": "001248",
                    "law_name": "주택임대차보호법",
                    "effective_date": "20260102",
                },
            )


if __name__ == "__main__":
    unittest.main()
