# P3-STAGE-017 — Era × Board Stratum-Normalized Instability Development

Design status: approved  
Delivery gate: open for one artifact-only development scoring Job

Parent: `stage-combined-atomic-scoring.md`  
Machine contract: `../../config/stage-learning/stage-stratum-normalized-instability.v1.json`
Upstream no-pass review: `../reviews/stage-p016-instability-no-pass-four-party-review-20260915.md`

## Goal

Test one abstract successor hypothesis after the valid P016 semantic no-pass: the existing fixed 3 × 3
instability profile coordinates may be structurally useful, but one global raw geometry scale is not
comparable across the frozen Era × Board cells.

P017 does **not** retry P016. It creates a new artifact-only development experiment. Each of the eight
frozen cells receives its own unlabeled quantile basis; the same fixed expansion/broad profile
coordinates are then instantiated against those eight bases and evaluated as nine shared profiles.

## Frozen boundaries

- The development ceiling is exactly the immutable P015 252-case reference/evidence surface.
- No new securities, random sampling, replacement, rereview or DWD read is allowed.
- All eight cell-local bases and all 9 × 8 threshold instances are constructed before review ledgers
  are opened by the runtime.
- Quantile construction uses no labels, reference opinions or candidate outcomes.
- The 3 × 3 profile coordinates are the exact existing P012/P010 instability profile coordinates;
  there is no denser candidate search.
- The P016 zero-gain close-envelope additive channel is retired from P017 eligibility. Its
  mechanism-specific incremental-TP requirement is therefore not numerically changed to zero; it is
  structurally not applicable because the mechanism itself is absent.
- The remaining instability quality gates keep the unchanged P012 numeric values: pooled minimum
  predictions 18; equal-weight Era × Board macro precision >=0.78, positive-reference coverage
  >=0.70 and definite-negative FPR <=0.10.
- P017 scores instability only. Fixed P008 relocation, order profiles, Stage projection and
  preserved evaluation are separate later gates.
- Preserved 2021, 2022–2024 and 2025 remain unread.

## Execution order

The runtime order is itself part of the blinding contract:

1. Verify P016/P015/P012 repository bindings and the two immutable evidence artifacts.
2. Reconstruct only the 252 already-published reviewer-visible cases and frozen Era × Board strata.
3. Build all eight unlabeled cell-local bases and the complete 9-profile cell threshold grid.
4. Only after step 3, open the immutable P014/P015 reference ledgers and verify exact reference counts.
5. Score all nine fixed profile coordinates under the unchanged pooled-count/macro-rate contract.
6. Freeze at most one eligible instability profile; otherwise close semantic no-pass.

No automatic retry is authorized after a semantic result.

## Work sequence

| ID | Work | Dependency | Exit |
| --- | --- | --- | --- |
| P3-STAGE-017-A | Freeze normalization/topology/blinding semantics | P3-STAGE-016 | ready design: exact 252 surface, eight unlabeled cell bases, fixed 3×3 coordinates, no envelope eligibility |
| P3-STAGE-017-B | Run artifact-only stratum-normalized instability scoring | P3-STAGE-017-A | ready `PASS_ENGINEERING_READY_FOR_ONE_STRATUM_NORMALIZED_INSTABILITY_JOB`; one Job, 9 profiles, no R/O/projection/preserved read; `../acceptance/stage-stratum-normalized-instability-engineering-20260915.md` |
| P3-STAGE-017-C | Four-party semantic outcome review | P3-STAGE-017-B | blocked until B publishes a terminal semantic result |
| P3-STAGE-017-D | Close and decide successor gate | P3-STAGE-017-C | blocked; any further atomic/projection work requires a separate freeze |

## Pass / fail meaning

A P017-B pass means only that one fixed profile coordinate satisfies the unchanged instability quality
gates when calibrated with unlabeled cell-local distributions on this **development** reference set. It
does not imply relocation/order/projection compatibility or preserved out-of-time validity.

A P017-B no-pass is a semantic result. It cannot authorize adding profiles, changing quantiles,
lowering gates, reintroducing/tuning envelope logic or reopening P016.

## Claim ceiling

P017 can establish bounded development instability quality under an Era × Board-local unlabeled
normalization scheme. It cannot establish preserved validation, market prevalence, economic value,
strategy value, production suitability or trading authority.
