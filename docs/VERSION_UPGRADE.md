# VERSION_UPGRADE.md

This document defines the operating guide for upgrading SuperLawVA to
AlphaLawVA.

## Upgrade Context

SuperLawVA was developed last year by separate frontend, backend, and ML teams.
This upgrade will be handled by a small ML-centered team of two people covering
frontend, backend, and ML together.

Because the team is small, prefer simple, maintainable, and explicit
implementations over complex abstractions.

## Naming Policy

- Service display name: AlphaLawVA
- Repository/root directory name: AlphaLawVA
- Lowercase tool identifier when needed: `alphalawva`
- Previous name: SuperLawVA

Use `AlphaLawVA` for new user-facing product text, repository references, and
root directory references. Use `alphalawva` only where tools require lowercase
identifiers, such as package names, Docker image names, container names, or
database names.

Treat remaining SuperLawVA names in old code or documents as legacy context.
Renaming legacy references should be handled as a separate, intentional task.

## Changeable Areas

The following may change during implementation, experiments, and evaluation:

- Database schema
- Model choice
- Embedding model
- RAG strategy
- Evaluation setup
- Frontend screen structure
- Lower-level directory structure
- Prompt design
- Chunking strategy
- Retrieval strategy

Treat these as current design assumptions. Do not hard-code them as final
decisions without comparison results or explicit user approval.

## Fixed Rules

The following are project rules unless the user explicitly changes them:

- Coding principles
- Reporting rules
- OS compatibility
- Security rules
- Change safety rules
- Legal AI output rules
- Secret handling rules

## Codex Thread Guide

Split work into separate threads by feature or domain. Do not mix unrelated
tasks in one thread.

Suggested initial thread split:

1. Precedent data collection
2. Statute data collection
3. Preprocessing and chunking
4. Neo4j setup
5. ChromaDB setup
6. FastAPI development
7. LangGraph agent
8. Next.js frontend

This split is an operational guide, not a fixed architecture rule. Add, merge,
or rename threads when the project scope changes.

Use thread-specific instructions for special workflows such as review-only,
bugfix-only, or push-only threads. Keep those instructions narrow so they do
not leak into unrelated work.

## Work Style

- Split large work into design, minimal implementation, tests, and refactor stages.
- Do not bundle data collection, RAG, API, frontend, and infrastructure changes together unless the task genuinely requires it.
- Record model, embedding, chunking, prompt, and retrieval changes with evaluation context.
- Record technical debt discovered during the upgrade as TODOs or documentation instead of silently expanding the current task.

## Excluded Scope

Unless explicitly requested by the user, do not mix the following into upgrade
work:

- Large architecture rewrites
- MSA conversion
- New infrastructure introduction
- Full design overhaul
- Arbitrary public API contract changes
- Final database schema decisions
- Final model, embedding, or vector DB decisions without comparison results
