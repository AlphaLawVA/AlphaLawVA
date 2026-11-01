# User Result Persistence

This document records the first-development implementation for authenticated
user result storage. It is an implementation baseline, not a final production
architecture decision.

## Scope

Implemented:

- Supabase email/password authentication and browser sessions
- Supabase access-token verification in FastAPI
- Certified-letter result save, list, and detail APIs
- Precedent favorite create, list, detail, explanation update, and delete APIs
- My Page list and detail UI for the two implemented user features

Pending:

- Contract-review result persistence
- Special-clause result persistence
- Account deletion, email change, and password reset UX
- Production database migrations, backups, monitoring, and retention policy

## Security Boundary

The frontend sends `Authorization: Bearer <Supabase access token>`. FastAPI asks
Supabase Auth to validate that token and reads the verified user UUID. Result
APIs never accept a `user_id` field from the browser.

Every MySQL read, update, and delete for private data includes the verified user
UUID. A missing record and another user's record both return `404`, avoiding
confirmation that another user's private record exists.

Only the Supabase publishable key is used by the browser and token verifier.
Supabase secret or service-role keys must not be placed in frontend variables,
committed files, screenshots, or logs.

## Tables

### `certified_letter_results`

- One row per user and generated draft (`UNIQUE(user_id, draft_id)`)
- Columns for result ID, user ID, draft ID, title, and timestamps
- Full validated `CertifiedLetterDraftResult` stored as JSON

The save endpoint accepts only `draft_id`. FastAPI loads the completed job and
stores its server-generated result instead of trusting result JSON supplied by
the browser.

### `precedent_favorites`

- One row per user, search, and precedent
- Original search question stored with the selected precedent
- Full retrieval result and matched chunks stored as JSON
- Similarity-explanation state stored separately so it can be updated

This deliberately allows the same precedent to be saved from different user
questions, because each question represents a distinct search context.

## Local Development

1. Start Docker MySQL with `compose.mysql.yml`.
2. Set `SNAPSHOT_STORE_BACKEND=mysql` and the `MYSQL_*` values in the untracked
   root `.env`.
3. Keep the Supabase public URL and publishable key in
   `frontend/.env.local`. FastAPI reuses those public local-development values;
   deployment should set `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`
   explicitly.
4. Start FastAPI and Next.js.

The MySQL adapters create missing development tables on first use. Do not use
that convenience as a substitute for reviewed migrations in a real service.

## API Summary

All routes below require a valid Supabase bearer token.

- `POST /api/v1/certified-letter-results`
- `GET /api/v1/certified-letter-results`
- `GET /api/v1/certified-letter-results/{result_id}`
- `POST /api/v1/precedent-favorites`
- `GET /api/v1/precedent-favorites`
- `GET /api/v1/precedent-favorites/{favorite_id}`
- `POST /api/v1/precedent-favorites/{favorite_id}/similarity-explanation`
- `DELETE /api/v1/precedent-favorites/{favorite_id}`

## Known First-Development Tradeoffs

- Certified-letter generation remains available before login. The generated
  draft uses a random UUID, and authentication is enforced when it is saved.
  A production design should bind the generation job to a user or issue a
  one-time ownership proof instead of relying only on an unguessable draft ID.
- Generated payloads can contain personal contract information. Production
  work still needs an explicit masking/encryption, retention, deletion, backup,
  and access-audit policy.
- My Page currently shows placeholders for teammate-owned contract-review and
  special-clause results until their final response shapes are integrated.
