# P3-STAGE-001-H second-group development profile freeze — 2026-09-09

## Status

- Work item: `P3-STAGE-001-H`
- Delivery state after this freeze: `ready` for development-only continuous profiling
- Config: `config/stage-learning/second-group-profile.v1.json`
- Runtime kind: `quanthus.stage-second-group-profile.v1`
- Holdout authority: none
- 2022-2024 arbitration: consumed previously and not reread
- 2025 permanent blind: unopened / unselected

This record freezes the pre-result semantics and execution boundary for the second stage group. It is
not result evidence and does not close H.

## Why this profile exists

The current draft vocabulary treats codes 5/6/7/10/11 as if they were successive market stages, but
the approved qualification review already identified a dimension problem: an advancing trend can
coexist with a local pullback, while structural failure and post-break rebound require an explicit
prior support role and process history. Guessing numerical thresholds before measuring those
morphologies would turn the vocabulary into its own weak-label generator.

Therefore the next executable step is a development-only continuous profile, not a five-class
classifier.

## Frozen semantic decomposition

1. Stage 5 remains a **trend-background hypothesis** after a frozen Stage-4 acceptance point.
2. Stages 6/7 remain **local pullback / repair process hypotheses**. They are not forced to be
   mutually exclusive with the broader Stage-5 background before development evidence supports a
   compression rule.
3. Stages 10/11 remain a **support-failure / post-break rebound process**. Falling back below the
   original breakout anchor is not sufficient for Stage 10.
4. The only support candidate used by this profile is the pre-T frozen low anchor captured when the
   breakout episode started. The profile may measure breaks/reclaims of this object but cannot call
   it proven key support.
5. The profile emits no primary stage and no official label.

## Frozen data and entry boundary

- Cohort: 512 securities selected deterministically using the same pre-2020 development population
  discipline already exercised by the capacity census.
- Cohort selection evidence ends at 2019-12-31.
- Warm-up begins 2019-07-01.
- Report window: 2020-01-02 through 2021-12-31 only.
- Anchor scales: 20 and 60 sessions.
- Entry: first observation in each breakout episode that satisfies the already-frozen Stage-4
  development candidate (`probe=1.0`, `hold=2`, `signed-distance=0`, `activity=1.0`).
- Path horizon: at most 60 admitted sessions including the first Stage-4 acceptance session.
- Missing/unadmitted rows, listing changes and coordinate changes terminate the path. They never
  manufacture structural failure.

## Frozen measurements

Observation-level measurements are causal at each row and include:

- return from Stage-4 acceptance;
- 5/10/20-session trailing returns;
- drawdown from the running peak;
- distance to the frozen breakout anchor;
- distance to the initial frozen low support candidate;
- volume-ratio20 when available.

Episode-level summaries include:

- maximum extension after acceptance;
- maximum drawdown from the running peak;
- minimum distance to the initial support candidate;
- sessions to first support-candidate break;
- maximum rebound from the running post-break low;
- sessions from first break to first support-candidate reclaim;
- sessions observed.

Only counts and nearest-rank p05/p10/p25/p50/p75/p90/p95 distributions are published. Raw security
codes, market prices, per-episode rows and stage labels are not published by this job.

## Forbidden interpretation

This profile cannot:

- select Stage-5/6/7/10/11 thresholds;
- train a classifier;
- run the synthetic expert panel;
- read future settlement or strategy results;
- read or reuse 2022-2024 arbitration as fresh evidence;
- read 2025;
- choose a future holdout population;
- publish SHADOW/QUALIFIED labels, schedule daily inference or authorize trading.

After the profile result is audited, a separate development freeze may define a small, finite
parameter space or decide that some numbered states should be merged or represented as dimensions.
