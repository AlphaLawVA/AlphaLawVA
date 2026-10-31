# AlphaLawVA API 설계 규칙

이 문서는 AlphaLawVA의 모든 백엔드 API가 일관된 형태를 갖도록 하기 위한 공통 설계 규칙이다.
판례 검색, 계약서 검토, 특약 생성, 내용증명 생성 등 기능을 구현하기 전에 이 규칙을 먼저 적용한다.

## 1. API 공통 규칙

| 구분 | 규칙 |
|---|---|
| 프로토콜 | HTTPS 기반 REST API를 사용한다. 로컬 개발 환경만 HTTP를 허용한다. |
| 버전 | 공개 API 경로는 `/api/v1`처럼 URL에 주 버전을 명시한다. |
| 데이터 형식 | 요청과 응답 본문은 UTF-8 JSON을 사용한다. |
| Content-Type | JSON 본문을 보낼 때 `application/json`을 사용한다. |
| 성공 응답 | 성공 데이터는 불필요한 `data` 래퍼 없이 도메인 객체를 직접 반환한다. |
| 오류 응답 | 모든 오류는 공통 `error` 객체로 감싸 반환한다. |
| 필드명 | JSON 필드명은 `snake_case`를 사용한다. |
| 경로명 | URL 경로는 소문자 `kebab-case`와 복수형 명사를 사용한다. |
| 문자 정규화 | 문자열 앞뒤 공백은 서버에서 제거한다. 빈 문자열은 유효한 값으로 저장하지 않는다. |
| 추가 필드 | 요청 스키마에 정의되지 않은 필드는 거절한다. |
| 애플리케이션 ID | 서비스가 생성하는 리소스 ID는 UUID 문자열을 사용한다. |
| 외부 데이터 ID | 국가법령정보센터 판례일련번호처럼 외부에서 받은 ID는 문자열로 취급한다. |
| 날짜 | 날짜는 `YYYY-MM-DD` 형식을 사용한다. |
| 날짜·시간 | 시각은 UTC ISO 8601 형식으로 반환한다. 예: `2026-10-19T03:30:00Z` |
| 선택 값 | 응답 스키마에 정의된 값이 없으면 빈 문자열이 아니라 `null`을 반환한다. |
| 배열 | 결과가 없으면 `null`이 아니라 빈 배열 `[]`을 반환한다. |
| 불리언 | 불리언 필드는 `is_`, `has_`, `can_` 접두사를 사용한다. |
| 열거형 | enum 값은 소문자 `snake_case`로 고정하고 표시 문구와 분리한다. |
| 문서화 | 모든 공개 API는 OpenAPI에 요청·응답 스키마, 상태 코드, 설명을 등록한다. |
| 보안 | API 키, 내부 경로, 스택 트레이스, 외부 모델의 원본 오류 본문을 응답에 노출하지 않는다. |

### 1.1 HTTP 상태 코드

| 상태 코드 | 사용 기준 |
|---:|---|
| `200 OK` | 조회, 수정, 동기 작업 성공 |
| `201 Created` | 새로운 영구 리소스 생성 성공 |
| `202 Accepted` | 생성 작업을 비동기로 접수했지만 아직 완료되지 않음 |
| `204 No Content` | 삭제 성공, 응답 본문 없음 |
| `400 Bad Request` | ID 형식 오류 등 요청 의미 자체가 잘못됨 |
| `401 Unauthorized` | 인증 정보 없음 또는 만료 |
| `403 Forbidden` | 인증됐지만 해당 리소스 권한 없음 |
| `404 Not Found` | 리소스 또는 상위 리소스를 찾을 수 없음 |
| `409 Conflict` | 중복 생성, 이미 처리 중 등 현재 상태와 충돌 |
| `422 Unprocessable Entity` | 필수 필드, 타입, 길이, 범위 등 스키마 검증 실패 |
| `429 Too Many Requests` | 호출 횟수 제한 초과 |
| `500 Internal Server Error` | 예상하지 못한 서버 오류 |
| `503 Service Unavailable` | 검색기, 데이터 저장소, 외부 AI 모델 등 의존 서비스 사용 불가 |

### 1.2 동기·비동기 작업

