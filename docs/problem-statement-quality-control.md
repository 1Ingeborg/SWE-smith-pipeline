# Scalable problem statement quality control

## Decision

Codex or human review is used to label a small calibration sample, not to review
every generated record. Production batches use automated gates for every record
and route only uncertain records plus a stratified random sample to manual review.

The current pipeline is:

```text
validated candidate patch
  -> replay representative failures in Docker
  -> Qwen3.7 Flash issue draft
  -> deterministic rule gate
  -> one automatic rewrite when needed
  -> leakage reviewer
  -> factuality/reproduction reviewer
  -> accepted / needs_review / rejected
```

The leakage and factuality roles are deliberately separated:

- The leakage reviewer receives the candidate issue and hidden code change. It
  checks for changed identifiers, implementation mechanisms, root-cause clues,
  and repair hints.
- The factuality reviewer receives the candidate issue, complete failure output,
  and behavioral source, but not the code change. It checks whether the symptom,
  expected behavior, reproduction, and claimed consequences are supported.
- Deterministic rules reject explicit test/data-generation references, edit
  descriptions, unsupported exception classes, unsupported runtime import
  failures, and overlong output.

Only a record that passes all three gates is accepted automatically. A model
error, confidence below 0.85, or ambiguous verdict becomes `needs_review` rather
than a silent acceptance.

## Seven-record calibration experiment

Seven validated MonkeyType candidates were used to create seven runnable gold
problem statements and seven known-bad model drafts. Every gold reproduction was
executed against its bugged Docker environment.

The first single Qwen Plus reviewer was too permissive: it accepted drafts that
named deleted identifiers and one unsupported runtime consequence. After the
review task was split, Qwen Plus was also too aggressive on valid symptoms,
rejecting them as indirect solution leakage.

Qwen3.7 Flash performed better in the narrowly defined reviewer roles. Before
the final deterministic check, it:

- automatically accepted 6 of 7 gold statements and routed 1 to review;
- rejected 6 of 7 known-bad statements;
- missed one unsupported claim that top-level imports would cause runtime import
  errors.

The missed claim is now rejected deterministically unless execution evidence
contains an actual `ImportError` or runtime failure. Re-evaluating the saved
review decisions with this rule leaves no known-bad auto-accepts in the 14-item
calibration set, while one valid item remains conservatively in `needs_review`.

This result validates the architecture, not production accuracy. Fourteen items
are too few to estimate a rare leakage rate.

## Scaling policy

Before processing hundreds or thousands of records:

1. Expand the gold set to at least 50-100 records, stratified by repository,
   mutation strategy, failure type, and patch size.
2. Include both good statements and adversarial bad statements: direct answer
   leaks, paraphrased repair hints, invalid reproductions, unsupported effects,
   and vague reports.
3. Measure the confusion matrix separately for leakage and factuality. Do not use
   model-reported confidence as an accuracy estimate.
4. Require zero known critical-leak auto-accepts and high recall on the expanded
   calibration set before enabling bulk acceptance.
5. In production, review every `needs_review` item and a stratified random sample
   of accepted items. Increase the sample or stop the batch when drift is found.
6. Recalibrate after changing the generator model, reviewer model, prompt,
   repository mix, or mutation strategy distribution.

For stronger independence, use a reviewer from a different model family once an
alternative endpoint is available and benchmark it on the same fixed gold set.

## Commands

Prepare complete evidence on the Docker host:

```bash
python src/swesmith_lab/issuegen/generate.py \
  /data/results/problem-statements/prepared.jsonl \
  --dataset /data/datasets/swe-smith-lab/monkeytype-pilot-valid.jsonl \
  --validation-dir /data/repos/SWE-smith/logs/run_validation/Instagram__MonkeyType.70c3acf6 \
  --prepare-only --overwrite
```

Generate and review on a host that can reach DashScope:

```bash
export DASHSCOPE_API_KEY='set-outside-git'
python src/swesmith_lab/issuegen/generate.py results/reviewed.jsonl \
  --prepared-input results/prepared.jsonl \
  --workers 2 --overwrite
unset DASHSCOPE_API_KEY
```

Resume an interrupted batch without repeating completed instance IDs:

```bash
python src/swesmith_lab/issuegen/generate.py results/reviewed.jsonl \
  --prepared-input results/prepared.jsonl \
  --workers 2 --resume
```

Re-run the automated gates on a fixed candidate or gold set:

```bash
python src/swesmith_lab/issuegen/generate.py results/calibration-review.jsonl \
  --prepared-input results/prepared.jsonl \
  --candidate-input results/gold.json \
  --max-rewrites 0 --overwrite
```
