# Certified Letter Generation Design v0.1

This document records the provisional AlphaLawVA certified-letter contract and
workflow. It is an implementation baseline, not a final legal, model, retrieval,
database, or infrastructure decision. Changes to the public schema, prompt,
retrieval behavior, or evidence policy require tests and an explicit review.

## Scope

The first alpha supports residential lease certified-letter drafts for:

- deposit return after lease termination;
- lease termination notice;
- repair-performance requests; and
- rent or management-fee disputes.

The output is a draft and does not replace professional legal advice. The
generation workflow must not promise legal effect, dispute success, or a
particular court outcome.

## Evidence Policy

- Only retrieved statute evidence may be provided to the letter-drafting model.
- Similar precedents are retrieved and reranked separately for user reference.
- Precedent content must not be inserted into the certified-letter body or used
  as a body citation in this version.
- Every statute citation in the body must map to retrieved statute evidence.
- Weak, missing, or contradictory evidence produces an explicit outcome instead
  of an unsupported letter.
- Retrieval source metadata must be preserved through the workflow.

This separation is enforced by the generator interface, which accepts normalized
facts and selected statute evidence but no precedent collection.

## Public API

### Create a draft job

```http
POST /api/v1/certified-letter-drafts
Idempotency-Key: <client-generated-key>
Content-Type: application/json
```

The `Idempotency-Key` header is optional in v0.1 but recommended. Reusing a key
for the same request returns the existing job. Reusing it for a different
request is a conflict. A reused key returns the existing job's current status;
the first submission normally returns `queued`.

Example request:

```json
{
  "purpose": "계약 종료 후 보증금을 반환받고 싶습니다.",
  "facts": "계약은 종료되었고 임대인에게 보증금 반환을 요청했지만 아직 받지 못했습니다.",
  "requested_deadline": "2026-11-15",
  "contract": {
    "contract_type": "jeonse",
    "property": {
      "address": "서울특별시 중구 세종대로 1",
      "detail_address": "101동 101호",
      "building_type": "apartment"
    },
    "period": {
      "start_date": "2024-10-01",
      "end_date": "2026-09-30"
    },
    "payment": {
      "deposit": 100000000,
      "monthly_rent": null
    },
    "lessor": {
      "name": "홍길동",
      "address": "서울특별시 종로구 사직로 1",
      "detail_address": null,
      "phone_number": null
    },
    "lessee": {
      "name": "김민지",
      "address": "서울특별시 중구 세종대로 1",
      "detail_address": "101동 101호",
      "phone_number": null
    }
  }
}
```

Rules:

- `purpose` and `facts` remain separate; the server does not collapse them into
  one opaque query.
- `contract_type` is `jeonse` or `monthly_rent`.
- A `jeonse` contract must not contain `monthly_rent`.
- A `monthly_rent` contract must contain a positive `monthly_rent` value.
- Dates use `YYYY-MM-DD`; money is an integer number of Korean won.
- `requested_deadline` is optional. When absent, the workflow may suggest the
  relative deadline "수령 후 7일 이내". That phrase is a generated strategy,
  not an asserted historical fact, and must be disclosed in `warnings`.
- User identity is not accepted in the request body. Authentication will supply
  ownership when it is introduced.
- Unknown request fields are rejected.

Accepted response:

```json
{
  "draft_id": "12345678-1234-5678-1234-567812345678",
  "status": "queued",
  "created_at": "2026-10-22T05:30:00Z"
}
```

The endpoint returns `202 Accepted`. Schema validation failures return `422` in
the common API error format. Failure to accept a new job returns `503`.

### Read a draft job

```http
GET /api/v1/certified-letter-drafts/{draft_id}
```

Job status values:

- `queued`
- `processing`
- `completed`
- `failed`

Domain outcome values for a completed job:

- `draft_ready`
- `needs_information`
- `out_of_scope`
- `insufficient_evidence`

Job status describes execution. Outcome describes the legal-generation result.
A functioning retriever that finds insufficient evidence is therefore a
completed job with `insufficient_evidence`, not a system failure.

Completed draft example:

```json
{
  "draft_id": "12345678-1234-5678-1234-567812345678",
  "status": "completed",
  "outcome": "draft_ready",
  "result": {
    "document": {
      "title": "임대차보증금 반환 촉구서",
      "sender": {
        "name": "김민지",
        "address": "서울특별시 중구 세종대로 1",
        "detail_address": "101동 101호"
      },
      "recipient": {
        "name": "홍길동",
        "address": "서울특별시 종로구 사직로 1",
        "detail_address": null
      },
      "body": "내용증명 초안 본문"
    },
    "strategy_summary": "보증금 반환 의무와 이행 기한을 명확히 통지합니다.",
    "follow_up_actions": [
      {
        "order": 1,
        "action": "발송 전 사실관계와 주소를 다시 확인합니다."
      }
    ],
    "statute_evidence": [],
    "similar_precedents": [],
    "warnings": [
      "이 문서는 법률 검토 전 초안입니다."
    ]
  },
  "missing_information": [],
  "error": null,
  "created_at": "2026-10-22T05:30:00Z",
  "updated_at": "2026-10-22T05:30:12Z",
  "completed_at": "2026-10-22T05:30:12Z"
}
```

