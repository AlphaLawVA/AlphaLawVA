# Google Document AI 실시간 실험

합성 표준임대차 PDF 3페이지(전세/월세) 전용 로컬 CLI다. 실제 지인 문서는
전송하지 않는다. 매매, 다른 양식, 개인정보 비식별화, 암호화 저장, UI/API는
이번 작업 범위가 아니다. 기존 lease_pipeline의 저장 JSON 재실행은 유지한다.

## 준비와 비용 승인

1. `.venv/Scripts/python.exe -m pip install -r requirements-document-ai.txt`
2. 기본 실행은 사전 확인만 수행하며 인증정보도 읽지 않고 외부 호출하지 않는다.
3. 전송할 PDF/페이지 수, Google 프로세서 버전, 1회 요청과 예상 비용을 확인하고
   해당 실행에 대한 사용자 승인을 받는다. 과거 호출 승인은 재사용하지 않는다.
4. 승인된 실행 전에 Google Cloud 계정의 단기 OAuth 액세스 토큰을 로컬 `.env`의
   `GOOGLE_DOCUMENT_AI_ACCESS_TOKEN`에 설정한다. API 키가 아니다. 토큰을 채팅,
   Git, 로그에 넣지 않는다. `.env.example`에는 플레이스홀더만 있다.
   토큰 발급과 IAM 권한 설정은 별도 사용자 작업이며 아직 확인하지 않았다.

```powershell
.venv/Scripts/python.exe -m ml.document_processing.contracts.document_ai_live --pdf C:/Users/oes05/Downloads/전세_손글씨_01.pdf --contract-type jeonse --project 612326834377 --location us --processor 516e31deb9cf1b4c --version pretrained-ocr-v2.1-2024-08-07 --synthetic-only
```

이는 사전 확인 명령이다. 실제 실행은 **승인 후에만** `--execute-approved`와
`--estimated-cost-usd <승인한 예상 USD 금액>`을 추가한다. 월세는 파일과 유형을
monthly_rent로 변경한다. 모델 버전은 이전 콘솔 실험 버전을 명시한 것이며
현재 서버에서 그 버전에 접근 가능한지는 유료 호출 전까지 확인되지 않았다.

## 처리와 재실행

- PDF 실제 형식, 20 MiB 제한, 암호화 여부와 정확히 3페이지인지 먼저 검사한다.
- PDF 해시와 프로세서/버전/요청 설정으로 실행 폴더를 정한다.
- 첫 호출 전에 폴더를 독점 생성하고 attempt.json을 남긴다. 동시 호출과 실패 후
  자동 재시도를 차단한다. 폴더를 임의로 바꾸면 이 보호를 우회할 수 있다.
- Google REST 응답의 text와 pages(좌표 포함)를 document.json으로 보존한다.
- manifest.json에 버전, 해시, 요청 시간, 예상 비용, 재시도 0회를 기록한다.
  실제 청구액은 알 수 없어 null이며, 예상 비용은 청구 한도가 아니다.
- 기존 표준임대차 추출기로 검토용 결과를 만든다. 결과 상태는 needs_review다.
- 같은 입력과 설정은 해시를 검증한 캐시를 재사용한다. 추출 결과는 매번 별도
  파일로 보존한다. OCR 성공 후 추출 실패 시에도 OCR 결과는 재사용한다.
- 캐시 이용 시 이번 OCR 시간은 null, 최초 요청 시간은 original_request_seconds다.

요청 시간은 네트워크 왕복과 응답 JSON 해석을 포함하며 모델 추론만의 시간이
아니다. PDF 읽기와 후속 추출은 제외한다. 기본 연결 타임아웃은 10초, 읽기는
120초다. 읽기 타임아웃은 무응답 구간 제한으로 전체 작업의 강제 종료 시간이
아니다. 리다이렉트와 자동 HTTP 재시도는 하지 않는다.

오류나 강제 종료 후 attempt.json만 남으면 완료/과금 여부가 불명확하다.
자동 재시도하지 말고 콘솔/실행 폴더를 확인하고 새 호출 승인을 받아야 한다.
오류 본문과 인증 헤더는 출력하지 않는다. 합성 선언 플래그가 개인정보 부재를
검증하지는 않으며, 로컬 OCR 원문과 결과는 암호화되지 않는다.

## 검증 및 한계

- 유료 호출 없이 실제 PDF 파싱, 사전 확인, 전송 옵션, 오류 비노출, 실패 후
  중복 방지, 캐시 무결성 및 추출 실패 후 재사용을 테스트한다.
- HTTP 응답은 테스트 대역이다. 실제 인증, IAM, 네트워크, OCR 품질/속도와
  청구액은 검증되지 않았다. 이 구현 자체가 OCR 정확도를 높이지는 않는다.
- 기존 골드와 평가 방식, 프롬프트, 공개 API, 추출 스키마는 변경하지 않았다.

공식 요청 규격:
https://cloud.google.com/document-ai/docs/send-request

## 변경 취소

커밋 전 기준, 아래 변경만 제거한다. 먼저 diff로 이후의 사용자 변경 유무를 확인한다.

```powershell
git restore -- .env.example requirements-dev.txt docs/STANDARD_LEASE_PIPELINE.md
git clean -n -- ml/document_processing/contracts/document_ai_live.py tests/document_processing/test_document_ai_live.py requirements-document-ai.txt docs/DOCUMENT_AI_LIVE.md
```

미리보기에서 이번 신규 파일만 확인한 뒤 같은 경로에 `git clean -f`를 사용한다.
실험 output 폴더와 이전 커밋은 보존한다. 이 문서의 변경 취소 명령은 자동 실행하지 않는다.
