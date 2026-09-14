# P3-STAGE-013 — Era × Board × Exposure development-resource review

## Scope

This is a same-origin simulated four-responsibility review after the P3-STAGE-012 freshness-capacity closeout. It selects the resource-accounting model for a successor capacity census. It does **not** open market-result data, rerun P012 G1, score candidates, or claim independent expert validation.

Machine decision: `config/stage-learning/stage-development-resource-model.v1.json`.

## Problem restatement

P012 failed because its frozen 2015-cohort route could not construct another 512-identity globally fresh pool after prior whole-pool exclusions. That proves a capacity failure under that exact resource-accounting rule, not a global lack of A-share history.

The successor therefore must decide what counts as an independent development resource. The owner additionally requires board/time interaction to be represented rather than adding random securities. This matters because listing-board microstructure is time-dependent: STAR is absent from early history, and ChiNext changes its price-limit regime on 2020-08-24. A single early-year pool cannot represent those later structural cells.

The repository already has a PIT-capable `dwd_listing.board_code` with `SSE_main`, `SZ_main`, `ChiNext`, and `STAR`. By contrast, current DWD industry membership is not authoritative historical PIT truth across the full horizon; static industry mapping is explicitly concentration-only. Therefore listing board and economic/theme sector cannot be treated as the same governance axis.

## Alternatives

### Option A — Keep whole-identity exclusion and add more old stocks/years

Pros: strongest apparent identity separation from prior work.

Cons: continues burning every historical window of an identity after any prior pool exposure, pushes research progressively farther into old regimes, and still under-represents modern STAR/ChiNext structure. It also preserves the inherited 512-pool shape even when the successor does not need a second 256-identity evaluation half.

### Option B — Reduce 512 to a smaller random pool

Pros: operationally easy.

Cons: directly changes a failed frozen capacity condition without repairing the resource model; random addition does not solve board/time representation and could silently bias the sample toward MAIN.

### Option C — Exposure-aware identity × time-window reuse without board stratification

Pros: stops burning an identity's entire history and cleanly distinguishes development from preserved evaluation.

Cons: a large MAIN population could dominate; early windows cannot represent STAR; the model could pass aggregate gates while failing on structurally different listing boards.

### Option D — Era × board × exposure-aware development reservoir

Pros: treats development reuse and independent evaluation separately, represents board/time interaction explicitly, retains preserved identity-global exclusion only where independence is actually required, and avoids outcome-defined bull/bear sampling labels.

Cons: requires a metadata-only capacity census and a new exposure ledger before the next result-bearing experiment. Historical theme/industry stratification cannot yet be promoted to a hard axis because the source is not authoritative across the full horizon.

## Four-responsibility review

### 1. Short-term trading perspective

**Verdict: SELECT D.**

A path classifier is sensitive to the market's mechanical movement envelope. MAIN, ChiNext, and STAR do not always operate under the same limit/IPO regime, so pooling them without board/time accounting can make identical-looking 20-session geometry mean different things operationally. An early-only expansion is particularly weak because it cannot contain STAR at all.

The reviewer rejects retrospective labels such as “2024 technology bull” as a selection rule. That observation is useful motivation for requiring cross-board robustness, but it cannot be used after the fact to cherry-pick the development sample. The board/time axes should come from known listing/trading rules and PIT metadata.

### 2. Fund-manager perspective

**Verdict: SELECT D with anti-dominance constraints.**

The problem is not only the number of stocks; it is the economic composition of the sample. A proportional random draw from the full universe will mostly test the largest board family. Conversely, equalizing every tiny board-era cell can over-weight a small niche. The appropriate compromise is representation-first allocation: every structurally present board-era cell must appear before additional density is assigned, then remaining capacity is allocated deterministically from metadata-only cell capacity.

The exact preserved-2021 identity plan should remain sealed and globally excluded from successor development. That retains a genuine identity-independent evaluation surface even if development identities are reused across disjoint historical windows.

### 3. Quantitative research perspective

**Verdict: SELECT D; redefine the statistical unit, not the gate after a failure.**

The core correction is to distinguish:

`economic_identity × non_overlapping_time_window × board_at_time × evidence_role`

from the old whole-identity burn-down rule. Metadata discovery and pool reservation are not equivalent to threshold fitting or review/scoring. L0/L1 should not consume the identity. L2-L4 consume the exact development window; another disjoint window may be used for development, but repeated identities must be reported and cannot be counted as identity-independent evidence. L5 preserved evaluation retains identity-global exclusion.

Market eras must be based on exogenous institutional boundaries, not later returns. The selected v1 eras are pre-STAR, STAR before the ChiNext 20% switch, and the STAR + ChiNext 20% regime through 2020-12-31. Exact sampling quotas are intentionally not guessed now; they must be frozen after a metadata-only census and before any market-result read.

### 4. Data / evidence governance perspective

**Verdict: SELECT D; listing board is authoritative now, historical sector is not.**

`dwd_listing.board_code` is the correct current hard stratification source. MAIN is represented as the union of `SSE_main` and `SZ_main`; ChiNext and STAR remain separate. BSE is outside the current Stage source scope.

Historical industry/theme membership cannot currently be a mandatory hard axis across 2012-2020 because the repository explicitly treats the available static industry mapping as non-PIT/concentration-only and DWD historical industry authority begins only at defensible snapshots. Promoting it would create false historical precision. A future PIT historical-sector source may add a sector axis under a separate governed change.

The next action must therefore be a metadata-only census producing counts and exclusion reasons by era × board × exposure level. No price path, morphology, atomic truth, threshold, candidate output, or preserved evaluation data is needed for that census.

## Selected decision

The panel unanimously selects:

> **P3-STAGE-013 — ERA_BOARD_EXPOSURE_AWARE_DEVELOPMENT_RESERVOIR**

with these binding principles:

1. Stop treating every prior pool appearance as permanent whole-identity consumption.
2. Count development resources by identity × non-overlapping time window × board-at-time × evidence role.
3. Keep preserved-2021 identities globally excluded and unread; keep 2025 permanently blind.
4. Keep 2022-2024 closed in v1; do not spend recent history merely to solve a bookkeeping defect.
5. Use listing board as a hard sampling axis; MAIN / ChiNext / STAR must be represented according to structurally present board-era cells.
6. Use institutional/rule-based time eras, not retrospective bull/bear labels.
7. Do not use current/static industry mapping as historical sector truth. Sector stratification waits for authoritative historical PIT data.
8. Freeze exact quotas only after a metadata-only capacity census and before result-bearing data is read.
9. Quota allocation must be deterministic and representation-first; no random append to hit a number.
10. Repeated identities across disjoint development windows are allowed but separately reported and never counted as identity-independent validation.

## Why 2024 technology leadership changes the design but not the sample

The owner's observation exposes a valid weakness: a stock-stage model may interact with cross-board market structure, and a regime dominated by growth/technology boards may stress different geometry than a MAIN-dominated period. The correct response is not to label 2024 as a “technology bull” and train specifically on it after seeing that outcome. The response is to make board-at-time and time regime explicit in the research design so that future holdouts can reveal whether the model is board/regime fragile.

This keeps the insight while avoiding retrospective sample construction.

## Next gate

`P3-STAGE-013-A` is a metadata-only capacity census. It must report usable resources by era × board family × exposure level, including the capacity released by disjoint-window development reuse and the exact impact of the preserved-2021 exclusion. Only after that census may exact quotas and a successor result-bearing development preregistration be frozen.
