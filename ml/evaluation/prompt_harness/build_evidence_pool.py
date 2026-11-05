# build_evidence_pool.py
"""
Description: 프롬프트 하네스가 쓰는 실제 법령 조문 원문 묶음을 로컬 법령 청크에서 추출한다.
조문을 지어내지 않도록 한 번 추출한 원문을 evidence_pool_v01.json 으로 고정한다.
Author: ooheunsu
Date: 2026-11-05
Before:
    - 프롬프트 평가에 쓸 근거 조문 원문 모음이 없는 상태.
After:
    - 법령 ID와 조문 번호 목록으로 실제 청크 원문을 추출해 평가 입력으로 재사용함.
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

# 법령 ID별로 평가 근거에 쓸 조문 번호. 새 조문은 여기에 추가하고 다시 실행한다.
SELECTED_ARTICLES: dict[str, list[str]] = {
    "001248": ["제3조", "제3조의2", "제4조", "제6조", "제6조의3", "제7조", "제8조", "제10조"],
    "001706": ["제356조", "제548조", "제551조", "제623조", "제626조", "제628조"],
}

DEFAULT_OUTPUT = Path(__file__).with_name("evidence_pool_v01.json")


def build_pool(chunks_zip: Path) -> list[dict[str, str]]:
    pool: list[dict[str, str]] = []
    with zipfile.ZipFile(chunks_zip) as archive:
        for law_id, labels in SELECTED_ARTICLES.items():
            document = json.loads(archive.read(f"chunks/{law_id}.json"))
            found = {
                chunk["metadata"]["article_label"]: chunk
                for chunk in document["chunks"]
                if chunk["metadata"]["article_label"] in labels
            }
            missing = [label for label in labels if label not in found]
            if missing:
                raise ValueError(f"{law_id} 법령에서 조문을 찾지 못했습니다: {missing}")
            for label in labels:
                chunk = found[label]
                metadata = chunk["metadata"]
                pool.append(
                    {
                        "source_id": chunk["chunk_id"],
                        "law_name": metadata["law_name"],
                        "article_label": label,
                        "article_title": metadata["article_title"],
                        "effective_date": metadata["effective_date"],
                        "text": chunk["retrieval_text"],
                    }
                )
    return pool


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks-zip", type=Path, default=Path("data/statutes/chunks.zip"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    pool = build_pool(args.chunks_zip)
    args.output.write_text(
        json.dumps(pool, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{len(pool)}개 조문을 {args.output}에 저장했습니다.")


if __name__ == "__main__":
    main()
