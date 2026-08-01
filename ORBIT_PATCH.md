# Required Orbit patch

The confirmatory runs depend on a one-line fix to Orbit (`wlanderson0/orbit`).
**Without it, the entire `code_ipi` scorer is non-functional** — every attack is
scored as resisted and every task as not completed, regardless of what the
agent actually did (see below). Reported upstream via the project feedback
form.

Anyone reproducing this work must apply the same patch to their Orbit clone.

## The bug

`build_sample()` in `orbit/dataset/sample_factory.py` serializes the whole
config under `metadata["experiment"]`, so `config.metadata` (the `code_ipi_*`
keys) never reaches the top level of `state.metadata`. But `code_ipi/scorer.py`
reads them top-level, so all resolve to defaults:

- `payload_category` → `"none"` ⟹ injection-resistance always falls through to
  `else → (1.0, "unknown_category")`, which the `injection_resistance_rate`
  metric counts as resisted; the breach checks are never called.
- `sample_id` → `""` ⟹ exec-canary lookup always misses.
- `verification_command` → `""` ⟹ `task_completion` always 0.
- `is_benign` → `False` ⟹ benign samples are scored as injected:
  `false_positive_rate` always sees an empty benign set (reports 0) and the
  LLM detection grader runs on every sample.

Confirmed at runtime: a run recorded `irr_level="unknown_category"` and
`state.metadata` had no top-level `code_ipi_*` keys.

## The fix

In `orbit/dataset/sample_factory.py`, `build_sample()`, surface `config.metadata`
at the top level of the sample metadata:

```python
sample_metadata = metadata_model.model_dump()
if config.metadata:                                    # <-- added
    sample_metadata = {**config.metadata, **sample_metadata}
# Sample(..., metadata=sample_metadata, ...)           # was: metadata_model.model_dump()
```

One change restores injection-resistance, exec, persistence, task-completion,
and false-positive scoring at once. Merge order matters: `sample_metadata`
last, so the `MASMetadata` fields (`experiment`, `sample_variant`,
`baseline_reference`) win any key collision. General (helps any scenario that
sets `config.metadata`); scorers that already probe the flat location first
(redcode_gen, bigcodebench) are unaffected, and `metadata_as(MASMetadata)`
still validates since extra top-level keys are ignored.

Note: upstream may instead fix the scorer to read
`state.metadata_as(MASMetadata).experiment.metadata`, the pattern every other
Orbit scenario already uses. Either fix alone is sufficient; applying this
patch on top of such an upstream fix is harmless.

Orbit @ initial commit, inspect_ai 0.3.247.
