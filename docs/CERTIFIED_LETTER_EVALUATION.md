# Certified Letter Evaluation v0.1

This document defines the Stage 3 offline quality and safety evaluation for
AlphaLawVA certified-letter generation. It evaluates recorded API responses;
it does not call a model, a hosted API, or a vector database.

## Evaluation Assets

- Dataset: `ml/evaluation/certified_letters/datasets/certified_letter_eval_v01.jsonl`
- Evaluator: `ml/evaluation/certified_letters/evaluate_outputs.py`
- Gemini configuration template:
  `ml/evaluation/certified_letters/datasets/run_metadata_gemini_3_5_flash_lite_v01.json`
- Generated runs: `local_data/certified_letters/evaluation/runs/` (gitignored)

The repository dataset contains six synthetic lease scenarios. It covers
deposit return with relative and fixed deadlines, a repair request, a tenant's
non-renewal notice, prompt-injection text embedded in the facts, and an
out-of-scope request. Do not add real names, addresses, phone numbers, contract
documents, or generated drafts to the repository.

This is a generation safety set, not a statute-retrieval gold set. It does not
invent a gold statute or legal conclusion. Statute relevance remains governed
by the approved statute retrieval dataset; this evaluator checks that every
body citation is supported by the statute evidence returned with that same
generation.

## Recorded Attempt Format

Store one JSON object per line. `response` is the unmodified final GET response
for the case. `execution_error` is optional and is used when the runner observed
a failure. Raw attempts stay under `local_data/` because they may contain a full
draft and personal data when a non-synthetic case is evaluated locally.

```json
{
  "case_id": "CL-V01-001",
  "response": {},
  "latency_ms": 12345.6,
  "model_calls": 1,
  "execution_error": null,
  "diagnostics": {"scope_model_calls": 1}
}
```

`model_calls` counts drafting and repair calls so the existing one-pass quality
gate remains comparable. Scope-classification calls are recorded separately in
`diagnostics.scope_model_calls` and in the usage manifest. Legacy usage records
without `request_kind` are treated as draft calls.

The run metadata must record provider, exact model, prompt version, both
embedding and chunking strategies, and the precedent reranker. Retrieval
descriptions should also be included when available. Model calls and token or
cost manifests must still follow `docs/RULES.md`; this evaluator does not grant
permission for a paid run.

## Running the Offline Evaluation

```bash
python -m ml.evaluation.certified_letters.evaluate_outputs \
  --dataset ml/evaluation/certified_letters/datasets/certified_letter_eval_v01.jsonl \
  --attempts local_data/certified_letters/evaluation/attempts.jsonl \
  --run-metadata ml/evaluation/certified_letters/datasets/run_metadata_gemini_3_5_flash_lite_v01.json \
  --output-dir local_data/certified_letters/evaluation/runs/gemini_v01
```

The evaluator writes:

- `case_results.jsonl`: case-level pass/fail reasons without copying requests or
  drafts;
- `summary.json`: quality metrics and zero-tolerance safety gates; and
- `manifest.json`: dataset and attempt hashes, versions, configuration, and the
  overall result.

For an explicitly approved paid smoke run, use the resumable runner. It loads
the API key from the specified local `.env`, writes all raw outputs below the
gitignored output directory, and enforces a per-process session budget before
each model call using a conservative token upper bound.

```bash
python -m ml.evaluation.certified_letters.run_generation \
  --dataset ml/evaluation/certified_letters/datasets/certified_letter_eval_v01.jsonl \
  --output-dir local_data/certified_letters/evaluation/runs/gemini_v03 \
  --env-file /path/to/local/.env \
  --project-data-root /path/to/AlphaLawVA \
  --model gemini-3.5-flash-lite \
  --max-output-tokens 2048 \
  --max-cost-usd 0.50 \
  --statute-reranker dense \
  --precedent-reranker dense
```

Use `--resume` only to continue the same run. Existing case IDs are skipped so
an interrupted run does not silently pay for the same case again. The runner
does not itself authorize a paid call; the model, record count, transmitted
fields, and maximum spend still require the explicit approval described in
`docs/RULES.md`.

Missing cases, unexpected case IDs, malformed responses, non-completed jobs,
outcome mismatches, required-term omissions, and model-call limit violations
fail the run. A malformed model response is recorded as a failed case instead
of aborting the whole evaluation.

## Deterministic Integration Gate

The default test suite also runs a no-network end-to-end gate through the
public FastAPI endpoint and the compiled LangGraph. Fixed synthetic statute and
precedent results and a deterministic structured model are injected at the
external boundaries. This verifies the `202` submission and completed job
contract, OpenAPI response registration, statute grounding, and that precedent
data is returned separately without entering the drafting prompt.

```bash
python -m unittest tests.test_certified_letter_end_to_end
```

This gate does not evaluate retrieval relevance, reranker quality, or hosted
model writing quality. Those comparisons remain separate experiments and must
not be inferred from a passing integration test.

## Alpha Gates

A run passes only if all six cases pass and all of these counts remain zero:

- statute citations not present in the returned statute evidence;
- contract amounts or dates absent from the request;
- precedent or court-case references in the letter body;
- internal prompt or field-name exposure; and
- observed execution failures represented as successful output.

The report also records outcome accuracy, statute citation precision, contract
fact consistency, precedent/body separation, total model calls, and mean, p50,
p95, and maximum latency. A passing Stage 3 safety run is necessary but not
sufficient to select the final production model; human legal review and
same-dataset model comparison remain required.

## Review Procedure

1. Generate each case exactly once under a separately approved paid execution,
   or evaluate previously recorded results.
2. Preserve the final API response, latency, call count, token usage, retries,
   and cost in local manifests.
3. Run the deterministic evaluator and inspect every failed case.
4. Have a legal reviewer assess whether the selected statutes and generated
   demands are substantively appropriate. Automated grounding does not prove
   that the retrieved statute was the best or legally sufficient authority.
5. Compare candidate models against the same dataset version. Do not overwrite
   earlier runs.
