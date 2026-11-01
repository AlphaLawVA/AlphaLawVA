# 표준계약서 재실행 파이프라인

현재 단계는 합성자료의 저장된 Google OCR 결과를 처리하는 로컬 CLI다.
법무부 표준임대차 본문 1~3페이지의 전세/월세와 서울중앙지방법원 일반형
매매계약서 1~3페이지를 지원한다. 그 밖의 양식은 지원하지 않는다.

## 실행

```powershell
.venv/Scripts/python.exe -m ml.document_processing.contracts.lease_pipeline --ocr-json C:/Users/oes05/Downloads/jeonse_document.json --contract-type jeonse --synthetic-only --output output/document_processing/lease_pipeline_v01/jeonse.json
```

월세는 contract-type을 monthly_rent, 매매는 sale로 바꾸고 해당 JSON을 지정한다.
결과 파일이 이미 존재하면 덮어쓰지 않으므로 새 출력 경로를 사용한다.
입력은 읽기만 하며, 골드셋을 로드하거나 외부 API를 호출하지 않는다.

## 동작

1. JSON 크기, 페이지 수/순서, 페이지별 OCR 줄 및 표제를 검사한다.
2. 기존 Google 좌표 추출기로 ContractExtraction을 생성한다.
3. 추출/누락 필드와 특약 경계의 확인 항목을 생성한다.
4. 입력 SHA-256, UTC 실행 시각, 단계별 로컬 시간과 함께 저장한다.

양식 검사는 표제 기반 휴리스틱이다. 정밀한 양식 버전 식별이 아니며
표제가 잘못 인식되면 정상 양식도 거부할 수 있다. 전체 형식이 검증됐거나
법적 판단이 완료됐다는 뜻이 아니다. 모든 결과 상태는 needs_review다.
추출되지 않은 선택항목은 스키마의 null 및 경고도 함께 확인해야 한다.

timings_seconds의 ocr_request는 null이다. 저장된 JSON에서는 과거
Google OCR 시간을 복원할 수 없다. local_total_excluding_output_write는
파일 읽기부터 확인 목록 생성까지이며 Python 시작과 출력 쓰기는 제외한다.

## 이번 검증

- 전세/월세/매매 저장 JSON 실제 재실행과 양식별 표제 검사를 확인했다.
- OCR 테스트를 unittest.TestCase로 구성해 CI 자동 수집에 포함했다.
- 기존 합성 텍스트 PDF를 tests/document_processing/fixtures/로 분리했다.
  이제 PDF 통합 테스트는 로컬 output 파일에 의존하지 않으며 fixture가
  없으면 건너뛰지 않고 실패한다.
- 전체 ruff check 통과. 저장소 루트 output/과 tmp/를 Git에서 제외하여
  임시 외부 라이브러리는 lint 대상에서도 제외한다. 프로젝트 코드/테스트는 유지한다.

## 남은 작업

- Google 실시간 연결은 [별도 CLI](DOCUMENT_AI_LIVE.md)로 추가했고 합성자료
  3건으로 실제 인증과 호출을 검증했다.
- 독립적인 같은 양식 새 작성본 검증, 사용자 확인 API/UI.
- 등기사항전부증명서는 별도 구조화 파이프라인으로 구현한다.
- 실제 문서용 개인정보 처리, 암호화 저장, 접근 제어와 폐기 정책 구현.

합성자료 전용 플래그는 사용자의 선언이며 실제 개인정보가 없는지 증명하지
않는다. 현재 로컬 JSON 출력은 암호화 저장소가 아니다. 원본 JSON을 그대로
결과에 복사하지 않지만 실제 문서 개인정보 보호를 보장하지 않는다.
