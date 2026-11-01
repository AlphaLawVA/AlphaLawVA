# export_extraction_schemas.py
"""
Description: Pydantic으로 정의한 계약 검토 문서 추출 모델을 프론트엔드와
OCR 구현에서 공유할 수 있는 버전형 JSON Schema 파일로 내보낸다.
Author: ooheunsu
Date: 2026-09-20
Before:
    - 계약서·등기부·확인설명서 추출 Pydantic 모델이 정의된 상태.
After:
    - schema 디렉터리에 문서별 v0.1 JSON Schema 파일이 생성됨.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from ml.document_processing.contracts.extraction_schema import (
    extraction_json_schemas,
)


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "schema"


def export_extraction_schemas(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, schema in extraction_json_schemas().items():
        output_path = output_dir / filename
        output_path.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written.append(output_path)
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="계약 검토 추출 JSON Schema 생성")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="JSON Schema 출력 디렉터리",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for path in export_extraction_schemas(args.output_dir):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
