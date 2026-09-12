# P3-STAGE-010-D1 Transport Recovery Authorization — 2026-09-12

## State

```text
INITIAL D1: FAILED BEFORE ARTIFACT
TECHNICAL RETRY: FAILED BEFORE ARTIFACT
TRANSPORT RECOVERY: AUTHORIZED ONCE
DEVELOPMENT REFERENCES: 0
CANDIDATE SCORES: 0
PRESERVED 2021 EVALUATION: UNREAD
```

The first D1 failed before any governed artifact because a natural-year endpoint (`2018-12-31`)
was supplied to cohort discovery instead of the final 2018 trading session (`2018-12-28`). That
calendar-only defect was repaired under the already-consumed one-time technical retry authorization.

The technical retry then progressed through cohort discovery, identity projection, 2019 development
market-data reading, Fact construction, threshold-basis construction, the fixed nine-candidate grid,
and morphology-blind case selection. It failed only when packaging those already-selected 2019 cases
into the shared reference-evidence transport:

```text
OrderReferenceError: case_outside_development
```

The retry Job was:

- Job: `stage_2612f623a94b44ba86ed71128d16bf48`
- Revision: `7412361e4c9094ceffda0eb986fbe0033ebc9ee3`
- Input digest: `sha256:1690009abc40a826357b893e9a3fd30ffb02c9b3bb5ff7764338b3ea48b8ea2d`
- Terminal state: `failed`
- Worker error: `stage_positive_instability_quantitative_d1_process_failed`
- Result artifact: none

## Root cause

`config/stage-learning/order-reference-protocol.v1.json` already declares `history_start`,
`case_start`, and `development_end`. The implementation of `build_evidence()` did not consume those
fields; it hard-coded case dates to 2020-2021 and source rows to 2019 onward. P3-STAGE-010 D1 is
preregistered for 2018 warm-up plus 2019 development, so the evidence transport rejected the case
after all market/Fact preparation had already succeeded.

This is an implementation-contract conformance defect in evidence transport. It is not a Stage-0
semantic failure, parameter result, reference result, or candidate result.

## Recovery

A versioned 2019 evidence scope is frozen at:

`config/stage-learning/order-reference-evidence-scope-2019.v1.json`

Digest:

`sha256:2d65ce22666a7709a0f67e3d708df085bb624505484bddeec62a7de9b6ecaa0d`

The generic evidence builder now defaults to the original v1 protocol exactly as before, but when an
explicit frozen protocol plus digest is supplied it obtains the allowed history/case/end dates from
that contract rather than Python literals. Existing v1 behavior remains the default and is covered by
its original regression suite. The P010 D1 prepare is the only current caller that explicitly binds the
2019 scope.

Machine recovery authority:

`config/stage-learning/positive-instability-quantitative-d1-transport-recovery.v1.json`

Digest:

`sha256:9962922fb4ba7ada5e4041c86f01f919fa40969ee5e5a5061c5a0c6440d7a1b1`

## Claim ceiling

The transport recovery does not authorize any of the following:

- changing the 512 / 256 / 160 identity and case budgets;
- changing any sampling seed or identity exclusion;
- expanding or changing the nine-candidate grid;
- changing development/evaluation gates;
- retuning the frozen relocation guard;
- changing Stage 2;
- generating reference opinions before the candidate-blind review ledger is sealed;
- reading the preserved 2021 evaluation plan;
- reading 2022-2025;
- candidate replacement, semantic retry, supplement, official labels, strategy, or trading.

The two failed Jobs remain immutable evidence. Neither published an artifact. Before this recovery,
no development reference opinion, candidate score, selected candidate, or preserved-evaluation market
row was exposed. The recovery is therefore a bounded evidence-transport recovery, not a third semantic
trial.
