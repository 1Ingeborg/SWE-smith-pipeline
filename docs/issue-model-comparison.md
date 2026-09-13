# Qwen problem statement model comparison

## Goal

Generate a realistic GitHub issue (`problem_statement`) from a validated
SWE-smith candidate patch without exposing the patch, tests, benchmark, root
cause, or intended fix.

## Experiment

- Dataset: 3 validated MonkeyType candidates.
- Mutation strategies: remove an assignment, remove a loop, remove class methods.
- Models: `qwen3.7-flash-2026-07-15`, `qwen-plus-2025-12-01`, and
  `qwen3-coder-flash-2025-07-28`.
- Generation settings: temperature 0, thinking disabled, maximum 800 output tokens.
- Evidence: candidate patch, failing-test summary, and up to two relevant test
  functions.
- Iteration: the prompt was tightened after the first outputs leaked test or
  implementation details. A truncated failure was then reproduced separately
  to obtain its actual `NameError` before a focused retry.

## Results

The second prompt iteration produced the following aggregate usage and latency:

| Model | Input tokens | Output tokens | Mean latency | List-price estimate |
| --- | ---: | ---: | ---: | ---: |
| Qwen3.7 Flash | 2,986 | 608 | 3.96 s | CNY 0.0011 |
| Qwen Plus | 2,819 | 554 | 4.86 s | CNY 0.0034 |
| Qwen3 Coder Flash | 2,819 | 492 | 2.52 s | CNY 0.0048 |

The list-price estimate uses the <=32K tier for both Flash models and the
applicable Qwen Plus tier. An active free quota can reduce the billed amount to
zero. Across the first run, prompt iteration, and focused retry, the total
list-price estimate was approximately CNY 0.0221.

### Quality observations

- Qwen3.7 Flash produced the best overall user-facing issue reports and was the
  cheapest model. It still inferred implementation details for one locally
  defined-function case, so its output cannot be accepted without a gate.
- Qwen Plus was concise, but repeatedly copied test names and described disabled
  implementation logic.
- Qwen3 Coder Flash was fastest, but most readily exposed test names and the exact
  removed method. Coding specialization did not help this issue-writing task.
- All models mischaracterized the assignment-removal case when the validation log
  only contained a truncated `Na...` failure. Once the exact `NameError` was
  supplied, all three described the observable failure correctly. Evidence
  quality had a larger effect than changing models.

## Recommended pipeline

1. Start with `qwen3.7-flash-2026-07-15`, temperature 0, and thinking disabled.
2. Supply the patch, a complete exception/traceback, and the smallest relevant
   behavioral test context. Do not rely on a truncated pytest summary.
3. Reject output when `quality_flags` is non-empty or when a factual review finds
   behavior not supported by the execution evidence.
4. Retry once with the detected violation stated explicitly. Send unresolved or
   ambiguous cases to manual review.
5. Use Qwen Plus only as a fallback for drafts that remain unclear. Do not use
   Qwen3 Coder Flash by default for `problem_statement` generation.
6. Keep the raw candidate, model draft, quality flags, reviewer decision, and final
   accepted statement as separate fields so bad-case iteration remains auditable.

This experiment is deliberately small. Re-evaluate the decision on at least
30-50 diverse validated candidates before generating a larger training set.

Official references:

- [Model Studio model pricing](https://help.aliyun.com/zh/model-studio/model-pricing)
- [OpenAI-compatible Qwen API](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)

## Reproduction

The comparison was a one-off model selection experiment. Its harness was not
kept in the repository, so there is no script to re-run here. Reproducing the
numbers means re-writing a small client that sends the same prepared evidence
to each candidate model. The decision it supported is recorded above.
