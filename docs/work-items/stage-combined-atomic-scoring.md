# P3-STAGE-016 — Combined Atomic Scoring and Projection Compatibility

Design status: approved  
Delivery gate: open for one artifact-only independent atomic-scoring Job

Parent: `stage-complete-reserve-reference.md`  
Machine scoring contract: `../../config/stage-learning/stage-combined-atomic-scoring.v1.json`
Four-party review: `../reviews/stage-p016-combined-scoring-four-party-review-20260915.md`

## Goal

Use the immutable P015 252-case blind-reference ledger to answer the candidate-quality question that
P014/P015 deliberately did not open. Preserve the exact P012 9 / 1 / 3 candidate topology, numeric
quality gates, fixed relocation guard and resolver while honoring the P014 preregistered Era × Board
macro-primary aggregate.

P016 does not reopen data selection or reference construction. It is an artifact-only scoring route.

## Frozen aggregate contract

The P012 numeric gate values are unchanged. Their aggregation semantics are made explicit before
candidate access:

- reference positive/negative minima and minimum prediction counts are pooled counts;
- instability incremental TP/FP requirements remain pooled counts;
- precision, positive-reference coverage and definite-negative FPR are equal-weight Era × Board
  macro rates;
- a cell with zero definite predictions contributes precision 0 rather than disappearing;
- coverage averages only cells with at least one definite positive reference;
- FPR averages only cells with at least one definite negative reference;
- pooled micro ratios, per-cell, per-era and per-board-family results are diagnostics only;
- no new per-cell pass threshold exists.

This resolves the latent P014-vs-P012 implementation mismatch before reading candidate values.

## Artifact closure

P016-B may read only:

- P014 semantic artifact manifest
  `sha256:5926d73a04e37b74f7025c2f38314898ee7c5414aa45fe0686de20357ce3c388`;
- P015-C 60-case blind-evidence artifact manifest
  `sha256:8219bb18b1c5c3236fa5e3dd1ec0ea5112dfc101e286d4dbdab1c72c3d3c8d59`;
- frozen P014 review ledger
  `sha256:f17063c28e0f75a8ae5280d6ee66b31f9b9ffc318896a3ae8b7e48efc101ed69`;
- frozen P015 incremental ledger
  `sha256:db3216d322e00ad40ebd7de435684b43bcaf72cbc66a90dcb49403a8be461e06`;
- unchanged P012/P014 repository contracts.

The P014 threshold basis was built from unlabeled observations inside all 256 qualified exact windows
before case selection and review. P016 regenerates the 9 instability and 3 order candidate identities
from that persisted basis and requires byte-semantic equality with the persisted grid before scoring.
No threshold recomputation or grid expansion is allowed.

Old 192 envelope measurements come from the immutable P014 artifact. The new 60 envelope traces are
reconstructed from the already-published 21-close reviewer histories with the exact P012 synthetic
close-envelope rule. No DWD read occurs.

## Work sequence

| ID | Work | Dependency | Exit |
| --- | --- | --- | --- |
| P3-STAGE-016-A | Resolve aggregate semantics and freeze artifact-only scoring contract | P3-STAGE-015 | closed `PASS_SELECT_MACRO_RATE_POOLED_COUNT_TWO_JOB_SCORING_ROUTE`; no candidate values read |
| P3-STAGE-016-B | Independently score/freeze I, fixed R and O | P3-STAGE-016-A | ready `PASS_ENGINEERING_READY_FOR_ONE_ARTIFACT_ONLY_ATOMIC_SCORING_JOB`; one Job, 9 I + fixed R + 3 O, zero joint pair search, no projection; `../acceptance/stage-combined-atomic-scoring-engineering-20260915.md` |
| P3-STAGE-016-C | Projection compatibility on B's frozen predictions | P3-STAGE-016-B | blocked until B publishes an eligible atomic freeze; no candidate alternatives or reselection |
| P3-STAGE-016-D | Close and gate preserved evaluation | P3-STAGE-016-C | may authorize a separate preserved-2021 preregistration only if all frozen development/projection gates pass |

## P016-B failure semantics

P016-B is a scoring Job, not a search loop. It closes normally with a no-pass disposition if:

- reference closure differs from the immutable P015 result;
- no instability candidate passes the unchanged gates;
- the fixed relocation guard fails;
- no order profile passes.

These are semantic results, not technical retry authority. A process failure permits no retry unless a
separate technical-recovery contract is frozen before another Job.

## P016-C boundary

P016-C will bind the exact P016-B artifact after B completes. It may consume only the single frozen I,
fixed R and single frozen O predictions, apply the unchanged P012 resolver and projection gates under
the same pooled-count / macro-rate semantics, and publish pass/fail diagnostics. It cannot read
candidate alternatives or change the B freeze.

Even a P016-C pass does not read preserved 2021. It only permits a separately versioned preserved-
evaluation preregistration.

## Claim ceiling

P016 can establish bounded development candidate quality and projection compatibility on the immutable
252-case balanced Era × Board reference surface. It cannot establish preserved out-of-time validity,
market prevalence, economic value, strategy value, production suitability or trading authority.
