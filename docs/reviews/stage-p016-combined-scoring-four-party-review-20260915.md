# P3-STAGE-016 Combined Atomic Scoring — Four-Party Review

Status: `PASS_SELECT_MACRO_RATE_POOLED_COUNT_TWO_JOB_SCORING_ROUTE`

This is a same-origin simulated review by the four standing Quanthus perspectives: short-term
trading, fund management, quantitative research and data/governance. It is not four independent
human or model reviews.

## Question

P015 closed reference sufficiency on an immutable 252-case blind ledger. The next step must score the
unchanged P012 9 / 1 / 3 atomic surfaces without reopening thresholds, changing gates, re-reviewing
references or touching preserved 2021 prematurely.

One latent mismatch must be resolved before candidate access: P014 preregistered
`macro_equal_weight_across_eight_era_board_cells` as the primary aggregate and pooled micro as
secondary descriptive output, while the reusable P012 scorer implements pooled micro metrics only.
P014 never reached scoring, so this mismatch has never been exercised against candidate results.

## Short-term trading view

Era × Board stratification must remain decision-bearing. MAIN has much more natural sample capacity
than STAR or ChiNext; allowing pooled micro quality ratios to decide the candidate would partially
undo the representation-first resource design. A candidate that works mainly in one board/era and is
silent elsewhere should not obtain a strong primary score merely through pooled counts.

Recommendation: keep the P012 numeric thresholds but apply ratio gates to equal-weight cell metrics.
No new per-cell pass thresholds are needed.

## Fund-manager view

Reference and prediction **counts** should remain global. The existing P012 minimum positive/negative
references and minimum prediction counts are denominator safeguards, not portfolio weights. Changing
them into per-cell minima would create a new gate and over-penalize naturally sparse atomics.

Recommendation: pooled counts retain the exact P012 values; precision, positive-reference coverage
and definite-negative false-positive rate are macro quality ratios. Pooled micro ratios remain
reported for diagnostics only.

## Quantitative view

The aggregate contract is frozen as follows before reading candidate values:

- build the same P012 confusion counts per Era × Board cell;
- pooled reference-count and prediction-count gates remain exact P012;
- macro precision is the equal-weight mean over all eight cells, with a zero-prediction cell assigned
  precision 0 rather than silently disappearing;
- macro positive-reference coverage is the equal-weight mean across cells that actually contain at
  least one definite positive reference for that proposition/class;
- macro definite-negative FPR is the equal-weight mean across cells containing at least one definite
  negative reference;
- the same P012 numeric ratio thresholds are applied to those macro values;
- instability incremental TP / FP requirements remain pooled count requirements exactly as P012;
- candidate ranking is macro precision descending, macro FPR ascending, macro coverage descending,
  then the original pooled tie-breakers and stable candidate id;
- pooled micro metrics and all per-cell metrics are mandatory diagnostics, not alternate gates.

Projection compatibility uses the same split: pooled count/prediction minima, macro quality ratios,
and macro equal-weight ambiguous-conflict rate across all eight cells. It may not trigger any
candidate reselection.

## Data / governance view

The scorer must be artifact-only:

1. P014 semantic artifact is immutable at manifest
   `sha256:5926d73a04e37b74f7025c2f38314898ee7c5414aa45fe0686de20357ce3c388`.
   Its threshold basis was constructed from unlabeled observations inside all 256 qualified windows
   before case selection and before review. The candidate grid was generated from that basis before
   any reference opinion existed.
2. P015-C artifact is immutable at manifest
   `sha256:8219bb18b1c5c3236fa5e3dd1ec0ea5112dfc101e286d4dbdab1c72c3d3c8d59` and contains the exact 60
   new reviewer-visible cases.
3. The P014 192-case and P015 60-case review ledgers remain immutable by SHA. All 252 case ids must map
   one-to-one to one frozen Era × Board stratum.
4. Old 192 envelope measurements are reused from the P014 artifact. New 60 envelope measurements are
   reconstructed only from the already-published evidence history using the exact P012 synthetic
   close-envelope rule. No DWD read and no learned envelope threshold are permitted.
5. Threshold basis and candidate-grid values remain unopened until the scoring preregistration is
   committed and deployed.

## Selected execution shape

Use two separate governed Jobs.

### P016-B — independent atomic scoring

Read the immutable basis/grid only after the scoring contract is frozen. Score all 9 instability
candidates independently, score the fixed relocation guard, and score all 3 order profiles
independently. Freeze at most one instability candidate and one order profile using the macro-primary
ranking above. There is no joint I×O search and no projection in this Job.

If reference sufficiency unexpectedly differs, no I candidate is eligible, fixed relocation fails,
or no O profile is eligible, close fail-closed without a projection Job.

### P016-C — projection compatibility

Consume only P016-B's frozen I/R/O predictions and immutable references. Apply the unchanged P012
resolver and projection gates under the macro-rate / pooled-count aggregate contract. Do not read
candidate alternatives and do not reselect after projection.

A projection PASS may authorize a **separate** preserved-2021 preregistration. It does not read 2021
inside P016-C.

## Rejected alternatives

- pooled micro as the primary quality gate — conflicts with the already-frozen P014 aggregate;
- new per-cell positive/negative count gates — changes P012 numeric gate semantics;
- threshold recomputation on 252 reviewed cases — result-driven retuning;
- expanding 9 / 1 / 3 candidates — post-result candidate search;
- joint I×O ranking — violates P012 independent atomic selection;
- one Job that scores, projects and then repicks — violates no-reselection discipline;
- new DWD reads for the 60 envelope traces — unnecessary and weakens artifact closure.

## Decision

`PASS_SELECT_MACRO_RATE_POOLED_COUNT_TWO_JOB_SCORING_ROUTE`

The review authorizes engineering/preregistration only. It does not authorize candidate access until
the machine scoring contract and runtime are independently verified and durably committed.
