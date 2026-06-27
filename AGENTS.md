# AGENTS.md

AlphaLawVA is an AI real-estate legal partner web app for users in their 20s
and 30s with little real-estate transaction experience. This file is the entry
point for AI coding agents working in this repository.

## Project Identity

- Previous name: SuperLawVA
- Current name: AlphaLawVA
- Repository/root directory name: AlphaLawVA
- Project type: AI real-estate legal partner web app
- Target users: people in their 20s and 30s with little real-estate transaction experience

Use `AlphaLawVA` for the service, repository, and root directory name. Use
lowercase identifiers such as `alphalawva` only where tools require lowercase
names, such as package names, Docker image names, container names, or database
names.

Treat SuperLawVA as a legacy name only.

## Required Reading

Before working, read the document that matches the task:

1. `docs/RULES.md`: coding principles, change safety, security, testing, verification, reporting, branching, and commit rules
2. `docs/ARCHITECTURE.md`: service scope, directory structure, service boundaries, data design, data collection, and agent architecture
3. `docs/RAG_AND_EVALUATION.md`: legal AI output rules, RAG, prompts, model and embedding comparison, and evaluation rules
4. `docs/VERSION_UPGRADE.md`: SuperLawVA to AlphaLawVA upgrade context, changeable areas, fixed rules, and thread guide

If documents conflict, follow the more specific rule. If the conflict affects
architecture, security, legal output, data deletion, public API behavior,
prompt behavior, retrieval behavior, model behavior, or database schema, ask
the user before editing.

## Stable Rules

- Do not treat provisional architecture choices as final decisions.
- Do not fabricate statutes, precedents, case numbers, article numbers, legal effects, source metadata, or legal conclusions.
- Do not change public behavior outside the requested scope.
- Do not add unnecessary dependencies.
- Do not commit `.env` or expose secrets in logs, code, tests, screenshots, or documentation.
- Do not ignore Windows and macOS compatibility.
- Do not weaken tests, hide failing cases with mocks, or remove coverage without a clear explanation.

## Basic Verification

After code changes, run checks for the changed area when the project tooling is
available.

Backend/Python:

```bash
ruff check .
```

Frontend:

```bash
cd frontend
npm run lint
npm run build
```

If the frontend uses pnpm or yarn instead of npm, use the matching package
manager command and lockfile. If a check cannot be run, report why instead of
claiming that it passed.

## Completion Report Format

When finishing a coding task, report:

1. Changed files
2. Core changes, preferably as a concise diff-style summary
3. Behavior changes, including changed defaults, branches, prompts, schemas, or return shapes
4. Tests and checks run, changed, deleted, or skipped
5. Possible side effects
6. Rollback method with git commands