The public response does not expose provider API errors, stack traces, local
paths, user agents, hard-coded document IDs, or unverified model metadata.
Prompt, model, retrieval, and reranker versions belong to an internal generation
trace and evaluation record unless a later product requirement exposes them.

## LangGraph Workflow

The certified-letter workflow is a bounded state graph, not an open-ended ReAct
loop.

```text
normalize_input
  -> check_scope
  -> check_required_information
  -> build_search_queries
  -> [retrieve_statutes -> rerank_statutes]
     [retrieve_precedents -> rerank_precedents]
  -> check_evidence
  -> generate_draft
  -> validate_draft
  -> repair_draft (at most once)
  -> finalize_result
```

Early outcomes:

- an unrelated request ends as `out_of_scope`;
- missing delivery-critical facts end as `needs_information`;
- a successful search with inadequate statute evidence ends as
  `insufficient_evidence`;
- dependency failures end the job as `failed`.

## Retrieval and Reranking

The existing `BgeM3StatuteRetriever` and `BgeM3PrecedentRetriever` remain the
dense-retrieval baseline. The workflow must not duplicate their vector-store
loading logic.

Initial comparison configuration:

- statutes: retrieve up to 20 candidates, deduplicate by statute article,
  rerank, select up to 5;
- precedents: retrieve up to 20 precedent-level candidates, rerank, select
  3 to 5.

The reranker is selected only after a same-dataset comparison against dense
retrieval. Statutes and precedents may use different rerankers. The selected
implementation must remain replaceable through an explicit interface.

Stage 2 runtime configuration uses the following provisional alpha choices:

- generation: `gemini-3.5-flash-lite` through structured JSON output, with
  Claude remaining the intended production candidate;
- statute ranking: the approved BGE-M3 dense score until a statute-specific
  reranker comparison is completed;
- precedent ranking: local `bge-reranker-v2-m3`, selected for the alpha because
  the existing ten-scenario comparison improved Precision@3 from 0.8000 to
  0.8333 and MRR@5 from 0.7450 to 0.7750, while its latency remains acceptable
  for the slower certified-letter workflow;
- model loading: one shared BGE-M3 encoder instance for statute and precedent
  queries, with all embedding and reranker models loaded lazily.

The Gemini adapter records model, prompt version, token usage, retries, result
status, and estimated cost without storing the raw prompt, request, or draft.
Setting `CERTIFIED_LETTER_GENERATION_ENABLED=false` or omitting
`GEMINI_API_KEY` keeps the endpoint unavailable instead of returning a mock
draft.

Stage 4 completes the existing-retriever integration without an LLM query
expander. Search queries combine the redacted purpose and facts with contract
type, period, deposit, and monthly rent. Names, delivery addresses, detailed
addresses, and phone numbers are removed. Results from multiple deterministic
queries are deduplicated by statute article or precedent ID; all matched chunks
for a repeated precedent are retained for a later replaceable reranker.

## Draft Validation

Before returning a draft, deterministic checks validate:

- sender and recipient direction;
- names, addresses, dates, and monetary amounts against the request;
- every statute name and article against selected evidence;
- required title, demand, deadline or deadline placeholder, and draft warning;
- absence of internal prompts, provider errors, and unsupported success claims.

Fixable validation failures may trigger one repair call with the same statute
evidence. A second failure does not return a fallback letter as a success.

## Alpha Storage and Execution

The first implementation uses an in-memory job store and FastAPI background
execution. The store has a TTL and an injectable interface. This intentionally
does not provide restart durability or cross-process coordination; a persistent
store and production job queue are later infrastructure decisions.

## Privacy and Logging

Certified-letter requests contain names, addresses, phone numbers, contract
dates, and payment amounts. Application logs must not contain the raw request,
full draft, prompt, or provider response. Logs may contain the draft UUID,
status transition, duration, dependency category, and redacted error class.

## Evaluation Gates

Stage 3 adds the versioned synthetic dataset and offline evaluator described in
`docs/CERTIFIED_LETTER_EVALUATION.md`. Generated drafts and raw responses remain
under gitignored `local_data/`; only synthetic inputs and configuration
templates are version controlled.

Before enabling a model or reranker by default, record:

- evaluation dataset and version;
- dense baseline and reranked retrieval metrics;
- statute citation precision;
- contract fact consistency;
- precedent-to-body separation checks;
- latency and model-call count; and
- prompt, model, embedding, chunking, and retrieval versions.

The alpha safety gates are zero unsupported statute citations, zero invented
contract amounts or dates, zero precedent citations in the body, and zero
system failures disguised as successful drafts.

## Deferred Scope

- authentication and per-user authorization;
- MySQL persistence;
- Redis, Celery, or another distributed job queue;
- unrestricted ReAct loops;
- final Neo4j integration;
- precedent quotations in the letter body;
- final model or reranker selection without comparison results; and
- a full frontend redesign.
