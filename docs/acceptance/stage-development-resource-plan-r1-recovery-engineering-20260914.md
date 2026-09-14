# P3-STAGE-013-C-R1 — Metadata plan recovery engineering acceptance

Disposition: `PASS_ENGINEERING_READY_FOR_ONE_METADATA_R1_RECOVERY`

## Failure retained

The sole initial P013-C metadata-plan Job remains immutable:

- Job: `stage_6fff29f632534045a0c67bf6f175474f`
- revision: `d1008c653b5675b69ed2c9b1a51c2af8a5e88d9d`
- input digest: `sha256:95f1f38502ce37df181fd786cf3d3a2329c50b3c9827fe9f0fbd3bc2f5af9550`
- terminal state: failed
- governed artifact: none
- exception: `cell_global_identity_shortfall:STAR_PRE_CHINEXT_20PCT/STAR`

No market price, morphology, label/reference, candidate output, preserved-2021 market row, 2022-2024 or 2025 data was read by this Job.

## Attribution

P013-A proved 57 exposure-aware available unique identities in the binding early-STAR cell, above the frozen cell reserve of 32. The initial exact-plan implementation nevertheless pre-slotted 32 assignments nearly equally across four 61-session windows. That stronger window-level equality condition was not proven by the census and exhausted one sparse launch window before the cell reserve could be completed.

The failure therefore does **not** justify reducing the STAR quota, crossing into another cell, appending random identities, or opening later data. It justifies replacing strict near-equal per-window slotting with the separately reviewed metadata-only capacity-constrained temporal spread.

## R1 frozen repair

`config/stage-learning/stage-development-resource-quota-revision-r1.v1.json` keeps unchanged:

- eight structurally present era-board cells;
- 32 reserves per cell;
- 24 primary + 8 pre-frozen fallback per cell;
- 256 total reserves and 192 primary review cases;
- current-batch global economic-identity uniqueness;
- frozen identity/window seeds;
- no cross-cell fallback;
- no post-result replacement.

Only within-cell window assignment changes to `capacity_constrained_max_min_waterfill_v1`. Least-filled non-exhausted metadata windows are preferred; candidate scarcity and frozen hashes deterministically resolve ties. Sparse windows may exhaust, but all remaining assignments stay inside the same era-board cell.

## Recovery authority

`config/stage-learning/stage-development-resource-plan-recovery.v1.json` binds the exact failed Job, original plan config, parent quota freeze, R1 quota revision and P013-A census artifact. It authorizes at most one metadata-only recovery Job and no further automatic retry.

The worker keeps the original one-shot budget marker and writes a separate recovery budget. A second recovery revision is rejected.

## Verification

Focused tests cover:

- original strict allocation failing on a synthetic sparse launch window;
- R1 waterfill completing the same 32-cell quota without cross-cell fill;
- global identity uniqueness and 24/8 role counts;
- exact failed-parent binding before recovery;
- one recovery budget only and same-revision idempotence;
- rejection of a third revision;
- bounded failure diagnostics.

Claim ceiling: this acceptance permits one metadata-plan R1 recovery only. It does not authorize result-bearing market reads or qualify any atomic proposition, candidate or Stage projection.
