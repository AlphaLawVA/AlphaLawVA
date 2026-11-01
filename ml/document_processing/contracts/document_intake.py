# document_intake.py
"""
Description: 계약서 PDF와 이미지의 실제 형식 및 안전 제한을 검증하고,
PDF 페이지의 텍스트 추출 가능 여부에 따라 후속 처리 경로를 결정한다.
Author: ooheunsu
Date: 2026-09-20
Before:
    - 사용자가 제출한 계약서 파일이 아직 신뢰할 수 없는 입력 상태.
After:
    - 검증된 문서 정보와 직접 추출·OCR·혼합 처리 경로가 메모리에서 반환됨.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable


DEFAULT_MAX_FILE_SIZE = 20 * 1024 * 1024
DEFAULT_MAX_PDF_PAGES = 100
DEFAULT_MIN_TEXT_CHARS = 20
SUPPORTED_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png"}


class DocumentIntakeError(ValueError):
    """계약서 입력을 안전하게 처리할 수 없을 때 발생한다."""


class DocumentKind(str, Enum):
    PDF = "pdf"
    IMAGE = "image"


class ProcessingRoute(str, Enum):
    DIRECT_TEXT = "direct_text"
    OCR = "ocr"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class ContractDocumentPage:
    page_number: int
    route: ProcessingRoute
    extracted_text: str | None
    extracted_char_count: int

    def to_dict(self, *, include_text: bool = False) -> dict[str, Any]:
        result = asdict(self)
        if not include_text:
            result.pop("extracted_text")
        return result


@dataclass(frozen=True)
class ContractDocumentInspection:
    filename: str
    kind: DocumentKind
    media_type: str
    size_bytes: int
    sha256: str
    page_count: int
    route: ProcessingRoute
    pages: tuple[ContractDocumentPage, ...]

    def to_dict(self, *, include_text: bool = False) -> dict[str, Any]:
        result = asdict(self)
        result["kind"] = self.kind.value
        result["route"] = self.route.value
        result["pages"] = [
            page.to_dict(include_text=include_text) for page in self.pages
        ]
        return result


def inspect_contract_document(
    path: Path,
    *,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    max_pdf_pages: int = DEFAULT_MAX_PDF_PAGES,
    min_text_chars: int = DEFAULT_MIN_TEXT_CHARS,
    pdf_reader_factory: Callable[[Path], Any] | None = None,
) -> ContractDocumentInspection:
    resolved = path.resolve()
    _validate_limits(max_file_size, max_pdf_pages, min_text_chars)
    if not resolved.is_file():
        raise DocumentIntakeError(f"계약서 파일이 없습니다: {resolved}")

    suffix = resolved.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocumentIntakeError(
            "지원하지 않는 계약서 형식입니다. PDF, JPEG, PNG만 허용합니다."
        )

    size_bytes = resolved.stat().st_size
    if size_bytes == 0:
        raise DocumentIntakeError("빈 계약서 파일은 처리할 수 없습니다.")
    if size_bytes > max_file_size:
        raise DocumentIntakeError(
            f"계약서 파일이 크기 제한을 넘습니다: {size_bytes} > {max_file_size}"
        )

    media_type, kind = _detect_file_type(resolved)
    _validate_suffix_matches_type(suffix, kind, media_type)
    digest = _sha256(resolved)
    if kind is DocumentKind.IMAGE:
        page = ContractDocumentPage(
            page_number=1,
            route=ProcessingRoute.OCR,
            extracted_text=None,
            extracted_char_count=0,
        )
        return ContractDocumentInspection(
            filename=resolved.name,
            kind=kind,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=digest,
            page_count=1,
            route=ProcessingRoute.OCR,
            pages=(page,),
        )

    reader_factory = pdf_reader_factory or _load_pdf_reader
    try:
        reader = reader_factory(resolved)
    except DocumentIntakeError:
        raise
    except Exception as error:
        raise DocumentIntakeError("PDF 구조를 읽을 수 없습니다.") from error
    if getattr(reader, "is_encrypted", False):
        raise DocumentIntakeError("암호화된 PDF는 암호 해제 후 제출해야 합니다.")

    pdf_pages = list(reader.pages)
    if not pdf_pages:
        raise DocumentIntakeError("페이지가 없는 PDF는 처리할 수 없습니다.")
    if len(pdf_pages) > max_pdf_pages:
        raise DocumentIntakeError(
            f"PDF 페이지 수가 제한을 넘습니다: {len(pdf_pages)} > {max_pdf_pages}"
        )

    pages = tuple(
        _inspect_pdf_page(page, index, min_text_chars)
        for index, page in enumerate(pdf_pages, start=1)
    )
    routes = {page.route for page in pages}
    if routes == {ProcessingRoute.DIRECT_TEXT}:
        document_route = ProcessingRoute.DIRECT_TEXT
    elif routes == {ProcessingRoute.OCR}:
        document_route = ProcessingRoute.OCR
    else:
        document_route = ProcessingRoute.HYBRID

    return ContractDocumentInspection(
        filename=resolved.name,
        kind=kind,
        media_type=media_type,
        size_bytes=size_bytes,
        sha256=digest,
        page_count=len(pages),
        route=document_route,
        pages=pages,
    )


def _validate_limits(
    max_file_size: int,
    max_pdf_pages: int,
    min_text_chars: int,
) -> None:
    if max_file_size < 1 or max_pdf_pages < 1 or min_text_chars < 1:
        raise ValueError("문서 처리 제한값은 모두 1 이상이어야 합니다.")


def _detect_file_type(path: Path) -> tuple[str, DocumentKind]:
    with path.open("rb") as file:
        header = file.read(16)
    if header.startswith(b"%PDF-"):
        return "application/pdf", DocumentKind.PDF
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", DocumentKind.IMAGE
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", DocumentKind.IMAGE
    raise DocumentIntakeError("파일 내용이 지원되는 PDF, JPEG, PNG 형식이 아닙니다.")


def _validate_suffix_matches_type(
    suffix: str,
    kind: DocumentKind,
    media_type: str,
) -> None:
    expected = {
        "application/pdf": {".pdf"},
        "image/png": {".png"},
        "image/jpeg": {".jpg", ".jpeg"},
    }[media_type]
    if suffix not in expected:
        raise DocumentIntakeError(
            f"파일 확장자와 실제 형식이 일치하지 않습니다: {suffix}, {kind.value}"
        )


def _load_pdf_reader(path: Path) -> Any:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError(
            "PDF 처리 패키지가 없습니다. "
            "pip install -r requirements-document-processing.txt 를 실행하세요."
        ) from error
    return PdfReader(path, strict=True)


def _inspect_pdf_page(
    page: Any,
    page_number: int,
    min_text_chars: int,
) -> ContractDocumentPage:
    try:
        text = page.extract_text() or ""
    except Exception as error:
        raise DocumentIntakeError(
            f"PDF {page_number}페이지의 텍스트를 추출할 수 없습니다."
        ) from error
    visible_char_count = sum(not character.isspace() for character in text)
    route = (
        ProcessingRoute.DIRECT_TEXT
        if visible_char_count >= min_text_chars
        else ProcessingRoute.OCR
    )
    return ContractDocumentPage(
        page_number=page_number,
        route=route,
        extracted_text=text if route is ProcessingRoute.DIRECT_TEXT else None,
        extracted_char_count=visible_char_count,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
