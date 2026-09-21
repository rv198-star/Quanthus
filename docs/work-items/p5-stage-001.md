# P5-STAGE-001 — Learned Stage Assessment Dual-Track Research

| Field | Value |
| --- | --- |
| Parent issue | #400 |
| Milestone | #12 |
| Current gate | P5-STAGE-001-A / #401 |
| Current status | A implementation in progress |
| Governing design | `../design/lld/07-research-scoring/p5-stage-001.md` |
| Frozen request | `../../config/research/p5-stage-001/request.v1.json` |
| Frozen targets | `../../config/research/p5-stage-001/stage-targets.v1.json` |
| P6 relationship | parallel; not predecessor; no mutation |
| P3-STRAT-003 relationship | independent; no data-authority inheritance |

## Goal

Run a bounded P5 research comparison between the existing rule/Stage surface, simple learned baselines,
and one fixed small temporal encoder. The research question is semantic Stage assessment, not
portfolio return or trading.

The line preserves one historical vocabulary but two assessment routes:

```text
authoritative Continuous Facts
        |                     |
existing rule/projection   learned P5 assessment
        |                     |
        +---- read-only comparison/report ----+
```

Agreement is useful diagnostic evidence but is not an automatic confidence multiplier.

## Why this line exists

The Stage phase-1 closeout remains valid. P5-STAGE-001 does not restart a vocabulary-completion
campaign. It tests one concrete downstream research need: whether a learned multi-label assessment can
add reference-supported semantic identification or useful disagreement without first completing every
deterministic Stage rule.

The research can close with simple models winning, no increment, only a subset of useful heads, data
limitations, or compute limitations.

## Child sequence

| Gate | Issue | Purpose | Result/data boundary |
| --- | --- | --- | --- |
| A | #401 | Freeze value card, authority, targets, comparisons, statistics and budget | no market rows; no training |
| B | #402 | Freeze exact development inputs, readiness and references | explicit activation required |
| C | #403 | Validate CPU/GPU backend and recovery | synthetic/environment only |
| D | #404 | Implement R/L/G/T0/T1 interfaces and contracts | synthetic/engineering only |
| E | #405 | Train fixed matrix | train + development-validation only |
| F | #406 | Calibrate and evaluate once | calibration + frozen evaluation |
| G | #407 | Produce read-only dual-track report | sealed results only |
| H | #408 | Independent closeout | review only |

Dependencies stay `A -> (B,C,D) -> E -> F -> G -> H`. Completion of A does not auto-start any
successor.

## Current recovery checkpoint

As of 2026-09-22:

- Owner explicitly opened #401 after approving the dual-track direction and the four-perspective
  design amendments.
- A is allowed to change repository docs/config/tests and run synthetic/contract verification only.
- A must not read market rows, train a model, inspect protected 2022-2026 data, or mutate P6/P3-STRAT.
- The source baseline for A is `ff83909f4584a60cb3bdf6808cae39d339326336`.
- A's machine assets are:
  `config/research/p5-stage-001/request.v1.json` and
  `config/research/p5-stage-001/stage-targets.v1.json`.
- A must finish with a real independent read-only design/implementation review after repository tests.
- Only after A is accepted may the owner separately activate B, C, or D.

Resume order after interruption:

1. verify live #401 and current branch/source SHA;
2. read this checkpoint, then the frozen request and target set;
3. inspect the latest A test/review receipt;
4. continue the single unfinished A step only;
5. never rerun accepted work merely because the chat session changed.

## A acceptance boundary

A accepts only if all of the following hold:

- the downstream value card is concrete and research-only;
- the owner authorization is narrow, A itself remains no-market-read/no-training, and successors are
  not auto-activated;
- the twelve cards preserve layered semantics, T-only boundaries, null handling, 0/ABSTAIN/X
  separation, and Stage11 conditional probability;
- reference eligibility prevents rule-silver circular confirmation;
- R/L/G/T0/T1 attribution, seed aggregation, calibration, bootstrap, Brier, severe-error and
  multiple-comparison rules are frozen before any result;
- the sequence-model exception to the general P5 ladder is explicit and bounded;
- P6/P3-STRAT and protected years remain untouched;
- focused contract tests and repository checks pass;
- an actual isolated reviewer checks the final A diff/evidence.

A does not need a market-data manifest, model weights, GPU benchmark, label corpus, or economic result.

## Successor boundary

B's candidate scope is predeclared but inactive:

- 2012-2021 SSE/SZSE development only;
- exact manifest resolved and verified in B before any row read;
- only the source families in the frozen request;
- 2011 warm-up is not authorized by A;
- 2022-2026 remain closed;
- no strategy/economic result read.

C and D are likewise inactive until separately started. E cannot open until B/C/D are accepted.

## Stop conditions

Close or block instead of widening if progress would require:

- protected-year access;
- future evidence to confirm a T-stage label;
- current sector membership as a historical proxy;
- an unknown/unjudgeable reference treated as negative;
- the tested rule as the only confirmatory truth;
- result-driven head selection, gate lowering, replacement seed, or hyperparameter search;
- P6/P3-STRAT changes;
- production, strategy, broker, or trading authority.

## Closeout semantics

Allowed line outcomes include `SUPPORTED_INCREMENT`, `NO_INCREMENT`, `PARTIAL`,
`INCONCLUSIVE`, `DATA_LIMITED`, `COMPUTE_LIMITED`, and `INVALID_EVIDENCE`.

A itself may close as `A_ACCEPTED_PREREGISTRATION_ONLY`, `REOPEN_NOT_AUTHORIZED`, or
`INVALID_A_CONTRACT`. A green test suite is necessary evidence, not permission to overstate the
research authority.
