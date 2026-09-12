# P3-STAGE-010-D — Fresh Quantitative Development Preregistration

## State

```text
PREREGISTERED / IMPLEMENTATION BEFORE DATA READ
2019 DEVELOPMENT: NOT READ YET
PRESERVED 2021 EVALUATION: STILL UNREAD
2022-2025: SEALED
```

Machine contract:
`config/stage-learning/positive-instability-quantitative-development-screen.v1.json`

Digest:
`sha256:586dda1fe987842cee2f56084697e994bfb44781c998b8a093c3e3678565a6e2`

## 1. Why a new development cohort is required

The P3-STAGE-009 2020 development evidence has already been consumed by a failed 27-candidate screen.
It may explain the shared-core defect but cannot choose P3-STAGE-010 thresholds. P010 therefore uses a
new 2019 cohort from economic identities excluded from every prior exposed pool and from the still-
sealed Stage-009 evaluation identities.

## 2. Preserved evaluation

P3-STAGE-009 D1 already froze a morphology-blind 160-identity 2021 evaluation plan but never read its
market data. P010 preserves that exact plan:

- D1 Job: `stage_93c9590d5df34f5e8f38e242077c1e3c`;
- D1 manifest: `sha256:c49bbf50f01a5319634bdd0e54a1a3716f14e78b462d5570bf3f6c028e64c6b5`;
- evaluation identity digest: `sha256:8bbbd7ca7110d06bfe07db6c2f555cc093b0c1a259aa666e359a637f9157b1e8`;
- evaluation plan digest: `sha256:2800777b5941892a73cf022ddd9375780f2a4f63985f1ce0c1eae9b3668169a7`.

No reselection, replacement or backfill is permitted. The plan cannot be opened before D2 freezes one
candidate from the new 2019 development cohort.

## 3. New 2019 development pool

D1 will build a fourth fresh pool after excluding Stage-007, Stage-008 and the **entire** Stage-009
512-identity pool. From 256 development-capacity identities it plans 160 morphology-blind cases,
40 per quarter, one case per economic identity. Selection uses only stable hashes and assigned quarter;
no morphology-dependent backfill occurs.

Threshold basis uses all available unlabeled 2019 primary-scale observations from those development
identities. Reference opinions are not visible while quantile thresholds are constructed.

## 4. Nine-candidate budget

Search is intentionally smaller than P3-STAGE-009:

```text
3 expansion profiles × 3 broad-reversal profiles = 9 candidates
```

Profiles are bundled quantile coordinates. Individual thresholds are not independently optimized and
the grid cannot expand after results.

Expansion profiles vary channel-specific relative expansion, low amplitude floor, absolute width gain
and path-disorder thresholds. Broad profiles separately vary the material broad-range floor and the
three two-of-three path-disorder thresholds.

## 5. Gates

Development requires at least 120 complete cases, 18 definite Stage-0 positives, 70 definite
negatives and 15 predictions. The candidate must reach:

- precision >= 0.78;
- positive coverage >= 0.70;
- definite-negative FPR <= 0.10;
- no Stage-2 prediction change;
- no worse precision/FPR than the frozen 008 baseline;
- absolute coverage loss versus baseline <= 0.10;
- remove at least one baseline false positive when baseline has any.

If no candidate passes, D closes `REVISE_OR_PARK_DEVELOPMENT_FAIL` and the preserved 2021 evaluation
remains unread.

If one candidate is frozen, D3 may read only the exact preserved 2021 plan. Evaluation then requires
precision >=0.80, coverage >=0.65 and FPR <=0.10 with the same no-regression constraints. Even a pass
claims only `PROMISING_EXPLORATORY_REPAIR`.

## 6. Authority ceiling

This preregistration does not itself authorize data access. D1/D3 implementation, source isolation,
one-shot budgets and candidate-blind Primary review transport must first pass engineering acceptance on
a durable commit. 2022–2025, official labels, strategy, daily activation and trading remain closed.
