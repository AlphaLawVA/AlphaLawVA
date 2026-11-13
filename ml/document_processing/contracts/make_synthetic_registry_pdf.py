# make_synthetic_registry_pdf.py
"""
Description: 등기사항전부증명서(건물) 모양의 합성 테스트 PDF를 만든다. 이름·주소·번호는 모두 가짜이고 모든 쪽에
"합성 테스트 자료(실제 등기부 아님)"를 적는다. 실제 스캔본처럼 텍스트 없이 이미지로만 구운 PDF라 OCR 경로를 그대로 시험할 수 있다.
Author: ooheunsu
Date: 2026-11-13
Before:
    - 근저당·가압류·가등기·임차권 등 등기 시나리오별 시험 문서를 마스킹된 실제 등본 한 개로만 확인할 수 있었다.
After:
    - 시나리오별 합성 등본 PDF와 정답 값(JSON)이 출력 폴더에 만들어진다.
    - 개발 도구이며 PyMuPDF(fitz)와 Windows 한글 글꼴(맑은 고딕)이 필요하다. 서비스 실행에는 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/malgun.ttf"),
    Path("C:/Windows/Fonts/NanumGothic.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
)
PAGE_W, PAGE_H = 595.0, 842.0
MARGIN = 50.0
FONT_SIZE = 8.5
LINE_H = 11.5
NOTICE = "합성 테스트 자료 - 실제 등기부가 아님"
VIEWED_AT = "2026년11월13일 10시00분00초"

PROPERTY = "서울특별시 가나구 가상동 123-4"
ROAD = "서울특별시 가나구 가상로 12"
REGISTRY_OFFICE = "서울중앙지방법원 가상등기소"
FILE_NAME_ID = "1101-2020-000123"

TITLE_ROWS = [
    ["1", "", f"{PROPERTY}\n\n[도로명주소]\n{ROAD}",
     "철근콘크리트구조\n(철근)콘크리트지붕\n5층 다세대주택\n1층 63.20㎡\n2층 63.20㎡\n3층 63.20㎡\n4층 63.20㎡\n5층 63.20㎡",
     "도로명주소\n2019년3월4일 등기"],
]
OWNER_TRANSFER = [
    "1", "소유권이전", "2019년5월7일\n제23456호", "2019년4월20일\n매매",
    f"소유자 김가상 900101-*******\n{ROAD}, 101호",
]

# 시나리오: 갑구 행, 을구 행, 정답 값
SCENARIOS: dict[str, dict[str, Any]] = {
    "A_근저당": {
        "gapgu": [OWNER_TRANSFER],
        "eulgu": [
            ["1", "근저당권설정", "2020년8월20일\n제68947호", "2020년8월20일\n설정계약",
             f"채권최고액 금240,000,000원\n채무자 김가상\n{ROAD}, 101호\n"
             "근저당권자 가상은행 주식회사 110111-0000000\n서울특별시 가나구 가상대로 100"],
            ["1-1", "1번근저당권변경", "2022년6월2일\n제41234호", "2022년5월30일\n변경계약",
             "채권최고액 금300,000,000원"],
        ],
        "expected": {"owner": "김가상", "mortgage": "근저당권설정", "max_amount": "300,000,000원"},
    },
    "B_가압류_압류": {
        "gapgu": [
            OWNER_TRANSFER,
            ["2", "가압류", "2024년5월10일\n제12345호",
             "2024년5월9일\n서울중앙지방법원의\n가압류결정\n(2024카단1234)",
             "청구금액 금50,000,000원\n채권자 가상금융 주식회사 110111-1111111\n서울특별시 가나구 가상대로 200"],
            ["3", "압류", "2025년2월3일\n제3456호", "2025년2월1일\n압류(징수부-1234)",
             "권리자 국\n처분청 가나세무서장"],
        ],
        "eulgu": [],
        "expected": {"owner": "김가상", "seizure": ["가압류", "압류"]},
    },
    "C_가등기_임차권": {
        "gapgu": [
            OWNER_TRANSFER,
            ["2", "소유권이전청구권가등기", "2023년9월1일\n제55555호", "2023년8월30일\n매매예약",
             f"가등기권자 박가등 900202-*******\n서울특별시 가나구 가상로 99"],
        ],
        "eulgu": [
            ["1", "임차권등기", "2024년3월15일\n제7777호",
             "2024년3월10일\n서울중앙지방법원의\n임차권등기명령\n(2024카기888)",
             "임차보증금 금200,000,000원\n차임 월 금500,000원\n범위 건물 전부\n"
             "임대차계약일자 2022년3월5일\n점유개시일자 2022년3월20일\n확정일자 2022년3월21일\n"
             f"임차권자 이임차 880808-*******\n{ROAD}, 101호"],
        ],
        "expected": {"owner": "김가상", "provisional_registration": True, "lease_registration": True},
    },
    "D_깨끗한등기": {
        "gapgu": [OWNER_TRANSFER],
        "eulgu": [],
        "expected": {"owner": "김가상", "encumbrances": "없음"},
    },
}

TITLE_COLS = ("표시번호", "접 수", "소재지번,건물명칭 및 번호", "건물 내 역", "등기원인 및 기타사항")
TITLE_WIDTHS = (0.09, 0.12, 0.28, 0.27, 0.24)
RIGHTS_COLS = ("순위번호", "등 기 목 적", "접 수", "등 기 원 인", "권리자 및 기타사항")
RIGHTS_WIDTHS = (0.09, 0.17, 0.15, 0.15, 0.44)


def _font_path() -> Path:
    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("한글 글꼴을 찾지 못했습니다(맑은 고딕 또는 나눔고딕 필요).")


class Renderer:
    """표 그리기와 쪽 넘김을 맡는다."""

    def __init__(self) -> None:
        self.font_path = _font_path()
        self.font = fitz.Font(fontfile=str(self.font_path))
        self.doc = fitz.open()
        self.page: fitz.Page | None = None
        self.y = 0.0
        self.width = PAGE_W - 2 * MARGIN

    def text_width(self, text: str, size: float) -> float:
        return self.font.text_length(text, fontsize=size)

    def wrap(self, text: str, width: float, size: float = FONT_SIZE) -> list[str]:
        lines: list[str] = []
        for paragraph in text.split("\n"):
            current = ""
            for char in paragraph:
                if self.text_width(current + char, size) > width and current:
                    lines.append(current)
                    current = char
                else:
                    current += char
            lines.append(current)
        return lines

    def write(self, x: float, y: float, text: str, size: float = FONT_SIZE, center_in: float | None = None) -> None:
        assert self.page is not None
        if center_in is not None:
            x += (center_in - self.text_width(text, size)) / 2
        self.page.insert_text(fitz.Point(x, y), text, fontsize=size, fontname="kr", fontfile=str(self.font_path))

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        self.y = MARGIN + 8

    def section_title(self, name: str, hint: str) -> None:
        assert self.page is not None
        height = 22
        rect = fitz.Rect(MARGIN, self.y, MARGIN + self.width, self.y + height)
        self.page.draw_rect(rect, width=0.8)
        self.write(MARGIN + 40, self.y + 15, f"【 {name} 】", 11)
        self.write(MARGIN + 140, self.y + 15, f"( {hint} )", 8.5)
        self.y += height

    def header_row(self, columns: tuple[str, ...], widths: tuple[float, ...]) -> None:
        assert self.page is not None
        height = 22
        x = MARGIN
        for label, fraction in zip(columns, widths):
            w = self.width * fraction
            self.page.draw_rect(fitz.Rect(x, self.y, x + w, self.y + height), width=0.6)
            self.write(x, self.y + 15, label, 8.5, center_in=w)
            x += w
        self.y += height

    def _draw_row(self, wrapped: list[list[str]], widths: tuple[float, ...], height: float) -> None:
        assert self.page is not None
        x = MARGIN
        for lines, fraction in zip(wrapped, widths):
            w = self.width * fraction
            self.page.draw_rect(fitz.Rect(x, self.y, x + w, self.y + height), width=0.6)
            for index, line in enumerate(lines):
                self.write(x + 4, self.y + 12 + index * LINE_H, line)
            x += w
        self.y += height

    def table(self, title: tuple[str, str], columns: tuple[str, ...], widths: tuple[float, ...],
              rows: list[list[str]], empty_text: str | None = None) -> None:
        if self.y + 22 + 22 + 40 > PAGE_H - 90:
            self.new_page()
            self.write(MARGIN, self.y + 8, f"[건물] {PROPERTY}", 9)
            self.y += 20
        self.section_title(*title)
        self.header_row(columns, widths)
        body = rows or ([[ "", "", "", "", empty_text]] if empty_text else [])
        for cells in body:
            wrapped = [self.wrap(cell, self.width * w - 8) for cell, w in zip(cells, widths)]
            height = max(len(lines) for lines in wrapped) * LINE_H + 10
            if self.y + height > PAGE_H - 90:  # 쪽 넘김: 다음 쪽에 소재와 머리글을 다시 쓴다
                self.new_page()
                self.write(MARGIN, self.y + 8, f"[건물] {PROPERTY}", 9)
                self.y += 20
                self.header_row(columns, widths)
            self._draw_row(wrapped, widths, height)
        self.y += 14

    def finish(self, notes: bool) -> None:
        assert self.page is not None
        self.write(MARGIN + 130, self.y + 10, "-- 이 하 여 백 --", 9)
        self.write(PAGE_W - MARGIN - 200, self.y + 40, f"관할등기소 {REGISTRY_OFFICE}", 8.5)
        total = len(self.doc)
        for index, page in enumerate(self.doc, start=1):
            self.page = page
            self.write(MARGIN, PAGE_H - 40, f"열람일시 : {VIEWED_AT}", 8.5)
            self.write(0, PAGE_H - 28, f"{index}/{total}", 8.5, center_in=PAGE_W)
            self.write(0, PAGE_H - 16, NOTICE, 7, center_in=PAGE_W)
        if notes:
            last = self.doc[-1]
            self.page = last
            for offset, line in enumerate((
                "* 실선으로 그어진 부분은 말소사항을 표시함.",
                "* 기록사항 없는 갑구, 을구는 '기록사항 없음'으로 표시함.",
                "* 본 등기사항증명서는 열람용이므로 출력하신 등기사항증명서는 법적인 효력이 없습니다.",
            )):
                self.write(MARGIN + 10, PAGE_H - 100 + offset * 12, line, 7.5)


def build(name: str, scenario: dict[str, Any]) -> fitz.Document:
    r = Renderer()
    r.new_page()
    r.write(0, r.y + 20, "등기사항전부증명서(현재 유효사항)", 15, center_in=PAGE_W)
    r.write(0, r.y + 40, "- 건물 -", 13, center_in=PAGE_W)
    r.write(PAGE_W - MARGIN - 170, r.y + 62, f"고유번호 {FILE_NAME_ID}", 8.5)
    r.y += 78
    r.write(MARGIN, r.y + 10, f"[건물] {PROPERTY}", 9)
    r.y += 20
    r.table(("표 제 부", "건물의 표시"), TITLE_COLS, TITLE_WIDTHS, TITLE_ROWS)
    r.table(("갑   구", "소유권에 관한 사항"), RIGHTS_COLS, RIGHTS_WIDTHS, scenario["gapgu"], "기록사항 없음")
    r.table(("을   구", "소유권 이외의 권리에 관한 사항"), RIGHTS_COLS, RIGHTS_WIDTHS, scenario["eulgu"], "기록사항 없음")
    r.finish(notes=True)
    return r.doc


def render_as_images(doc: fitz.Document, dpi: int = 200) -> fitz.Document:
    """텍스트 층이 없는 이미지 PDF로 바꿔 스캔본과 같은 입력이 되게 한다."""

    out = fitz.open()
    for page in doc:
        pixmap = page.get_pixmap(dpi=dpi)
        new_page = out.new_page(width=PAGE_W, height=PAGE_H)
        new_page.insert_image(new_page.rect, stream=pixmap.tobytes("png"))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--only", default="", help="시나리오 이름 일부(예: A)")
    parser.add_argument("--preview", action="store_true", help="쪽별 PNG 미리보기도 저장")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, scenario in SCENARIOS.items():
        if args.only and args.only not in name:
            continue
        vector = build(name, scenario)
        stem = f"합성등기부_{name}"
        render_as_images(vector).save(str(args.output_dir / f"{stem}.pdf"))
        (args.output_dir / f"{stem}.expected.json").write_text(
            json.dumps(scenario["expected"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if args.preview:
            for index, page in enumerate(vector, start=1):
                page.get_pixmap(dpi=100).save(str(args.output_dir / f"{stem}_p{index}.png"))
        print(f"생성: {stem} ({len(vector)}쪽)")


if __name__ == "__main__":
    main()