- 일반 조회와 짧은 작업은 요청 안에서 완료하고 `200`을 반환한다.
- 통상 3초 이상 걸릴 수 있는 생성 작업은 `202`와 작업 ID를 반환하는 비동기 방식을 우선 검토한다.
- 생성 결과를 저장하는 기능은 같은 입력과 리소스에 대해 중복 호출하지 않고 기존 결과를 재사용할 수 있어야 한다.
- 생성 상태는 `pending`, `processing`, `completed`, `failed`처럼 고정된 enum으로 표현한다.
- AI 생성 실패와 HTTP 통신 실패를 구분한다. 요청 자체는 성공했지만 작업이 실패한 경우 작업 상태에 `failed`를 기록한다.

### 1.3 인증과 소유권

- 인증 적용 후에는 `Authorization: Bearer {access_token}` 헤더를 사용한다.
- 사용자 소유 리소스는 URL이나 요청 본문에 임의의 `user_id`를 받지 않고 인증 토큰에서 사용자를 식별한다.
- 다른 사용자의 검색 기록, 생성 결과, 즐겨찾기는 `404` 또는 `403` 정책 중 프로젝트에서 정한 하나의 방식으로 일관되게 차단한다.

### 1.4 목록 조회와 페이지네이션

- 작은 임시 목록을 제외한 목록 API는 페이지네이션을 제공한다.
- 기본 방식은 커서 페이지네이션으로 통일한다.
- 요청 필드는 `limit`, `cursor`를 사용한다.
- 응답 필드는 `items`, `next_cursor`, `has_next`를 사용한다.
- `limit`에는 기본값과 최대값을 반드시 지정한다.
- 정렬은 `sort` 또는 사전에 합의된 전용 필드로만 허용하고 임의 SQL 필드명을 받지 않는다.

## 2. API 엔드포인트 규칙

### 2.1 기본 원칙

- URL에는 동작이 아니라 리소스를 표현한다.
- 리소스명은 복수형 명사를 사용한다.
- 동작은 HTTP 메서드로 구분한다.
- URL 끝에는 `/`를 붙이지 않는다.
- 경로 파라미터는 `{resource_id}`처럼 리소스명을 포함한다.
- 필터, 검색 조건, 페이지네이션은 쿼리 파라미터를 사용한다.
- 생성하거나 처리할 데이터는 요청 본문으로 전달한다.
- 중첩 경로는 소유 관계나 검색 맥락이 실제로 필요할 때만 사용한다.
- 명령형 동작이 꼭 필요하면 동사 엔드포인트 대신 하위 리소스로 표현한다.

### 2.2 HTTP 메서드 사용

| 목적 | 메서드 | 경로 예시 |
|---|---|---|
| 목록 조회 | `GET` | `/api/v1/precedents` |
| 단건 조회 | `GET` | `/api/v1/precedents/{precedent_id}` |
| 리소스 생성 | `POST` | `/api/v1/precedent-searches` |
| 전체 교체 | `PUT` | `/api/v1/users/me/settings` |
| 일부 수정 | `PATCH` | `/api/v1/documents/{document_id}` |
| 삭제 | `DELETE` | `/api/v1/precedent-favorites/{favorite_id}` |
| 하위 결과 조회 | `GET` | `/api/v1/precedent-searches/{search_id}/results/{precedent_id}` |
| 생성 결과 요청 | `POST` | `/api/v1/precedent-searches/{search_id}/results/{precedent_id}/similarity-explanation` |

### 2.3 엔드포인트 이름 규칙

| 권장 | 금지 | 이유 |
|---|---|---|
| `/precedent-searches` | `/searchPrecedent` | 복수 리소스와 HTTP 메서드로 의미 표현 |
| `/precedent-favorites/{favorite_id}` | `/getFavorite?id=...` | 조회 동사를 URL에 쓰지 않음 |
| `/similarity-explanation` | `/generateSimilarity` | 생성되는 결과 자체를 하위 리소스로 표현 |
| `/full-text` | `/getFullText` | `kebab-case` 명사 사용 |

### 2.4 요청 위치 규칙

| 데이터 | 위치 | 예시 |
|---|---|---|
| 리소스 식별자 | Path | `/precedents/{precedent_id}` |
| 페이지네이션·정렬·필터 | Query | `?limit=20&cursor=...` |
| 생성·수정할 도메인 데이터 | JSON Body | `{ "query": "..." }` |
| 인증 정보 | Header | `Authorization: Bearer ...` |
| 요청 추적 식별자 | Header | `X-Request-ID` |

### 2.5 멱등성과 중복 요청

- `GET`, `PUT`, `DELETE`는 같은 요청을 반복해도 최종 상태가 같아야 한다.
- 동일 생성 요청이 중복될 가능성이 큰 API는 중복 판단 키 또는 `Idempotency-Key` 도입을 검토한다.
- AI 결과가 이미 생성되어 있으면 같은 생성 API를 다시 호출해도 저장된 결과를 반환한다.
- 생성 중인 동일 작업을 다시 요청하면 `409 Conflict` 또는 기존 작업 상태를 반환한다.

