# P3-STAGE-022 — Primary-Unique / Contingent-Fallback Resource Contract

Design status: closed `CLOSED_DORMANT_FALLBACK_POOL_SHORTFALL`

Delivery gate: P023 admission-only successor recommended; P020 remains blocked

Parent: `stage-admission-exposure-taxonomy.md`  
Machine contract: `../../config/stage-learning/stage-primary-unique-contingent-fallback.v1.json`  
Execution contract: `../../config/stage-learning/stage-primary-unique-contingent-fallback-feasibility.v1.json`

Result: `../../config/stage-learning/stage-primary-unique-contingent-fallback-result.v1.json`

Review: `../reviews/stage-p022-primary-unique-contingent-fallback-review-20260916.md`

## Goal

Resolve the remaining P021 `243/256` reserve-identity shortfall without weakening the scientific
independence of realized Stage1/12 review cases. The active review target remains exactly 24 cases in
each of the eight Era × Board cells, or 192 cases total, and every active case must have a globally
unique economic identity.

The 64 historical fallback slots are reclassified as **contingent reservations**, not active cases.
A dormant fallback does not consume a review identity merely by being reserved. If a later
input-admission step needs a replacement, fallback activation must stay in the same cell and preserve
global identity uniqueness against all realized primaries and any already activated fallbacks.

## Frozen semantics

### Primary panel

- 24 primary resources per cell, eight cells, 192 total;
- 192 economic identities globally unique;
- no cross-cell fill;
- exact identity-window chosen from the repaired P021 metadata reservoir only;
- metadata-only deterministic matching and window selection;
- no post-result replacement.

### Contingent fallback pools

- eight dormant candidates per cell, 64 candidate rows total;
- every fallback identity must be outside the 192 primary identities;
- same-cell activation only;
- cross-cell identity overlap is legal while fallbacks remain dormant;
- activation can occur only for technical input-readiness failure;
- activation order is frozen before any admission result;
- each activation must re-check global identity uniqueness against the realized panel;
- inability to activate enough fallbacks is a later fail-closed resource outcome, never permission to
  change cell quota, add years or use semantic outcomes.

Worst-case simultaneous activation of all 64 fallbacks is diagnostic only. Requiring all 64 dormant
reservations to be globally unique would recreate the P021 `8×32=256` pre-consumption problem.

## Work sequence

| ID | Work | Dependency | Exit |
| --- | --- | --- | --- |
| P3-STAGE-022-A | Freeze primary/fallback semantics | P3-STAGE-021 | closed result-before-run contract |
| P3-STAGE-022-B | Freeze exact metadata primary plan and dormant fallback pools | P3-STAGE-022-A | closed `CLOSED_DORMANT_FALLBACK_POOL_SHORTFALL`; 192/192 primary globally unique PASS, dormant fallback 62/64; early STAR only 6/8 |
| P3-STAGE-022-C | Four-party review feasibility | P3-STAGE-022-B | closed; preserve P022 no-pass and move identity uniqueness from planned resources to realized admission-ready panel |
| P3-STAGE-022-D | Close and gate any admission successor | P3-STAGE-022-C | closed `OPEN_REALIZED_IDENTITY_ADMISSION_AND_CONTINGENT_REPLACEMENT`; P023 must be separately frozen; P020 remains blocked |

## Boundaries

P022 read no market bars, admission results, Stage1/12 semantics, thresholds, review opinions,
preserved 2021, 2022-2024 or 2025. It does not reopen P020. The exact P022 primary plan is valid
historical evidence, while the preregistered fallback pool gate failed at 62/64 and must not be
reinterpreted as PASS. Only a separately frozen P023 admission-only successor may use P022 evidence.
