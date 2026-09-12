# P3-STAGE-009-D Fresh Task-Disjoint Development Validation Preregistration

| Field | Value |
| --- | --- |
| Work item | P3-STAGE-009-D |
| Contract | `../../config/stage-learning/positive-instability-development-screen.v1.json` |
| Contract digest | `sha256:2ad1afc0c0ac1f9ba9822f1b34dc9f9a995e6c8783b7509e8c5189e897f4110a` |
| Predecessor | P3-STAGE-009-C / `PASS_DIAGNOSTIC_CONFORMANCE_ONLY` |
| State | PREREGISTERED / IMPLEMENTATION BEFORE DATA READ |

## 1. Why this validation does not open 2025

P3-STAGE-009 needs quantitative thresholds before it can run a meaningful fresh test. Opening the
permanent-blind 2025 partition merely to develop those thresholds would destroy the stronger future
holdout. The existing generic Stage development reader also correctly refuses all dates after 2021.

D therefore remains a **development-only task-disjoint validation**. It uses a third deterministic
set of economic identities that excludes:

- all 515 identities already excluded by Stage-007;
- the entire 512-identity Stage-007 pool;
- the entire 512-identity Stage-008 fresh pool.

The new 512-identity pool is split 256/256 into development and evaluation capacity with zero overlap.
2022–2024 and 2025 remain sealed.

A D pass can therefore produce at most `PROMISING_EXPLORATORY_REPAIR`. It cannot claim formal blind
qualification, runtime Fact qualification, official labels, strategy authority or trading authority.

## 2. Sampling is morphology-blind

D1 may read 2019 warm-up plus 2020 rows for the 256 development-capacity identities. Before any
reference opinion or candidate score exists, it freezes:

- 128 development identities, 32 assigned to each quarter;
- one primary-available 2020 date per identity by deterministic hash;
- 160 evaluation identities, 40 assigned to each quarter, **without reading their 2021 market rows**.

No cross-quarter backfill, identity replacement, result-dependent supplementation or second sample
round is allowed.

D3 can read the frozen 2021 evaluation identities only after D2 has frozen exactly one eligible
candidate.

## 3. Threshold generation avoids label leakage

The 009-B contract needs five primitive evidence projections. D1 derives their candidate cutoff values
from **unlabelled** primary-available 2020 observations across the full 256-identity development
capacity.

The preregistered quantile levels are:

- material range width: `Q50 / Q60 / Q70`;
- material positive width expansion: `Q50 / Q65 / Q80` over positive width-change observations only;
- low absolute directional efficiency: `Q50 / Q40 / Q30`;
- low traversal: `Q50 / Q40 / Q30`;
- high counter-direction excursion: `Q50 / Q60 / Q70`, excluding zero-net unavailable values.

These produce three shared core profiles, three expansion profiles and three reversal profiles. Their
Cartesian product is exactly **27 candidates**. The grid cannot expand after references or scores are
seen.

The 008 evaluation, root-cause counterfactual and C diagnostic supply no numeric cutoff.

## 4. Reference boundary

Both development and evaluation use the existing Primary-first semantic protocol with two same-origin
roles: short-term and fund. Candidate output, explicit recent 10-session derived Facts and future rows
are absent during review. Opinions are sealed before scoring.

The same-origin provenance is explicitly not independent or human-expert validation.

## 5. Development gates

D2 may freeze one candidate only if the 2020 development screen has at least:

- 96 complete cases;
- 15 definite Stage-0 positives;
- 50 definite Stage-0 negatives;
- 12 Stage-0 predictions;
- precision >= `0.75`;
- positive-reference coverage >= `0.70`;
- definite-negative FPR <= `0.12`.

It must also match or improve the frozen 008 candidate on precision and FPR, lose no more than `0.15`
absolute coverage, remove at least one false positive when the baseline has one, and reproduce the
unchanged Stage-2 predictions exactly.

If no one of the 27 preregistered candidates passes, D closes
`REVISE_OR_PARK_DEVELOPMENT_FAIL`; there is no grid extension.

## 6. Evaluation gates

If D2 freezes a candidate, D3 opens only the pre-frozen 2021 evaluation identities and dates. D4 then
scores the frozen candidate against sealed references. The evaluation requires at least:

- 120 complete cases;
- 18 definite Stage-0 positives;
- 70 definite Stage-0 negatives;
- 15 Stage-0 predictions;
- precision >= `0.80`;
- positive-reference coverage >= `0.65`;
- definite-negative FPR <= `0.10`.

It must not be worse than the frozen 008 baseline in precision/FPR, may lose at most `0.10` absolute
coverage, must remove at least one baseline false positive when one exists, and must leave Stage-2
predictions unchanged.

Pass disposition: `PROMISING_EXPLORATORY_REPAIR`.
Fail disposition: `REVISE_OR_PARK_EVALUATION_FAIL`.

## 7. Result-bearing budget

Only two market-data Jobs are allowed:

1. D1 — prepare 2020 development evidence, unlabelled quantile threshold basis, and freeze the 2021
   evaluation identity plan;
2. D3 — prepare 2021 evaluation evidence after D2 candidate freeze.

D2/D4 reference sealing and scoring are deterministic consumers of bounded review transport, not new
market-data Jobs.

Maximum parameter trials: 27 total, development only. Evaluation parameter search is zero.
Automatic supplement rounds: zero. Semantic answer retries: zero.

## 8. Authority after this preregistration

This preregistration authorizes **implementation of D1/D3 machinery only**. It does not itself read
market data or submit a Job.

D1 market-data access becomes authorized only after the implementation, one-shot budget, source
isolation and Primary-only transport have passed engineering acceptance on a durable commit.

2022–2025 remain closed throughout this D series.
