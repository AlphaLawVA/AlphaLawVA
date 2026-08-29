# RULES.md

This document defines AlphaLawVA's coding, security, testing, verification, and
reporting rules. Follow these rules unless the user explicitly changes them.

## Coding Principles

### Python File Header

Add a header in the following format at the top of every newly created Python
code file.

```python
# normalize_statute_xmls.py
"""
Description: 국가법령정보 공동활용 API로 수집한 XML 원본을
조·항·호·목 계층을 보존한 JSON으로 정규화하고 데이터 무결성을 검증한다.
Author: ooheunsu
Date: 2026-08-17
Before:
    - 선정된 법령의 XML 원본이 있지만 DB 구축용 계층형 구조로 정리되지 않은 상태.

After:
    - data/statutes/filtered_hierarchical_jsons/에 법령별 JSON이, data/statutes/manifests/normalization_v01.json에 검증 결과가 생성.
"""
```

- On the first line, write the actual filename in the `# filename.py` format.
- In `Description`, describe the file's purpose and main processing in no more
  than two sentences.
- In `Author`, write the nickname of the team member who created the file. For
  example, use `ooheunsu` for a file created by `ooheunsu`.
- In `Date`, write the file's creation date in `YYYY-MM-DD` format. Do not
  change it when modifying the file later.
- In `Before`, describe the required input or data state before execution.
- In `After`, describe the result created or changed after execution.
- Write the contents of `Before` and `After` as indented hyphen list items.
  Keep each item to one direct and concise line.

### 1. Think Before Coding

- Explain the approach before making non-trivial code changes.
- If requirements are unclear, ask instead of guessing.
- When facts are uncertain, inspect files, logs, docs, or command output and cite the source.

### 2. Simplicity First

- Do not add features that were not requested.
- Avoid premature abstraction.
- Prefer clear, direct code over clever code.

### 3. Surgical Changes

- Modify only the files needed for the requested task.
- Do not touch unrelated files.
- Preserve existing patterns unless there is a concrete reason to change them.

### 4. Goal-Driven Execution

- State the completion condition for each task.
- Keep work tied to the requested outcome.
- Do not expand the scope silently.

## Pre-Implementation Checklist

Before non-trivial implementation:

1. Inspect the current structure.
2. Find related files and call sites.
3. Check existing patterns.
4. Explain the expected impact range.
5. Present a short implementation plan before editing.

## Task Workflow

For coding tasks, use this loop unless the user asks for a different process:

1. Find relevant files and existing patterns.
2. List the short approach before non-trivial edits.
3. Make the smallest practical change.
4. Run the relevant checks and tests.
5. Summarize changed files, behavior changes, risks, and rollback.

For large work, split the task into stages such as design, minimal
implementation, tests, and refactor. Do not ask one thread to handle unrelated
domains at once.

## Change Safety Rules

Be especially careful with changes that can silently break other code even when
builds or tests pass.

Before changing any of the following, search for usages with `rg` and check the
impact:

- Function signatures
- Return types or response shapes
- Branch conditions
- Default values
- Shared utility functions
- Environment variable names
- Dependency versions in `package.json`, lockfiles, or `requirements.txt`
- Public API request or response schemas
- Prompt templates used by production or evaluation code

Do not change dependency versions, environment variable names, default values,
public API behavior, or production prompts unless the task explicitly requires
it or the user approves the reason.

If a shared function or common module is changed, list the known callers and
mention the regression risk in the completion report.

## Refactoring Rules

Refactoring must be incremental and behavior-preserving unless the user
explicitly requests a behavior change.

- Do not combine refactoring with unrelated feature work.
- Preserve existing API contracts when possible.
- If behavior changes, document the reason and add or run relevant verification.
- Record discovered technical debt as TODOs or in documentation instead of expanding the current task silently.
- Avoid large architecture changes during narrow bug fixes or version-upgrade steps.

Do not use refactoring as a reason to silently change:

- Public API behavior
- Request or response schemas
- Function signatures
- Return shapes
- Default values
- Environment variable names
- Prompt behavior
- Retrieval behavior
- Database schema assumptions
- Model or embedding choices

## Testing Rules

- Do not rewrite tests just to match the new implementation.
- Do not delete failing tests by calling them outdated unless the user approves the reason.
- If tests are modified or deleted, explain exactly why.
- Review test diffs as carefully as source-code diffs.
- Avoid excessive mocking for core business logic.
- For authentication, authorization, payment-like flows, file upload, SQL, and secret handling, include at least one realistic manual or integration-level check when practical.
- For legal AI outputs, include retrieval-grounding or source-metadata checks when practical.

## UI QA Rules

For frontend or visual UI work:

- Capture or inspect the affected screen before the change when practical.
- Capture or inspect the same screen after the change.
- Also check nearby screens that should not have changed.
- Prefer browser-based verification for layout, responsive behavior, and visual regressions.

If the frontend grows large enough, consider Playwright screenshot assertions,
Percy, Chromatic, or a similar visual regression workflow.

