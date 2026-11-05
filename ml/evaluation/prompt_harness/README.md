# 프롬프트 하네스

계약서 분석(`gemini_contract_review_analyzer`)과 특약 생성(`gemini_special_clause_generator`)
프롬프트를 **같은 입력 세트로 돌리고 같은 규칙으로 채점**해서 프롬프트 버전끼리 비교하는 도구입니다.

## 구성
| 파일 | 역할 |
|---|---|
| `evidence_pool_v01.json` | 실제 법령 조문 원문 14개(주택임대차보호법, 민법). `build_evidence_pool.py`로 로컬 법령 청크에서 추출 |
| `cases_v01.json` | 평가 케이스 25개(분석 15, 특약 10). 합성 입력과 위 조문으로 구성, 개인정보 없음 |
| `checks.py` | 자동 채점 규칙. 모델을 호출하지 않음 |
| `runner.py` | 실행기. 결과를 `local_data/prompt_harness/runs/<시각>/`에 저장 |

## 자동 채점 규칙
- 실패: 법적 결론 단정, 근거에 없는 조문·사건번호·금액·비율·날짜, 제공되지 않은 근거 ID, 필수 항목 누락, 길이 초과, 프롬프트 주입 따름, 특약 상태 불일치, 초안의 빈칸 표시(○○, [ ] 등), 서버 검증 탈락
- 경고: 위험·안전 판정으로 읽히는 표현, 근거에 없는 기간(30일 이내 등), 초안인데 확인 질문이 남음
- 사람 검토: 쉬운 말인지, 원문 취지를 지켰는지, 근거와 맞는지는 `review_sheet.md`에서 직접 확인

## 실행
```powershell
# 오프라인(가짜 응답, 무료): 하네스 자체가 동작하는지 확인
.venv\Scripts\python.exe -m ml.evaluation.prompt_harness.runner --mode fake

# 실제 호출(유료): 반드시 --confirm-live, 호출 횟수는 --max-calls 로 제한
.venv\Scripts\python.exe -m ml.evaluation.prompt_harness.runner --mode live --confirm-live --max-calls 30

# 후보 프롬프트 비교: 프롬프트 파일을 바꿔서 같은 케이스로 실행
.venv\Scripts\python.exe -m ml.evaluation.prompt_harness.runner --mode live --confirm-live --review-prompt-file candidate_review.txt
```

## 읽는 법
`summary.md`의 규칙별 표에서 **실패 수가 줄고 경고가 늘지 않으면** 개선입니다.
`config.json`에 프롬프트 버전과 해시, 모델, 사용량이 남으므로 어떤 조건의 결과인지 추적할 수 있습니다.

## 주의
- 케이스를 바꾸면 이전 결과와 직접 비교할 수 없으므로 `cases_v02.json`처럼 버전을 올립니다.
- 하네스는 판례 검색이나 내용증명 프롬프트를 다루지 않습니다.