## 3. API JSON 필드 규칙

### 3.1 필드 이름

| 데이터 의미 | 규칙 | 예시 |
|---|---|---|
| 식별자 | `{resource}_id` | `search_id`, `favorite_id` |
| 생성 시각 | `created_at` | `2026-10-19T03:30:00Z` |
| 수정 시각 | `updated_at` | `2026-10-19T04:00:00Z` |
| 개수 | `{resource}_count` | `result_count`, `favorite_count` |
| 상태 | `status` | `completed`, `failed` |
| 불리언 | `is_`, `has_`, `can_` | `is_favorited`, `has_next` |
| URL | `_url` 또는 `links` 객체 | `official_source_url`, `links.detail` |
| 배열 | 복수형 명사 | `results`, `matched_chunks` |
| 사용자 입력 원문 | `text` 또는 의미가 명확한 이름 | `query.text` |

### 3.2 필드 설계 원칙

- 하나의 필드는 하나의 의미만 가진다.
- `data`, `value`, `info`처럼 문맥이 불명확한 이름을 단독으로 사용하지 않는다.
- 같은 개념은 모든 API에서 같은 이름과 타입을 사용한다.
- 숫자는 표시용 문자열로 바꾸지 않고 JSON number로 반환한다.
- URL은 문자열, ID는 문자열로 반환하며 둘을 혼용하지 않는다.
- 목록의 개수 필드를 제공한다면 실제 배열 길이와 반드시 일치해야 한다.
- 사용자에게 표시할 문구와 프론트엔드 분기용 enum을 분리한다.
- 생성형 AI의 근거와 결과를 분리한다. 예: `matched_chunks`와 `matched_reason`.
- 검색 점수는 의미가 드러나는 이름을 사용한다. 예: `similarity_score`.
- 내부 모델 프롬프트, 전체 모델 응답, 스택 트레이스는 공개 필드에 포함하지 않는다.

### 3.3 `null`, 생략, 빈 값

- 응답 스키마의 안정성이 필요한 필드는 값이 없어도 키를 유지하고 `null`을 반환한다.
- 배열은 값이 없을 때 `[]`을 반환한다.
- 객체는 값이 없다고 임의로 `{}`를 반환하지 않는다. 필요하면 `null` 또는 명시적인 빈 스키마를 사용한다.
- 요청의 선택 필드는 생략할 수 있다.
- `""`, 공백 문자열, 문자열 `"null"`은 값 없음 표현으로 사용하지 않는다.

### 3.4 요청과 응답 스키마

- 요청 모델과 응답 모델을 분리한다.
- 요청에는 클라이언트가 입력할 수 있는 필드만 둔다.
- `created_at`, 서버 생성 ID, 처리 상태 등은 응답에서만 제공한다.
- 필수 여부, 최소·최대 길이, 숫자 범위, enum을 스키마에 명시한다.
- 모든 enum은 허용 값을 문서에 나열한다.
- 리스트 응답과 상세 응답은 목적에 맞게 분리한다. 목록은 미리보기, 상세는 전체 정보를 제공한다.
- 링크는 `links` 객체에 모아 클라이언트가 경로를 직접 조합하지 않도록 한다.

### 3.5 공통 응답 형태 예시

단건 성공:

```json
{
  "favorite_id": "66a41b91-d97e-4fb1-a892-ed8546e97173",
  "created_at": "2026-10-19T03:30:00Z",
  "is_favorited": true
}
```

목록 성공:

```json
{
  "items": [],
  "next_cursor": null,
  "has_next": false
}
```

상태를 가진 생성 결과:

```json
{
  "status": "completed",
  "model": "gemini-3.5-flash-lite",
  "result": "..."
}
```

### 3.6 호환성 규칙

- 선택 필드 추가는 같은 API 버전에서 허용한다.
- 필수 필드 추가, 필드 삭제, 필드명 변경, 타입 변경, enum 값 삭제는 파괴적 변경으로 본다.
- 파괴적 변경이 필요하면 `/api/v2`처럼 주 버전을 올린다.
- 기존 enum에 새 값을 추가할 때는 프론트엔드의 미처리 상태 대응 여부를 먼저 확인한다.
- 사용하지 않는 필드는 즉시 삭제하지 않고 폐기 예정 표시와 전환 기간을 둔다.

