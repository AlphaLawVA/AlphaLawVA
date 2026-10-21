# Google Document AI 실시간 실험

합성 표준계약서 PDF 3페이지(전세/월세/매매) 전용 로컬 CLI다. 실제 지인 문서는
전송하지 않는다. 다른 양식, 개인정보 비식별화, 암호화 저장, UI/API는 이번 작업
범위가 아니다. 기존 lease_pipeline의 저장 JSON 재실행은 유지한다.

## 준비와 비용 승인

1. `.venv/Scripts/python.exe -m pip install -r requirements-document-ai.txt`
2. 기본 실행은 사전 확인만 수행하며 인증정보도 읽지 않고 외부 호출하지 않는다.
3. 전송할 PDF/페이지 수, Google 프로세서 버전, 1회 요청과 예상 비용을 확인하고
   해당 실행에 대한 사용자 승인을 받는다. 과거 호출 승인은 재사용하지 않는다.
4. 승인된 실행은 Google 공식 Document AI 클라이언트와 Application Default
   Credentials(ADC)로 인증한다. 로컬에서는 `gcloud auth application-default login`
   후 quota project를 설정한다. 코드는 고정 API 키나 장기 토큰을 저장하지 않는다.
   인증 파일과 토큰을 채팅, Git, 로그에 넣지 않는다.

```powershell
.venv/Scripts/python.exe -m ml.document_processing.contracts.document_ai_live --pdf C:/Users/oes05/Downloads/전세_손글씨_01.pdf --contract-type jeonse --project 612326834377 --location us --processor 516e31deb9cf1b4c --version pretrained-ocr-v2.1-2024-08-07 --synthetic-only
```

이는 사전 확인 명령이다. 실제 실행은 **승인 후에만** `--execute-approved`와
`--estimated-cost-usd <승인한 예상 USD 금액>`을 추가한다. 월세는 파일과 유형을
`monthly_rent`, 매매는 `sale`로 변경한다.

## 처리와 재실행

- PDF 실제 형식, 20 MiB 제한, 암호화 여부와 정확히 3페이지인지 먼저 검사한다.
- PDF 해시와 프로세서/버전/요청 설정으로 실행 폴더를 정한다.
- 첫 호출 전에 폴더를 독점 생성하고 attempt.json을 남긴다. 동시 호출과 실패 후
  자동 재시도를 차단한다. 폴더를 임의로 바꾸면 이 보호를 우회할 수 있다.
- Google 공식 클라이언트 응답의 text와 pages(좌표 포함)를 document.json으로 보존한다.
- manifest.json에 버전, 해시, 요청 시간, 예상 비용, 재시도 0회를 기록한다.
  실제 청구액은 알 수 없어 null이며, 예상 비용은 청구 한도가 아니다.
- 기존 표준임대차 추출기로 검토용 결과를 만든다. 결과 상태는 needs_review다.
- 같은 입력과 설정은 해시를 검증한 캐시를 재사용한다. 추출 결과는 매번 별도
  파일로 보존한다. OCR 성공 후 추출 실패 시에도 OCR 결과는 재사용한다.
- 캐시 이용 시 이번 OCR 시간은 null, 최초 요청 시간은 original_request_seconds다.

요청 시간은 네트워크 왕복과 응답 변환을 포함하며 모델 추론만의 시간이 아니다.
PDF 읽기와 후속 추출은 제외한다. 기본 RPC 타임아웃은 120초이며 공식 클라이언트
호출의 자동 재시도는 명시적으로 비활성화한다.

오류나 강제 종료 후 attempt.json만 남으면 완료/과금 여부가 불명확하다.
자동 재시도하지 말고 콘솔/실행 폴더를 확인하고 새 호출 승인을 받아야 한다.
오류 본문과 인증 헤더는 출력하지 않는다. 합성 선언 플래그가 개인정보 부재를
검증하지는 않으며, 로컬 OCR 원문과 결과는 암호화되지 않는다.

## 2026-10-14 합성자료 검증

Google Enterprise Document OCR `pretrained-ocr-v2.1-2024-08-07`을 ADC로 실제
호출했다. 각 문서는 3페이지이며 1회씩 요청했고 자동 재시도는 사용하지 않았다.

| 유형 | OCR 요청 시간 | 로컬 추출 시간 | 구조 필드 정확도 | 특약 CER |
| --- | ---: | ---: | ---: | ---: |
| 전세 | 10.76초 | 0.067초 | 27/28, 96.4% | 2.53% |
| 월세 | 8.58초 | 0.099초 | 29/30, 96.7% | 5.23% |
| 매매 | 6.87초 | 0.049초 | 15/17, 88.2% | 1.82% |

이 수치는 추출 규칙 개발에도 사용한 합성자료 3건의 결과다. 독립 검증셋 성능이나
실제 문서 일반화 성능으로 해석하지 않는다. 실제 청구액은 응답에서 확인할 수 없어
기록하지 않았고, 승인 시 입력한 예상 비용은 건당 0.0045달러였다.

## 검증 및 한계

- 실제 PDF 파싱, ADC 인증, 공식 클라이언트 호출, 캐시와 중복 요청 차단을 확인했다.
- 단위 테스트에서는 사전 확인, 오류 비노출, 실패 후 중복 방지, 캐시 무결성 및
  추출 실패 후 OCR 결과 재사용을 테스트한다.
- OCR 철자 오류는 로컬 규칙으로 추측 보정하지 않고 사용자 확인 대상으로 남긴다.
- 기존 골드와 프롬프트, 공개 API, 추출 스키마는 변경하지 않았다.

공식 요청 규격:
https://cloud.google.com/document-ai/docs/send-request
