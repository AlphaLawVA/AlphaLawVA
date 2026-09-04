# Statute Retrieval Evaluation

법령 검색 임베딩 모델 비교를 위한 평가 데이터다. 규칙과 지표 정의는
`docs/STATUTE_RETRIEVAL_EVALUATION_DATASET.md`를 따른다.

## Files

- `schema_v01.json`: 평가 문항 JSON Schema
- `case_template_v01.json`: 새 문항 작성용 템플릿
- `datasets/pilot_v01.jsonl`: 검수 전 파일럿 문항
- `datasets/regression_v01.jsonl`: 첫 검색 실패가 발생할 때 생성할 회귀 문항

`pilot_v01.jsonl`의 초기 문항은 모두 `draft`다. 세 모델 Top-10 후보 풀을
판정하고 교차 검수를 통과하기 전에는 골드 평가 결과에 사용하지 않는다.

## JSONL Rule

한 줄에 완전한 JSON 객체 하나를 저장한다. 주석과 빈 객체를 넣지 않는다.
문항 순서는 `query_id` 오름차순을 유지한다.

## 블라인드 후보 검수표 생성

세 모델 DB가 준비된 환경에서 다음을 실행한다.

```powershell
.\.venv\Scripts\python.exe -m ml.evaluation.run_statute_retrieval_pool `
  --device cpu `
  --run-id pilot-v01-initial
```

- 모델별 원본 Top-10: `runs/<run-id>/rankings.json`
- 실행 조건: `runs/<run-id>/run_manifest.json`
- 블라인드 검수표: `reviews/pilot_v01_<run-id>_blind.csv`

`runs/`와 `reviews/`는 생성 산출물이므로 Git에 커밋하지 않는다. 원본 순위는
라벨링이 끝날 때까지 검수자에게 공개하지 않는다.

## CSV 검수 규칙

1. `question`과 `retrieval_text`를 읽는다.
2. `relevance`에 `0`, `1`, `2`, `3` 중 하나를 입력한다.
3. `reason`에 판정 근거를 짧게 입력한다.
4. 나머지 열, 행 순서, 파일명은 변경하지 않는다.

관련도는 `0=무관`, `1=주제만 관련`, `2=답의 일부 또는 보조 근거`,
`3=질문에 직접 답하는 핵심 근거`다. 법적 근거가 불확실하면 추측하지 말고
`reason`에 재검수 필요를 기록한다.

## AI 보조 교차 검수

사람의 1차 블라인드 검수가 끝나면 원본 블라인드 JSONL을 AI가 독립
판정하고 팀원 검수 대상을 추린다.

```powershell
.\.venv\Scripts\python.exe -m ml.evaluation.review_statute_retrieval_pool
```

기본 실행은 질문별 AI 결과를 체크포인트 저장한다. 팀원 검수 대상은
관련도 `2 이상/미만` 불일치, AI 저확신, 치명 문항 통제표본, 일치 후보의
10% 고정 표본으로 구성한다. AI 결과만으로 문항을 승인하지 않는다.

## 잠정 골드 병합과 누락 검사

팀원 교차 검수 결과를 JSONL로 추출한 뒤 다음을 실행한다.

```powershell
.\.venv\Scripts\python.exe -m ml.evaluation.prepare_statute_gold_adjudication
```

이 단계는 사람·AI·팀원 판정의 관련 여부를 다수결로 잠정 병합하고,
세부 점수 불일치와 통제표본 충돌을 법령 원문으로 재검토한다. 또한 시드
정답, BM25, 필수 개념 일치 결과를 이용해 기존 후보 풀 밖의 정답 후보를
검사한다.

- 잠정 골드: `datasets/pilot_v01_provisional.jsonl`
- 준비 상태: `reviews/pilot_v01_pilot-v01-initial_readiness.json`
- `provisional_metrics_ready=true`: 잠정 지표 계산 가능
- `final_metrics_ready=true`: 남은 판정 충돌 없이 최종 지표 계산 가능

통제표본은 사람과 AI의 판정이 같았지만 팀원 검수의 일관성을 확인하려고
교차 검수표에 일부러 포함한 후보다. 숨겨 둔 정답이나 별도 데이터가 아니다.

## 라벨 민감도 평가

잠정 다수결과 법령 독립 재검토 중 어느 라벨을 적용하는지에 따라 모델
평가가 달라지는지 다음 명령으로 확인한다.

```powershell
.\.venv\Scripts\python.exe -m ml.evaluation.evaluate_statute_label_sensitivity
```

결과 JSON과 요약 Markdown은 `runs/pilot-v01-initial/` 아래에 생성된다.

질문별 공통 누락, 모델 고유 누락, 6~10위 정답과 라벨 민감도를 분석하려면
다음을 실행한다.

```powershell
.\.venv\Scripts\python.exe -m ml.evaluation.analyze_statute_retrieval_failures
```

실패 분석에서 양성 라벨 과포함이 의심되면 최종 근거 재검토에서 제외된
양성 청크를 다음 명령으로 전수 재검토한다.

```powershell
.\.venv\Scripts\python.exe -m ml.evaluation.recheck_statute_positive_labels
```

## 11문항 최종 사람 승인 준비

법령 근거 재판정 결과는 AI가 만든 최종 검수용 초안이며, 그 자체로 승인된
골드 라벨이 아니다. 사람이 확인할 질문, 현재 정답, 다수결과 정답 여부가
달라진 항목을 다음 명령으로 추린다.

```powershell
.\.venv\Scripts\python.exe -m ml.evaluation.prepare_statute_final_review
```

생성되는 `reviews/pilot_v01_final_human_review_draft.json`에는 질문 11건,
현재 정답 38건, 정답 여부가 달라진 42건과 법령 원문이 들어간다. 사람의
수정·승인을 반영하기 전까지 데이터셋 상태는 `draft`로 유지한다.

사람의 최종 결정을 기록한 뒤 승인본과 최종 지표를 생성한다.

```powershell
.\.venv\Scripts\python.exe -m ml.evaluation.finalize_statute_human_review
```

- 사람 최종 결정: `approvals/pilot_v01_final_human_decisions.json`
- 승인 데이터셋: `datasets/pilot_v01_approved.jsonl`
- 승인 기록: `approvals/pilot_v01_final_approval_manifest.json`
- 최종 지표: `runs/pilot-v01-initial/metrics_approved.json`
- 최종 지표 요약: `runs/pilot-v01-initial/metrics_approved.md`

`approvals/`와 `datasets/`는 검수 이력과 골드셋이므로 Git으로 관리한다.
원시 모델 응답이 들어 있는 `reviews/`와 재생성 가능한 `runs/`는 커밋하지
않는다.
