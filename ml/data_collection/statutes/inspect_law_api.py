import argparse
import re
from datetime import datetime

from law_api_common import STATUTE_DATA_DIR, fetch_json, load_law_api_key, write_json

SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"
OUTPUT_DIR = STATUTE_DATA_DIR / "raw_jsons/debug"


def safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["search", "detail", "article"])
    parser.add_argument("--query")
    parser.add_argument("--id")
    parser.add_argument("--jo")
    args = parser.parse_args()

    api_key = load_law_api_key()

    if args.mode == "search":
        if not args.query:
            raise ValueError("search 모드에는 --query가 필요합니다.")

        url = SEARCH_URL
        params = {
            "OC": api_key,
            "target": "eflaw",
            "type": "JSON",
            "query": args.query,
            "nw": "3",
        }
        label = f"search_{args.query}"

    elif args.mode == "detail":
        if not args.id:
            raise ValueError("detail 모드에는 --id가 필요합니다.")

        url = SERVICE_URL
        params = {
            "OC": api_key,
            "target": "eflaw",
            "type": "JSON",
            "ID": args.id,
        }
        label = f"detail_{args.id}"

    else:
        if not args.id or not args.jo:
            raise ValueError("article 모드에는 --id와 --jo가 필요합니다.")

        url = SERVICE_URL
        params = {
            "OC": api_key,
            "target": "eflawjosub",
            "type": "JSON",
            "ID": args.id,
            "JO": args.jo,
        }
        label = f"article_{args.id}_{args.jo}"

    result = fetch_json(url, params)
    if result.get("msg") and "법령" not in result and "LawSearch" not in result:
        raise RuntimeError(
            f"OPEN API 조회 실패: {result.get('msg') or result.get('result')}"
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"{safe_name(label)}_{timestamp}.json"
    write_json(output_path, result)

    print(f"저장 완료: {output_path}")
    print(f"최상위 필드: {list(result.keys())}")


if __name__ == "__main__":
    main()
