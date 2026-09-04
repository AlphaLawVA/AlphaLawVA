# RAG_AND_EVALUATION.md

This document defines AlphaLawVA's legal AI output, RAG, prompt, model
comparison, embedding comparison, and evaluation rules.

## Model Candidates

LLM candidates by role:

- User-facing generation and agent responses: Claude or GPT-4o, TBD after testing
- Offline filtering and classification: local LLM candidate, TBD

Embedding candidate:

- `text-embedding-3-large`, provisional until comparison results are available

Do not lock in a model, embedding model, vector DB strategy, prompt, retrieval
strategy, or chunking strategy as a final decision without comparison results.

## Legal AI Output Rules

AlphaLawVA works in a legal domain, so legal accuracy and uncertainty handling
are product requirements.

- Do not fabricate statutes, precedent names, case numbers, article numbers, legal effects, legal conclusions, or source metadata.
- Prefer retrieved statutes and precedents with source metadata when generating legal explanations.
- Clearly separate retrieved legal facts from model-generated interpretation or strategy.
- If retrieved evidence is weak, missing, or contradictory, say so instead of over-answering.
- User-facing output must not claim to replace professional legal advice.
- Generated contracts, certified letters, and dispute strategies must be presented as drafts or guidance unless the product explicitly changes this policy.

## Prompt Rules

- Keep important prompts version-controlled.
- Do not silently change prompts used in production or evaluation.
- Before changing prompt templates used by production or evaluation code, search for usages and check the impact.
- Treat prompt construction that includes user-controlled input as security-sensitive.
- Defend explicitly against prompt-injection risks and report manual QA steps when relevant.

## RAG Rules

- Record retrieval, prompt, model, embedding, and chunking changes when comparing results.
- Keep evaluation datasets separate from ad-hoc test data.
- Prefer measurable comparisons over subjective model preference.
- Do not silently change retrieval behavior.
- When retrieval evidence is insufficient, report the limitation instead of generating unsupported legal guidance.
- Preserve source metadata through the retrieval and generation pipeline whenever practical.

## Evaluation Rules

- Use RAGAS where applicable for RAG evaluation.
- Follow `docs/STATUTE_RETRIEVAL_EVALUATION_DATASET.md` when creating or
  changing the statute retrieval gold dataset.
- For legal AI outputs, include retrieval-grounding or source-metadata checks when practical.
- Track evaluation assumptions such as dataset, model, embedding model, chunking strategy, retrieval method, and prompt version.
- Compare model or retrieval changes against the same evaluation set when practical.
- If evaluation cannot be run, report why and describe the remaining risk.

## Change Documentation

When RAG, model, prompt, retrieval behavior, or evaluation behavior changes,
include the following in the completion report or `CHANGES.md`:

- Prompt changes
- Retrieval behavior changes
- Model or embedding changes
- Chunking strategy changes
- Evaluation dataset changes
- Source metadata behavior changes
- Regression risk scenarios
- Manual QA method

Before reporting that there were no behavior changes, re-read the diff and
verify prompts, schemas, environment variables, retrieval behavior, and source
metadata behavior.
