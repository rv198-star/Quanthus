# P016 Instability No-Pass Four-Party Review

Status: `PASS_OPEN_P017_STRATUM_NORMALIZED_INSTABILITY_SUCCESSOR`

This is a same-origin simulated review by four bounded perspectives: short-term trader, fund manager,
quantitative researcher, and warehouse/governance engineer. It is not four independent human/model
reviewers.

## Evidence reviewed

The sole P016-B Job `stage_e06cc35f8935424d9e906b9d456bbff2` completed successfully as a
semantic scoring Job and returned `REVISE_OR_PARK_INSTABILITY_DEVELOPMENT_FAIL`. Its immutable run
manifest is `sha256:680dff860ca8215e5544363423ab48149c65158de1f92f94d4b7174c2b83adf8`.
Nine preregistered instability candidates were scored against the 252-case immutable P015 reference
surface. No candidate passed. Fixed relocation, the three order profiles, projection compatibility,
preserved 2021 and 2025 were not reached.

The bounded post-run diagnostic does not expose candidate threshold values, case predictions or
reference rows. It exposes only frozen profile ids and aggregate macro/micro/per-cell metrics.

## Shared observations

1. Every one of the nine candidates has `incremental_true_positive=0` and
   `incremental_false_positive=0` versus its same-profile base prediction. The P012 synthetic
   close-envelope additive channel therefore provides no marginal classification gain anywhere on
   this development surface. It must not be tuned after seeing this result.
2. The global candidate surface shows a stable precision/coverage trade-off rather than one isolated
   near-miss. Broad/lenient profiles recover positives but create excessive false positives; strict
   broad profiles control FPR but lose coverage.
3. The trade-off is not homogeneous across the eight Era × Board cells. Under strict profiles,
   `PRE_STAR/MAIN` and `PRE_STAR/CHINEXT` have high precision but low coverage, while later cells can
   show the opposite failure mode: high coverage with weak precision under less strict profiles.
4. P016 therefore demonstrates a problem in **cross-regime raw-scale comparability**, not a shortage
   of candidate count or reference count. Adding more arbitrary profiles would search the same
   mis-specified scale more densely.

## Short-term trader perspective

A fixed raw width/efficiency/traversal scale is not behaviorally interchangeable across older main-
board conditions, pre/post STAR, ChiNext rule regimes, and modern high-volatility boards. The per-cell
pattern is consistent with that: strict candidates miss older-regime disorder, whereas looser
candidates overfire in later high-variance cells.

Recommendation: do not add more global thresholds. Express the same small topology relative to the
local Era × Board distribution so that “broad”, “expanding” and “directionally inefficient” remain
comparable market-structure statements rather than absolute-number statements.

## Fund-manager perspective

The 252-case complete reserve is already balanced enough to reveal regime dependence. The failure is
not repaired by randomly increasing sample count. Portfolio-style cross-regime robustness should
prefer a representation whose meaning is stable between cells before preserved evaluation.

Recommendation: retain Era × Board as a hard normalization stratum. Treat the 252 cases as consumed
development evidence and keep preserved 2021 untouched for the later out-of-time test.

## Quantitative perspective

The best strict-frontier candidates illustrate the non-dominating trade-off:

- `expansion-lenient / broad-strict`: macro precision about 0.698, coverage about 0.689, FPR about
  0.073;
- `expansion-balanced / broad-strict`: macro precision about 0.765, coverage about 0.621, FPR about
  0.060.

Neither satisfies the unchanged P012 precision >=0.78 and coverage >=0.70 pair, and every candidate
also fails the required envelope incremental-TP condition because the additive channel contributes
zero incremental TP.

Recommendation: open a **new** development experiment rather than relaxing P016. P017 should keep a
small fixed profile topology but calculate feature thresholds from unlabeled distributions inside
each frozen Era × Board stratum. Quantile coordinates must be frozen before scoring and must not be
selected from label outcomes. The envelope additive channel should be retired from successor
eligibility rather than tuned to manufacture marginal positives.

## Warehouse/governance perspective

No new DWD read is required to test this successor idea. P014/P015 already publish the 252 cases,
reviewer-visible primary geometry/path evidence and immutable reference ledgers. A P017 development
runtime can be artifact-only and can bind the exact P015/P016 result lineage.

Recommendation: make the resource unit and normalization lineage explicit:

`case -> Era/Board cell -> unlabeled cell-local basis -> fixed profile coordinate -> prediction -> sealed reference`

The cell-local basis must retain digests and observation counts. No cross-cell fallback, post-result
coordinate change, candidate expansion, rereview, or preserved-data read is allowed.

## Decision

`PASS_OPEN_P017_STRATUM_NORMALIZED_INSTABILITY_SUCCESSOR`

P016 is closed as a valid semantic no-pass. P017 may investigate one abstract successor architecture:

- same immutable 252-case development ceiling;
- artifact-only feature reconstruction;
- Era × Board cell-local **unlabeled** quantile calibration;
- small fixed instability profile topology frozen before scoring;
- fixed P008 relocation unchanged;
- no P016 retry or numeric-gate relaxation;
- P012 envelope additive channel retired from successor eligibility and retained, at most, as a
  diagnostic measurement;
- preserved 2021 and 2025 remain unread.

P017 is a development successor, not an independent validation. Even a development pass cannot claim
out-of-time validity until the separately gated preserved evaluation is executed.
