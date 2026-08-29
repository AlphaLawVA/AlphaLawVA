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

- `classify_precedents.py`
  - `processed/classification_cases/` JSON에서 사건명, 판시사항, 판결요지만 뽑아
    LLM 입력용 JSONL을 만든다.
  - 키워드 진단 결과는 프롬프트에 넣지 않고 결과 JSON에만 붙여 후검수에 쓴다.
  - `--mode classify`에서는 LLM으로 관련성 3분류, confidence,
    사람 검토 필요 여부를 JSONL로 저장한다.
  - 이미 분류된 판례는 기본적으로 다시 분류하지 않고 건너뛴다.

- `preprocess_precedents.py`
  - 청킹 전 단계에서 raw 상세 JSON을 판례별 전처리 JSON으로 바꾼다.
  - SuperLawVA 전처리 방식을 기준으로 사건번호, 선고일자, 판례내용을 정리한다.
  - `판례내용` 안의 `주문`, `청구취지`, `원심판결`, `이유`를 별도 한글 필드로 분리한다.
  - AlphaLawVA 보강 필드로 `판례일련번호`, `법원명`, `판시사항`, `판결요지`,
    `참조조문`, `참조판례`, `원본파일경로`를 유지한다.

- `prepare_classification_cases.py`
  - `processed/cases/` 전처리본에서 1차 LLM 분류 대상 파일을 만든다.
  - 사건명이 없거나 판시사항/판결요지가 둘 다 없는 판례는 분류용 대상에서 제외한다.
  - 판례마다 `keyword_diagnosis`를 저장하지만, 이 값은 LLM 판단 근거가 아니라
    LLM 분류 후 상충 검수용으로만 사용한다.

## 저장 구조

```text
local_data/precedents/
├── raw/
│   ├── searches/
│   │   ├── case_name/
│   │   └── body/
│   └── details/
├── manifests/
    ├── collection_manifest.json
    ├── candidates.jsonl
    └── retry_queue.jsonl
└── processed/
    ├── cases/
    │   └── {판례일련번호}.json
    ├── classification_cases/
    │   └── {판례일련번호}.json
    ├── excluded_outliers/
    │   └── {판례일련번호}.json
    ├── preprocess_index.json
    ├── preprocess_report.json
    ├── keyword_diagnosis_report.json
    ├── llm_inputs.jsonl
    ├── classification_results.jsonl
    ├── classification_failures.jsonl
    └── classification_manifest.json
```

## 실행

Python 가상환경 생성 후 실행할 때:

```bash
/opt/anaconda3/bin/python -m venv .venv
.venv/bin/python --version
```

Ollama 모델은 Python 패키지가 아니라 Ollama가 관리하는 로컬 모델이다. 따라서
`.venv` 안에 설치되는 것이 아니라 사용자 로컬 Ollama 저장소에 내려받는다.

```bash
ollama pull gemma2:9b
```

로컬 모델은 품질과 속도 확인 전까지 최종 분류용 모델로 확정하지 않는다.
테스트 결과가 나쁘면 전체 실행하지 않고 프롬프트, 규칙 필터, Gemini 사용 여부를 다시 정한다.

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

청킹 전 판례 전처리본을 만들 때:

```bash
python3 precedents/preprocess_precedents.py
```

일부 판례만 테스트 전처리할 때:

```bash
python3 precedents/preprocess_precedents.py --limit 20 --overwrite
```

LLM 입력 데이터만 먼저 만들 때:

```bash
.venv/bin/python precedents/prepare_classification_cases.py --core-field-policy issue_or_summary
python3 precedents/classify_precedents.py --mode prepare
```

첫 번째 명령은 판시사항 또는 판결요지 중 하나라도 있는 판례만
`processed/classification_cases/`로 생성한다. 두 번째 명령은 이 분류용 판례에서
사건명, 판시사항, 판결요지만 LLM 입력으로 만든다.

Gemini Lite로 일부만 테스트 분류할 때:

```bash
.venv/bin/python precedents/classify_precedents.py --mode classify --provider gemini --gemini-model gemini-3.5-flash-lite --limit 20 --overwrite --progress-every 1
```

Gemini Lite로 전체 분류를 이어서 진행할 때:

```bash
.venv/bin/python precedents/classify_precedents.py --mode classify --provider gemini --gemini-model gemini-3.5-flash-lite --progress-every 100
```

성공한 결과는 `processed/classification_results.jsonl`에 즉시 저장되므로,
중간에 멈춰도 다음 실행 때 기존 결과를 건너뛰고 이어서 진행한다.
다시 처음부터 테스트하고 싶을 때만 `--overwrite`를 붙인다.

LLM 분류 후 키워드 진단과 상충되는 케이스를 우선 검수한다.
예를 들어 `LLM=related`인데 `keyword_diagnosis.label=strong_unrelated_signal`이거나,
`LLM=unrelated`인데 `keyword_diagnosis.label=strong_related`이면 원문 이유를 확인한다.

기본 `auto` provider는 `.env`의 `OLLAMA_BASE_URL`, `PRECEDENT_LLM_MODEL`,
`GEMINI_API_KEY`, `GEMINI_MODEL`을 사용한다. 현재 1차 판례 분류 실험에서는
Gemini Lite를 우선 사용한다.

이미 분류된 판례를 유지하면서 이어서 분류할 때:

```bash
.venv/bin/python precedents/classify_precedents.py --mode classify --provider gemini --gemini-model gemini-3.5-flash-lite
```