## 4. API 오류 명세 규칙

### 4.1 공통 오류 구조

모든 오류는 아래 구조를 사용한다.

```json
{
  "error": {
    "code": "search_session_not_found",
    "message": "검색 기록이 없거나 만료되었습니다. 다시 검색해 주세요.",
    "field_errors": []
  }
}
```

| 필드 | 타입 | 규칙 |
|---|---|---|
| `error.code` | string enum | 프론트엔드 분기용 안정적인 코드 |
| `error.message` | string | 사용자가 이해할 수 있는 한국어 안내 문구 |
| `error.field_errors` | array | 필드 검증 오류 목록, 없으면 `[]` |

필드 검증 오류:

```json
{
  "error": {
    "code": "invalid_request",
    "message": "요청 값이 올바르지 않습니다.",
    "field_errors": [
      {
        "location": ["body", "query"],
        "message": "String should have at least 2 characters",
        "type": "string_too_short"
      }
    ]
  }
}
```

### 4.2 오류 코드 작성 규칙

- 오류 코드는 소문자 `snake_case`를 사용한다.
- 오류 코드는 사람에게 보여줄 문장이 아니라 변경되지 않는 프로그램 식별자다.
- 같은 원인의 오류는 엔드포인트가 달라도 같은 코드를 사용한다.
- 리소스 없음은 `{resource}_not_found` 형식을 사용한다.
- 외부 의존 서비스 장애는 `{service}_unavailable` 형식을 사용한다.
- 현재 상태 충돌은 처리 내용을 드러내는 코드를 사용한다. 예: `similarity_explanation_in_progress`.
- 프론트엔드는 `message` 문자열이 아니라 `code`를 기준으로 분기한다.

### 4.3 오류 응답 원칙

- 예상 가능한 도메인 오류는 반드시 명시적인 HTTP 상태와 오류 코드로 변환한다.
- `404`에서 어떤 사용자의 리소스인지 등 민감한 존재 여부를 과도하게 노출하지 않는다.
- `500` 응답에는 내부 예외명, 파일 경로, 스택 트레이스, API 키를 포함하지 않는다.
- 서버 로그에는 원인 분석 정보를 남기되 개인정보와 비밀 값은 마스킹한다.
- 외부 AI API의 응답 본문을 그대로 클라이언트에 전달하지 않는다.
- 재시도 가능한 오류는 `429` 또는 `503`을 사용하고 필요하면 `Retry-After` 헤더를 제공한다.
- 검증 오류는 어느 필드가 잘못됐는지 `field_errors.location`으로 제공한다.

### 4.4 기본 오류 코드 형태

| 상황 | HTTP | 코드 예시 |
|---|---:|---|
| 요청 스키마 오류 | 422 | `invalid_request` |
| ID 형식 오류 | 400 | `invalid_precedent_id` |
| 리소스 없음 | 404 | `precedent_not_found` |
| 인증 필요 | 401 | `authentication_required` |
| 접근 권한 없음 | 403 | `forbidden` |
| 중복 또는 처리 중 | 409 | `resource_conflict` |
| 호출 제한 초과 | 429 | `rate_limit_exceeded` |
| 검색 백엔드 장애 | 503 | `search_backend_unavailable` |
| AI 생성 서비스 장애 | 503 | `generation_service_unavailable` |
| 예상하지 못한 오류 | 500 | `internal_server_error` |

## 5. API 구현 전 확인표

- 리소스와 소유 관계가 URL에 명확히 표현됐는가?
- URL에 `get`, `create`, `generate` 같은 불필요한 동사가 들어가지 않았는가?
- 적절한 HTTP 메서드와 상태 코드를 사용했는가?
- 요청 모델과 응답 모델이 분리됐는가?
- 필드명이 기존 API의 같은 개념과 일치하는가?
- 필수값, 길이, 범위, enum 검증이 정의됐는가?
- 값 없음과 빈 배열의 표현이 일관적인가?
- 성공·실패·처리 중 상태가 명확한가?
- 모든 예상 오류가 공통 오류 형식으로 변환되는가?
- 오류 코드만으로 프론트엔드가 안정적으로 분기할 수 있는가?
- 인증된 사용자가 자신의 리소스만 조회·수정할 수 있는가?
- 중복 요청 시 결과가 중복 생성되지 않는가?
- 오래 걸리는 작업에 비동기 처리가 필요한가?
- 공개 응답에 비밀 값이나 내부 구현 정보가 포함되지 않는가?
- OpenAPI 문서와 테스트가 함께 작성됐는가?
