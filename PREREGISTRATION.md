# Pre-registration — Architecture and Indirect Prompt Injection

**Status:** written before any confirmatory (BrowserART/Orbit) run.
Committed to establish the exploratory → confirmatory sequence.

## Question

Does multi-agent architecture change susceptibility to *indirect* prompt
injection — and does it move in the *opposite* direction from direct misuse?

This extends Hagag et al. (2026), *Architecture Matters for Multi-Agent
Security*, from a malicious **user** (their setting) to a benign user whose
agents encounter attacker-controlled **environment content** — the vector they
explicitly leave unexplored (§8).

## Threat model

Benign user; the attacker controls content the agents read from the environment
(Orbit `indirect_injection`: payload spliced into a tool's output or written to
a sandbox file). No weight or system-prompt access.

## Design

- **IV — architecture:** `standalone` (baseline) → `star` (isolated `as_tool`
  specialists) → `chain` (handoff) → `mesh`. Roles fixed at 4 specialists;
  memory private.
- **Held fixed:** payload, injection target, model, task, step budget.
- **Scenario:** `code_ipi` (Orbit's dedicated indirect-injection coding
  scenario) as primary; **BrowserART as the second-scenario replication check**
  (does the effect hold on a different task/payload — see course-changers).
  See Deviations below.
- **DVs — stage-wise:** exposed → *attempted* (complied) → *completed* (attack
  success) → *detected*. Attack success is kept separate from attempted and from
  detection — an attack can fail because an agent caught it (a defensive win) or
  because it wandered off (luck), and only the first is architecturally meaningful.

## Procedure

1. Measure the **standalone** injection success rate — the reference point.
2. Run the same injection under each architecture; report each as a delta from
   standalone.

## Hypotheses

Directional and *confirmatory* for the BrowserART/Orbit runs. The local
prototype (in this repo) that suggested them is disclosed as **exploratory,
hypothesis-generating** — not evidence for these claims.

- **H1 — sign-flip:** `star ≤ standalone` on attack success. Specialist
  isolation contains the injection (untrusted content reaches one agent, not
  four) — the *reverse* of the paper's direct-misuse result, where star was the
  riskiest topology.
- **H2 — exposure is a liability:** `mesh ≥ star`. More agents seeing the page
  means more chances to *comply*, not to catch, when per-agent detection is low.
- **H3 — silent failures:** detection ≈ 0 across architectures — non-attacks are
  unnoticed rather than refused.
- **H4 (secondary):** benign-task success roughly flat across architectures —
  capability does not predict injection-robustness.

## What would change course

- All conditions ≈ 0% or ≈ 100% attack success → tune payload strength until
  there is variance to explain.
- Between-topology differences inside noise at n≈20/cell → raise n / cut
  conditions; if still flat, report "no detectable effect" with CIs.
- Effect does not replicate on a second payload → report as a payload artifact,
  do not generalize.
- LLM judge disagrees with hand-labels on >10% of a spot-checked sample → fix
  the judge before trusting any detection rate.

## Deviations from initial plan (disclosed)

- **2026-07-31 — primary scenario changed from BrowserART to `code_ipi`.**
  Rationale: `code_ipi` is Orbit's purpose-built indirect-injection scenario and
  ships standalone/star/mesh topology presets plus an in-sandbox injection
  scorer, so it tests the same hypotheses with less bespoke config and a
  scenario designed for this exact threat vector. BrowserART is retained as the
  cross-scenario replication check, which directly serves the pre-registered
  "payload/scenario artifact" course-changer. Hypotheses H1–H4 are unchanged.
  Made before any confirmatory run.

## Exploratory prototype (already run — NOT confirmatory)

A local mock harness (this repo) on `qwen2:7b`, n=8/cell, single hand-written
payload, surfaced the patterns that motivated H1–H3: standalone completed the
attack ~50% of trials; star almost never relayed the injection to a specialist;
mesh saw broad compliance; detection was ~0 everywhere. Treated strictly as
hypothesis-generating due to tiny n, a weak model, and one payload.