## Security-Sensitive Areas

Do not merge AI-generated changes in the following areas without explicit
review:

- Authorization and permission checks
- Authentication/session handling
- SQL or query construction
- File upload and file parsing
- Secret handling
- Debug logging
- AI output that is shown directly to users or used in legal-document generation
- Prompt construction that includes user-controlled input

For these areas, report possible bypass scenarios, secret exposure risks,
prompt-injection risks, and manual QA steps.

## Environment Variables

All API keys and secrets must be managed through a local `.env` file only.
Create local `.env` files from `.env.example`.

- Never write API keys, passwords, or secrets directly in code.
- Do not commit `.env`.
- Keep `.env.example` committed with placeholder values only.
- Do not expose secrets in logs, screenshots, tests, documentation examples, or error messages.

Required keys:

- `LAW_API_KEY`: Korean Ministry of Government Legislation Open API for statutes and precedents
- `ANTHROPIC_API_KEY`: Claude API
- `OPENAI_API_KEY`: OpenAI API
- `MYSQL_PASSWORD`: MySQL
- `NEO4J_PASSWORD`: Neo4j

`LAW_API_KEY` is the current project convention. If the team decides to rename
it to a more explicit name such as `OPEN_LAW_API_KEY` or `MOLEG_API_KEY`, do
that before implementation or update every usage in one intentional change.

## OS Compatibility Rules

Developers use both Windows and macOS.

- Use `os.path.join()` or `pathlib` in Python instead of hard-coded path separators.
- Avoid platform-specific shell assumptions in scripts when possible.
- Be careful with line endings. Prefer LF in repository files unless a tool requires otherwise.
- Docker should be used to unify the development environment when the project setup is ready.

## Required Verification

After code changes, run the relevant checks whenever the project has the
required tooling available.

Backend and Python:

```bash
ruff check .
```

Frontend:

```bash
cd frontend
npm run lint
npm run build
```

If the frontend uses pnpm or yarn instead of npm, use the matching
package-manager commands and lockfile.

Also run relevant tests for the changed area.

If a check cannot be run because the project setup is incomplete, dependencies
are missing, or the command does not exist yet, report that clearly instead of
pretending it passed.

## Completion Report Format

When finishing a coding task, report:

1. Changed files
2. Core changes, preferably as a concise diff-style summary
3. Behavior changes, including changed defaults, branches, prompts, schemas, or return shapes
4. Tests and checks run, changed, deleted, or skipped
5. Possible side effects
6. Rollback method with git commands

Example rollback command:

```bash
git restore <changed-file>
```

Use a different rollback command if the actual change requires it.

## Change Documentation

For non-trivial changes or before push, create or update `CHANGES.md` if the
user asks for a push-ready summary.

Include a table covering:

- Newly added features
- Existing behavior that changed
- Function signature changes
- Return shape changes
- Branch condition changes
- Default value changes
- Prompt changes
- Retrieval behavior changes
- Regression risk scenarios
- Manual QA method
- Deleted or modified tests and the reason

If "existing behavior changed" appears to be empty, re-read the diff and verify
function signatures, return shapes, branch conditions, default values, prompts,
schemas, and environment variables again before reporting that there were no
behavior changes.

## Branching and Commit Guide

Use focused branches for meaningful units of work.

- Prefer one feature, bugfix, or experiment per branch.
- Avoid mixing frontend, backend, ML, and data-pipeline changes in one branch unless the task genuinely requires it.
- Use smaller branches for risky areas such as LangGraph agent design, Neo4j schema changes, and data-ingestion changes.
- Keep review-only and implementation work separate when possible.

Recommended branch prefixes:

- `feat/`: user-facing or service feature work
- `fix/`: bug fixes
- `experiment/`: model, retrieval, embedding, evaluation, or prompt experiments
- `refactor/`: behavior-preserving structure changes
- `infra/`: Docker, CI, deployment, or infrastructure changes
- `docs/`: documentation-only changes
- `chore/`: maintenance work that does not fit the above categories

Use Korean or English commit messages consistently within the team. Prefer
concise messages that state what changed.

Before creating a commit, summarize the intended commit scope and ask the user
for explicit approval. Do not create the commit until the user approves it.

## Do Not

- Do not invent database schemas as final decisions when they are still experimental.
- Do not lock in a model, embedding model, or vector DB strategy without comparison results.
- Do not hard-code `text-embedding-3-large` as a final embedding decision before comparison results are available.
- Do not fabricate legal sources, statute article numbers, precedent identifiers, or legal conclusions.
- Do not change public behavior outside the requested scope.
- Do not add unnecessary dependencies.
- Do not call external providers directly from unrelated layers for convenience.
- Do not make large refactors during a narrow bug fix or version-upgrade step.
- Do not ignore Windows and macOS compatibility.
- Do not commit `.env` or expose secrets in logs, code, tests, screenshots, or documentation.
- Do not weaken tests, hide failing cases with mocks, or remove coverage without a clear explanation.
