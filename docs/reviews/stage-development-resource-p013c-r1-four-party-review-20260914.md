# P3-STAGE-013-C-R1 — Four-party review of metadata-plan failure

Status: `APPROVE_CAPACITY_CONSTRAINED_TEMPORAL_SPREAD_R1`

This is a same-origin simulated review across four responsibilities. It is not independent-model or human-expert validation. The review sees only the metadata-only P013-A capacity result and the failed P013-C stack trace. No price path, morphology, label/reference, candidate output, preserved-2021 market row, 2022-2024 or 2025 data is opened.

## Observed failure

The sole P013-C exact-plan Job `stage_6fff29f632534045a0c67bf6f175474f` failed before artifact publication with:

`cell_global_identity_shortfall:STAR_PRE_CHINEXT_20PCT/STAR`

P013-A had already established 57 exposure-aware available unique identities for that era-board cell, which is greater than the frozen cell reserve of 32. Therefore the failure does not establish cell-level identity scarcity. It exposes a stronger implementation/policy assumption: the 32 reserves were pre-slotted almost equally across all four 61-session metadata windows, effectively requiring roughly eight unique identities in every window after identity de-duplication.

## Short-term trading perspective

Board-era representation remains important because early STAR trading conditions differ materially from later STAR and from MAIN/ChiNext. However, requiring identical density in every 61-session launch-era window is not economically necessary. The launch sequence itself creates changing listing breadth; forcing equal counts can turn board rollout mechanics into a sampling blocker.

Recommendation: retain STAR as a hard board axis and retain chronological spread, but allow sparse windows to exhaust naturally. Do not compensate from another board or later result.

## Fund-manager perspective

The equal eight-cell macro design remains preferable to population-proportional sampling because the objective is semantic stress, not market-cap or listing-count prevalence. Within a cell, however, the exact calendar-window density is a secondary representation concern. A capacity-constrained spread is more faithful than fabricating equal temporal density where the investable/listed universe did not yet support it.

Recommendation: keep 24 primary + 8 fallback per cell and 256 globally unique identities. Do not reduce STAR's cell quota and do not overweight later windows using price outcomes.

## Quantitative research perspective

The original census measured union-level unique identity capacity by cell; it did not prove a Hall-style feasibility condition for an exact 8/8/8/8 unique-identity assignment across four overlapping window candidate sets. The P013-C failure is therefore a sampling-design feasibility miss, not evidence against the exposure-aware resource model.

A deterministic capacity-constrained max-min waterfill is acceptable because it uses only metadata availability. At each step the least-filled non-exhausted window is preferred; scarcity ties are resolved by remaining unique-identity capacity, and identity selection protects inflexible identities first. This preserves temporal breadth as far as the actual candidate graph permits without introducing random or result-aware replacement.

Recommendation: remove the fixed maximum per-window count difference of one. Preserve the cell quota, frozen seeds, global identity uniqueness, no cross-cell fill and no post-result replacement.

## Data / evidence governance perspective

The first P013-B freeze and failed P013-C job must remain immutable evidence. The correction must be additive: `P3-STAGE-013-B-R1` revises only temporal spread semantics, and `P3-STAGE-013-C-R1` authorizes at most one metadata-only recovery job. The recovery must bind the original failed Job, original plan config, parent quota freeze, exact P013-A census artifact and protected surfaces.

No result-bearing authority is created. The recovery may not read prices, morphology, labels/references, candidate outputs, preserved 2021, 2022-2024 or 2025.

## Decision

All four responsibilities approve:

`CAPACITY_CONSTRAINED_MAX_MIN_WATERFILL_V1`

Frozen invariants:

- eight structurally present era-board cells;
- 32 reserves per cell;
- 24 primary + 8 pre-frozen fallback per cell;
- 256 globally unique economic identities in the current batch;
- identical identity/window seeds;
- no cross-cell fallback;
- no post-result identity replacement;
- no market/prevalence claim;
- no price/morphology/reference/candidate read during plan recovery.

The only revision is the within-cell temporal allocation rule. A window with no remaining eligible globally unused identity may exhaust; remaining slots stay within that same era-board cell. If the cell union cannot provide 32 globally unique identities, the recovery fails before market read.
