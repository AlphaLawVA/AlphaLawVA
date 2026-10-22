# Stage 7 Gemini Lite Smoke Run

Date: 2026-10-22

This record documents the first end-to-end smoke run of the certified-letter
graph. It is not a reranker or legal-quality benchmark. Both retrievers used
their dense-order baseline so that reranker model selection can be evaluated
separately later.

## Configuration

- Provider/model: Google Gemini API, `gemini-3.5-flash-lite`
- Prompt: `certified_letter_v0_3`
- Dataset: `certified_letter_eval_v01` (six synthetic cases)
- Maximum output: 2,048 tokens per call
- Rerankers: `dense` for both statute and precedent retrieval
- Raw artifacts: gitignored `local_data/certified_letters/evaluation/runs/`

Only synthetic request fields and up to five locally retrieved statute records
were sent to the model. Precedent text, API keys, local paths, and real personal
data were not included in generation prompts.

## Initial Run

- Passed: 3/6 cases under the strict dataset gates
- Outcome accuracy: 0.666667
- Statute citation precision: 1.0
- Contract fact consistency: 1.0
- Precedent-body separation: 1.0
- Model calls: 9
- Tokens: 33,424 input / 4,256 output / 37,680 total
- Estimated cost: USD 0.0206672
- Latency: 6,758 ms mean, 4,917 ms p50, 16,532 ms p95/max

All six safety gates passed: no unsupported statute citation, invented contract
amount or date, precedent citation in the letter body, internal prompt exposure,
or system failure presented as success was observed.

The failed quality cases were not hidden by relaxing the evaluator. One case
failed after its single repair, another completed after repair but exceeded the
dataset's one-call quality target, and the out-of-scope case was initially
misclassified because a negated lease reference still contained a scope keyword.

## Focused Verification

The scope check now treats a negated real-estate reference as out of scope when
there is no positive supported purpose. The original out-of-scope case then
completed correctly with zero model calls and zero additional cost.

The generation failure was rerun once to capture deterministic diagnostics. It
completed safely after one repair:

- Model calls: 2
- Tokens: 7,797 input / 843 output / 8,640 total
- Estimated cost: USD 0.0044466
- Safety gates: all passed
- Strict case result: failed only because the dataset keeps a one-call target

Total estimated paid usage for Stage 7 was USD 0.0251138. No further call was
made after the focused verification.

## Interpretation

The run verifies the graph, one-repair path, deterministic output validators,
usage recording, and spend guard. It does not establish that the retrieved law
is the best legal authority or that the generated letter is production-ready.
Reranker model comparison and a broader, reviewed legal-quality evaluation must
remain separate follow-up work.
