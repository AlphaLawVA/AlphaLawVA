# 서버 준비와 팀 테스트 (Docker 없이)

API 서버를 직접 실행하는 방법이다. 개발용 MySQL만 Docker(`compose.mysql.yml`)를 쓰고, API는 컨테이너로
만들지 않는다. API 이미지(Dockerfile)는 배포 일정이 정해지면 따로 만든다.

## 1. 파이썬 환경

- Python 3.11
- 설치: `pip install -r requirements-api.txt -r requirements-embedding.txt`
- `requirements-embedding.txt`의 `transformers`는 5.10.2, `sentence-transformers`는 5.5.1이다.
  리랭커 KURE-Reranker-nano가 쓰는 ModernBERT 구조는 transformers 4.48부터 지원되므로 이전 버전
  (4.47.0)으로 설치하면 리랭커를 불러오지 못한다(경고 후 리랭커 없이 동작).

## 2. 모델과 데이터

| 항목 | 위치(환경변수) | 준비 방법 |
|---|---|---|
| BGE-M3 검색 모델 | `ALPHALAWVA_MODEL_CACHE_DIR` | 팀 공유본을 복사 |
| 법령 벡터 DB | `ALPHALAWVA_STATUTE_DB_DIR` | 팀 공유본을 복사 |
| 판례 벡터 DB | `ALPHALAWVA_PRECEDENT_DB_DIR` | 팀 공유본을 복사 |
| 리랭커 KURE-Reranker-nano (약 0.6GB) | `local_data/models/rerankers/kure-reranker-nano` (`RAG_STATUTE_RERANKER_DIR`) | `python -m ml.rag.download_reranker_model` |

- 리랭커 명령은 고정된 버전(`nlpai-lab/KURE-Reranker-nano`, 커밋 `8514b9e1…`)을 받고 파일 해시를 검증한다.
  이미 받은 파일만 검증하려면 `--verify-only`를 붙인다. 심볼릭 링크를 쓰지 않아 Windows에서도 동작한다.
- 허깅페이스 모델은 용량이 크므로 저장 위치가 있는 드라이브의 여유를 먼저 확인한다.
- 실행 중 모델을 다시 받지 않게 하려면 서버 실행 전에 `HF_HUB_OFFLINE=1`을 설정한다.

## 3. 환경변수 (`.env`)

`.env.example`을 복사해 `.env`를 만든 뒤 값을 채운다. 비밀 값은 저장소에 올리지 않는다.

- `OPENAI_API_KEY`: 특약 생성·계약서 검토의 기본 생성 모델 `gpt-6.1-terra`에 필요하다. 이 키는 Chat Completions만 허용된다.
- `GEMINI_API_KEY`: 범위 분류기(Gemini flash-lite)에 필요하다.
- `SPECIAL_CLAUSE_RAG_ENABLED`, `SPECIAL_CLAUSE_LLM_ENABLED`, `CONTRACT_REVIEW_RAG_ENABLED`, `CONTRACT_REVIEW_LLM_ENABLED`를 `true`로 켠다.
- `SNAPSHOT_STORE_BACKEND`: `memory`(재시작하면 사라짐) 또는 MySQL.
- `ALPHALAWVA_CORS_ORIGINS`: 화면(프론트)이 접속하는 주소를 쉼표로 추가한다. 다른 사람이 임시 링크로 접속하면 그 주소를 넣어야 한다.
- 프론트는 `NEXT_PUBLIC_API_BASE_URL`(기본 `http://127.0.0.1:8000`)로 API 주소를 찾는다.
- 이전 동작으로 되돌리려면 `RAG_STATUTE_RERANKER_ENABLED=false`, `SPECIAL_CLAUSE_LLM_MODEL=gemini-3.5-flash-lite`, `CONTRACT_REVIEW_LLM_MODEL=gemini-3.5-flash-lite`.

## 4. 실행과 확인

```bash
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

- 상태 확인: `GET /health` → `{"status":"ok","service":"AlphaLawVA"}`
- API 문서: `http://127.0.0.1:8000/docs`
- 첫 요청은 검색 모델과 리랭커를 불러오느라 1분 안팎 걸린다. 이후 특약 요청 1건은 약 12초(노트북 CPU 기준)다.
- 메모리는 모델 로딩 때문에 수 GB 이상의 여유가 필요하다(검증 환경에서 약 4GB 여유로 동작).

## 5. 팀원과 같은 링크로 테스트할 때 (노트북에서 서버를 켜는 경우)

- 서버를 켠 PC에서 터널 도구로 외부 접속 주소를 만들어 공유하는 방식이다. 이 PC가 켜져 있는 동안만 접속된다.
- 링크가 바뀌면 `ALPHALAWVA_CORS_ORIGINS`와 `NEXT_PUBLIC_API_BASE_URL`을 새 주소로 맞춘다.
- 공개된 링크를 아는 사람은 누구나 API를 호출할 수 있고 호출 비용(OpenAI·Gemini)은 키 주인에게 청구된다. 링크는 팀원에게만 공유하고 접속 제한(비밀번호 등)을 둔다.
- 개인정보가 들어간 실제 계약서를 올리지 않는다.
