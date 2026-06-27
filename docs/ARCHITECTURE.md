# ARCHITECTURE.md

This document describes the current AlphaLawVA architecture assumptions. It
covers structures that may change during implementation and experiments. Stable
coding, security, testing, and reporting rules belong in `RULES.md`.

## Project Overview

AlphaLawVA helps users handle real-estate legal issues without relying on a
legal expert for every step. The service covers prevention before signing a
contract, contract review and generation, and dispute response after a problem
occurs.

The project is being upgraded by a small ML-centered team of two people. The
team will handle frontend, backend, and ML work together.

Some architecture details are intentionally not fixed yet. Database schema,
model choice, embedding model, RAG strategy, evaluation setup, and frontend
screen structure may change during experiments and implementation. Treat those
parts as current design assumptions, not permanent rules.

## Service Scope

Supported transaction types:

- Lease: jeonse and monthly rent
- Sale and purchase

Supported legal areas:

- Registry and ownership relations
- Ownership, mortgage, jeonse rights
- Opposing power
- Preferential repayment right
- Deposit return
- Jeonse fraud
- Contract termination

## Core Features

1. Contract review
   - Analyze risk by clause for lease and sale contracts.
   - Cross-check contract contents with registry documents.

2. Contract generation
   - Do not describe the feature as replacing a final legal contract prepared by a legal professional.
   - Generate draft contract templates and clause suggestions for lease and sale transactions.

3. Certified letter generation
   - Generate draft certified letters or legal notice text for deposit return, contract termination, and jeonse fraud response.

4. Precedent-based dispute search
   - Search similar precedents.
   - Provide response strategies based on retrieved precedents and statutes.

## Current Tech Stack

Languages:

- Python
- TypeScript

Backend:

- FastAPI
- Backend and ML are integrated in the same service boundary unless the implementation later proves this should change.
- The intended FastAPI entrypoint is `api/main.py`.
- API code may import ML modules from `ml/` while keeping folder responsibilities separate.

Frontend:

- Next.js
- Responsive web UI
- Frontend dependencies should live under `frontend/package.json` unless the team intentionally chooses a different monorepo layout.

AI and RAG:

- LangChain
- LangGraph
- ReAct-style agentic RAG

Databases:

- MySQL for users, contracts, analysis history, and glossary data
- ChromaDB for precedent vector search
- ChromaDB plus Neo4j for statute search and hierarchy traversal

Deployment:

- Docker
- GitHub Actions
- AWS EC2

## Project Directory Structure

This is the intended structure. New directories may be added as needed, but do
not reorganize or rename existing top-level directories without discussion.

```text
AlphaLawVA/
├── data/
│   ├── statutes/
│   │   ├── raw_jsons/
│   │   ├── filtered_hierarchical_jsons/
│   │   └── chunks/
│   └── precedents/
│       ├── raw_jsons/
│       ├── filtered_jsons/
│       └── chunks/
├── api/
│   ├── main.py
│   ├── routers/
│   └── models/
├── ml/
│   ├── agent/
│   ├── rag/
│   ├── data_collection/
│   └── embedding/
├── frontend/
│   ├── package.json
│   └── package-lock.json or pnpm-lock.yaml
├── docs/
│   ├── ARCHITECTURE.md
│   ├── RAG_AND_EVALUATION.md
│   ├── RULES.md
│   └── VERSION_UPGRADE.md
├── .env.example
├── docker-compose.yml
├── requirements.txt
└── AGENTS.md
```

Expected responsibilities:

- `data/`: raw, filtered, and chunked legal data.
- `api/`: FastAPI entrypoint, routers, request/response models, and backend API logic.
- `ml/`: agent, RAG, data collection, embedding, evaluation, and ML-related logic.
- `frontend/`: Next.js frontend application.
- `docs/`: project instructions for AI coding agents and team members.
- `docker-compose.yml`: local service orchestration.
- `requirements.txt`: Python dependencies until the team chooses a different dependency manager.
- `AGENTS.md`: repository entrypoint instructions for AI coding agents.

## Layering and Dependency Rules

Keep entrypoints thin and isolate external dependencies behind clear modules.

- FastAPI routers should handle request/response concerns and delegate business, ML, retrieval, and data access logic to explicit modules.
- Frontend components should not directly contain backend, RAG, data collection, or provider API logic.
- Do not call external APIs directly from routers or UI components.
- Keep Open Law API, LLM provider, embedding API, ChromaDB, Neo4j, and MySQL access behind explicit client, service, repository, or adapter-style modules.
- Avoid crossing folder responsibilities only for convenience.

## Data Design Assumptions

The following structure is provisional and may change as implementation,
experiments, and evaluation progress.

### Precedent DB

- Store precedent data in ChromaDB.
- Use hybrid search with vector search, BM25, and Kiwi morphological analysis.
- Chunk by legal document section, such as issue, holding summary, and reasoning.

### Statute DB

- Use Neo4j for hierarchical statute structure.
- Use ChromaDB for statute vector retrieval.
- Connect ChromaDB records to Neo4j nodes through `neo4j_node_id` metadata.

Neo4j node types:

- Law
- Article
- Paragraph
- Subparagraph

Neo4j relationship:

- `CONTAINS`: parent to child

## Data Collection Assumptions

### Statutes

- Source: Korean Ministry of Government Legislation Open API (`open.law.go.kr`)
- Topic keywords: 임대차, 전세, 월세, 보증금, 매매, 부동산, 등기, 대항력, 우선변제, 전세사기, 계약
- Collection flow:
  1. Start from the topic keywords.
  2. Use an LLM to generate a collection keyword list from the topic keywords.
  3. Request the legislation API with the generated Korean keywords.
  4. Collect candidate laws.
  5. Filter by law name.
  6. Store hierarchy and vector-search metadata.

### Precedents

- Source: Korean Ministry of Government Legislation Open API
- Topic keywords: 임대차, 전세, 월세, 보증금, 매매, 부동산, 등기, 대항력, 우선변제, 전세사기, 계약
- Collection flow:
  1. Collect candidate precedents directly with the topic keywords.
  2. Extract issue and holding summary.
  3. Judge whether each precedent is related to the topic with a local LLM as `True` or `False`.
  4. Collect full text only for relevant precedents.

## Agent Architecture Assumptions

Pattern:

- ReAct
- Agentic RAG

Framework:

- LangGraph

Planned tools:

- `search_precedent`: search precedent ChromaDB
- `search_law`: search statute ChromaDB and traverse Neo4j hierarchy
- `analyze_contract`: analyze contract risk
- `generate_certified_letter`: generate certified letter or legal notice draft content

Planned statute search flow:

1. Retrieve relevant statute chunks from ChromaDB.
2. Use `neo4j_node_id` to traverse the Neo4j hierarchy.
3. Add parent article context.
4. Pass retrieved context to the LLM.
