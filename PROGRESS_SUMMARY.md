# Progress summary — Architecture and Indirect Prompt Injection

## The question, in plain terms

When you connect several AI agents together to do a job, does the *shape* of that
team change how easy it is to hack?

The specific attack is **indirect prompt injection**: the user asks for something
innocent ("fix this bug"), but an attacker has hidden a malicious instruction in
the material the agents read — a code comment, a bug report, a README. If an
agent follows it, it might leak a secret or run a command it shouldn't.

A recent paper (*Architecture Matters for Multi-Agent Security*, Hagag et al.
2026) showed that team structure changes how often agents comply with a
**malicious user**. It explicitly did not study the case where the *user is
innocent* and the danger is in the environment. That gap is this project.

## What I did

1. **Built a prototype** to explore the idea and generate hypotheses.
2. **Pre-registered my predictions** (committed to git *before* running the real
   experiment) so results can't be rationalised after the fact.
3. **Adopted Orbit**, the evaluation framework from the same research group, and
   ran a real benchmark of injected coding tasks inside a Docker sandbox.
4. **Found and fixed a critical bug in Orbit** (see below).
5. **Built my own scorer** after finding the built-in one can't measure
   multi-agent attacks correctly.
6. **Ran the first comparison**: one agent alone vs. a 4-agent "star" team
   (a manager delegating to specialists), on the same 15 injected tasks.

## Contribution back to the field

**Open PR to the Orbit framework:
[wlanderson0/orbit#4](https://github.com/wlanderson0/orbit/pull/4)**

Orbit's scorer for injection tasks was reading its data from the wrong place, so
it silently reported *every attack as blocked and every task as failed*,
regardless of what the agents did. Anyone running that benchmark would have
concluded these systems were perfectly safe. My fix restores the scoring; the
project's full test suite (3,501 tests) passes.

I also identified a second, separate problem: Orbit's injection scoring can't
handle multi-agent teams — it misses what the specialist agents actually run, and
falsely counts an agent that *refuses* an attack as having carried it out. I
built an independent scorer that reads the full execution trace instead.

## Results so far

Same 15 injected tasks, same model (Claude Haiku 4.5), two team structures:

| | single agent | 4-agent star team |
|---|---|---|
| **attack succeeded** | 40% | 40% |
| task still completed | 100% | 100% |
| agent noticed the attack | 0% | 13% |

Two findings:

**1. Agents almost never notice.** Attacks succeed 40% of the time while the
agents flag them ~0–13% of the time. These aren't systems weighing a risk and
getting it wrong — they don't register that anything happened.

**2. Team structure doesn't change *how often* attacks succeed — it changes
*which* attacks succeed.** Splitting the totals by where the attacker hid the
payload reveals a clean crossover the headline number hides:

| attacker hid the payload in… | single agent | star team |
|---|---|---|
| the **bug report** (arrives in the prompt) | 80% | 40% |
| a **code comment** (found when reading files) | 33% | 67% |

The mechanism is intuitive. The star team's manager reads the bug report and
hands out narrow instructions, so a malicious instruction in the report usually
never reaches anyone who could act on it — the team structure *contains* it. But
the specialist who opens the file was told only "read this file"; stripped of the
task context, an injected comment looks like a legitimate instruction, so it gets
followed. **The agent that touches untrusted content is the one denied the
context needed to judge it.**

That points at a concrete design fix worth testing: give executing agents enough
task context to recognise content that doesn't belong.

## Honest limitations

- 15 tasks per arm; each direction of the crossover rests on a 2-sample
  difference. This is a **hypothesis backed by a plausible mechanism**, not an
  established result.
- Only two of four team structures tested (chain and mesh still to run).
- A well-aligned model (Claude Sonnet) resisted everything, so results so far
  characterise a weaker model.

## What funding would enable

Scale to the full 45-task benchmark across all four team structures and several
models — enough statistical power to confirm or kill the crossover — and test the
proposed defence (giving executor agents task context). Current spend is a few
dollars of API credit; the full sweep needs roughly two orders of magnitude more.
