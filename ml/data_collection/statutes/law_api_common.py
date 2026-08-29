import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen

STATUTE_MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = STATUTE_MODULE_DIR.parents[2]
STATUTE_CONFIG_DIR = STATUTE_MODULE_DIR / "config"
STATUTE_DATA_DIR = PROJECT_ROOT / "data" / "statutes"
ENV_FILE = PROJECT_ROOT / ".env"


def load_law_api_key() -> str:
    key = os.environ.get("LAW_API_KEY", "").strip()
    if key:
        return key

    if ENV_FILE.exists():
        for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() == "LAW_API_KEY":
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if value:
                    return value

    raise RuntimeError(
        "LAW_API_KEY가 없습니다. 프로젝트 루트의 .env 파일에 "
        "LAW_API_KEY=인증값 형식으로 설정하세요."
    )


def fetch_json(url: str, params: dict, attempts: int = 3) -> dict:
    request_url = f"{url}?{urlencode(params)}"
    request = Request(
        request_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "AlphaLawVA/0.1",
        },
    )

    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=30) as response:
                raw_text = response.read().decode("utf-8", errors="replace")
            return json.loads(raw_text)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            if attempt == attempts:
                raise
            time.sleep(2 ** (attempt - 1))

    raise RuntimeError("API 요청 재시도 처리가 비정상적으로 종료되었습니다.")


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON 최상위 값이 객체가 아닙니다: {path}")
    return payload


def redact_secret(message: str, secret: str) -> str:
    redacted = message.replace(secret, "[REDACTED]")
    redacted = redacted.replace(quote_plus(secret), "[REDACTED]")
    return redacted


def as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []
