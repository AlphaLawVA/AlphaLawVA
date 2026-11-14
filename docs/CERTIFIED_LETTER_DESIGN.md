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
- A draft may omit statute citations when none of the retrieved candidates is
  directly relevant. Every citation that is included must map to retrieved
  statute evidence.
- Missing statute evidence does not prevent a fact-based draft. In that case the
  body contains no statute citation, `statute_evidence` is empty, and the result
  carries an explicit review warning.
- Retrieved statutes are returned to the user only when the final body actually
  cites them.
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
  "sender_role": "lessee",
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

- `sender_role` is required and is either `lessee` or `lessor`. The server
  deterministically assigns that party as the sender and the other contract
  party as the recipient before the drafting model is called.
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
- `insufficient_evidence` (reserved for backward compatibility; missing statute
  search results alone no longer produce this outcome)

Job status describes execution. Outcome describes the legal-generation result.
`insufficient_evidence` remains readable for previously stored jobs, but the
current workflow does not use an empty statute search alone to produce it.

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
  -> check_scope (Gemini: in_scope / needs_information / out_of_scope)
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

- Gemini receives only `purpose` and `facts`; an unrelated request ends as
  `out_of_scope` before retrieval;
- a semantically vague or mixed request ends as `needs_information` with a
  fixed server-side description of whether `purpose` or `facts` is needed;
- code checks names and delivery addresses after an `in_scope` decision and
  missing delivery-critical values also end as `needs_information`;
- missing statute evidence continues to drafting without a legal citation and
  adds a deterministic review warning;
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

- scope classification: `gemini-3.5-flash-lite` with temperature 0, minimal
  thinking, a 64-token limit, and a three-value structured JSON decision;
- generation: the same `gemini-3.5-flash-lite` adapter and shared session cost
  guard, with Claude remaining the intended production candidate;
- statute ranking: retrieve 10 BGE-M3 candidates, keep candidates with dense
  similarity at least 0.35, rerank them with local `kure-reranker-nano`, and
  return at most 3 statutes;
- precedent ranking: search only the versioned 691-case real-estate allowlist,
  apply local `kure-reranker-nano` without a dense similarity cutoff, and return
  at most 3 similar precedents;
- model loading: one shared BGE-M3 encoder instance for statute and precedent
  queries, while identical KURE configurations share one lazily loaded model
  across contract review, special-clause generation, and certified letters.

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

Stage 5 keeps the runtime replaceable while using the team-selected KURE model
for both evidence types. The runtime exposes
independent `CERTIFIED_LETTER_STATUTE_RERANKER` and
`CERTIFIED_LETTER_PRECEDENT_RERANKER` selections. `dense` uses the retrieval
score baseline; any other safe model name resolves to a local directory under
`local_data/models/rerankers/`, or an explicit role-specific `*_DIR` may be
provided. This allows a future same-dataset experiment to replace either
cross-encoder without changing graph or API code. If reranking fails at
runtime, the workflow falls back to the already filtered dense order.

Stage 6 prompt `certified_letter_v0_5` incorporates the useful drafting
structure from SuperLawVA's legacy `generate_letter` prompt while removing
fabricated experience, unsupported success-rate claims, guaranteed-effect
language, ungrounded general-law fallback, and successful fallback letters.
The detailed comparison is recorded in
`docs/CERTIFIED_LETTER_PROMPT_CHANGES.md`.

Each returned similar precedent retains the real matched vector-search chunks.
When its `final_cases` source record is available, the response also contains
the stored generated summary, easy summary, and in-product full-text sections.
The UI must render those fields directly rather than relying on demo-only
properties. A missing source record leaves those optional presentation fields
empty without turning a successful certified-letter draft into a failed job.

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

### Running the demo

The demo is an API-served page, not a standalone HTML file. Start FastAPI and
open `http://127.0.0.1:8000/demo/certified-letter`. Opening
`api/static/certified_letter_demo.html` with a `file://` URL cannot reach the
relative `/api/v1` endpoints and is intentionally blocked with an explanation.

When code runs in a separate Git worktree while secrets and local vector data
remain in the saved project checkout, point the process at that checkout's
environment file. Relative data paths are then resolved from the environment
file's directory instead of the worktree source directory.

macOS or Linux:

```bash
ALPHALAWVA_ENV_FILE=/absolute/path/to/AlphaLawVA/.env \
  python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

PowerShell:

```powershell
$env:ALPHALAWVA_ENV_FILE = "C:\\absolute\\path\\to\\AlphaLawVA\\.env"
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

### Current Next.js integration status

The Next.js certified-letter flow is connected to the live FastAPI draft API.
It passes the user-confirmed contract snapshot, collects only the sender role,
purpose, confirmed facts, and optional deadline, then polls the in-memory job
until completion. The result screen renders the generated document, strategy,
follow-up actions, warnings, cited statute evidence, and similar precedents.

The following parts remain demo or alpha-only behavior:

- contract upload, OCR progress, and extracted contract values use the frontend
  sample snapshot until the separate OCR branch is integrated;
- draft jobs are stored in process memory and are lost when the API restarts;
- generated results are not yet saved to the user's MySQL result storage; and
- legal output remains a review-required draft rather than a send-ready legal
  conclusion.

Statute runtime data is local-only and excluded from Git. Keep the matching
data set in these standard locations:

- source article chunks: `data/statutes/chunks/`;
- BGE-M3 ChromaDB: `data/statutes/vectorstores/bge_m3/`; and
- embedding model cache: `data/statutes/models/`.

`data/statutes/chunks/` is the validated source corpus used to build the
ChromaDB; the API performs retrieval from the vector store. Configure
`CERTIFIED_LETTER_STATUTE_DB_DIR` when the vector store is outside the runtime
project root. The public response includes only selected statute evidence that
the final draft actually cites, so a successful fact-based draft may still
return an empty `statute_evidence` list.

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
