# 표준임대차 재실행 파이프라인

현재 단계는 합성자료의 저장된 Google OCR 결과를 처리하는 로컬 CLI다.
법무부 표준임대차 본문 1~3페이지의 전세/월세만 지원한다. 매매와 다른
임대차 양식은 기존 실험을 보존하되 이 진입점에서는 지원하지 않는다.

## 실행

```powershell
.venv/Scripts/python.exe -m ml.document_processing.contracts.lease_pipeline --ocr-json C:/Users/oes05/Downloads/jeonse_document.json --contract-type jeonse --synthetic-only --output output/document_processing/lease_pipeline_v01/jeonse.json
```

월세는 contract-type을 monthly_rent로 바꾸고 해당 JSON을 지정한다.
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

- 전세/월세 저장 JSON 실제 재실행 성공, 기존 전세 입력 해시 일치 확인.
- OCR 테스트 12개를 unittest.TestCase로 변환해 CI 자동 수집에 포함했다.
  검증 조건은 유지했고, CI와 같은 전체 discovery 명령으로 168개가
  건너뛰기 없이 통과했다(로컬 실행; GitHub 원격 실행은 아직 하지 않음).
- 기존 합성 텍스트 PDF를 tests/document_processing/fixtures/로 분리했다.
  이제 PDF 통합 테스트는 로컬 output 파일에 의존하지 않으며 fixture가
  없으면 건너뛰지 않고 실패한다.
- 전체 ruff check 통과. 저장소 루트 output/과 tmp/를 Git에서 제외하여
  임시 외부 라이브러리는 lint 대상에서도 제외한다. 프로젝트 코드/테스트는 유지한다.

## 남은 작업

- Google 실시간 클라이언트와 명시적 모델 버전, 요청 제한, 요청 시간 측정.
- 비용/데이터 전송 승인 후 합성자료 소량 호출 검증.
- 독립적인 같은 양식 새 작성본 검증, 사용자 확인 API/UI.
- 실제 문서용 개인정보 처리, 암호화 저장, 접근 제어와 폐기 정책 구현.

합성자료 전용 플래그는 사용자의 선언이며 실제 개인정보가 없는지 증명하지
않는다. 현재 로컬 JSON 출력은 암호화 저장소가 아니다. 원본 JSON을 그대로
결과에 복사하지 않지만 실제 문서 개인정보 보호를 보장하지 않는다.

## 브랜치와 커밋

2026-10-06 확인 기준 feat/contract-document-intake에서 작업 중이며
문서 처리 신규 파일들과 CI/dev requirements 변경이 미커밋 상태다.
이번 변경은 lease_pipeline.py, test_lease_pipeline.py, 이 문서의 추가다.
기존 추출 스키마/엔진/API/모델을 변경하지 않았다. 커밋/푸시는 수행하지 않았다.
커밋 전에 기존 미추적 코드와 데이터의 포함 범위, 비밀/개인정보 유무,
생성 결과 및 tmp 제외 여부를 별도로 점검해야 한다. git add .는 사용하지 않는다.

## 이번 추가만 되돌리기

미추적 파일이므로 아래 경로만 먼저 미리보기 후 제거한다. 기존 실험은 보존된다.

```powershell
git clean -n -- ml/document_processing/contracts/lease_pipeline.py tests/document_processing/test_lease_pipeline.py docs/STANDARD_LEASE_PIPELINE.md
git clean -f -- ml/document_processing/contracts/lease_pipeline.py tests/document_processing/test_lease_pipeline.py docs/STANDARD_LEASE_PIPELINE.md
```
