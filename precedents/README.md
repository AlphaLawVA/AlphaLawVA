# AlphaLawVA 판례 수집 작업 폴더

이 폴더는 판례 담당자가 사용하는 수집 코드와 설정을 모아둔 곳이다.
수집된 원본 대용량 데이터는 Git에 올리지 않고 `local_data/precedents/`에
저장한다.

## 파일 역할

- `precedent_config.py`
  - 국가법령정보 공동활용 API 주소, 환경변수, 저장 경로를 관리한다.
  - `.env`의 `LAW_API_KEY`를 읽어 API 요청의 `OC` 값으로 사용한다.
  - 원본 저장 위치는 기본적으로 `local_data/precedents/`다.

- `precedent_keywords.py`
  - 회의에서 정한 `KEYWORDS_V01` 원본을 보관한다.
  - 판례 목록 API에 직접 넣을 사건명 검색어와 본문 검색어를 분리한다.
  - 너무 넓어서 잡음이 큰 단어는 직접 수집 검색어로 쓰지 않도록 따로 둔다.

- `collect_precedents.py`
  - 키워드로 판례 목록을 조회한다.
  - 목록의 `판례일련번호`를 기준으로 후보를 합치고 중복을 제거한다.
  - 후보 판례의 본문조회 API 응답 전체를 `raw/details/`에 저장한다.
  - 이미 저장된 목록 페이지와 본문 파일은 다시 요청하지 않고 건너뛴다.
  - 실패한 요청은 `retry_queue.jsonl`에 남긴다.

## 저장 구조

```text
local_data/precedents/
├── raw/
│   ├── searches/
│   │   ├── case_name/
│   │   └── body/
│   └── details/
└── manifests/
    ├── collection_manifest.json
    ├── candidates.jsonl
    └── retry_queue.jsonl
```

## 실행

짧은 smoke test:

```bash
python3 precedents/collect_precedents.py --smoke-test
```

전체 수집:

```bash
python3 precedents/collect_precedents.py
```

기존 저장 파일을 무시하고 다시 받고 싶을 때:

```bash
python3 precedents/collect_precedents.py --overwrite-searches --overwrite-details
```
