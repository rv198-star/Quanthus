# P3-STAGE-021 — Admission-Only Exposure Taxonomy Repair

Design status: approved  
Delivery gate: metadata-only capacity route open

Parent evidence: `../../config/stage-learning/stage-downside-lifecycle-resource-result.v1.json`  
Taxonomy: `../../config/stage-learning/stage-admission-exposure-taxonomy.v1.json`  
Capacity contract: `../../config/stage-learning/stage-admission-exposure-capacity.v1.json`  
Review trigger: `../reviews/stage-p020b-resource-shortfall-four-party-review-20260915.md`

## Goal

Repair one governance mismatch exposed by P020-B without changing the Stage1/12 semantic design or
consuming newer data. The P013 exposure model treated any development market read as L2, but P014
introduced a deterministic input-admission layer that intentionally reads bars only to verify input
readiness while retaining/publishing no OHLC and computing no Structure facts, threshold basis,
candidate output or reviewer semantics.

P021 distinguishes that technical read from semantic development exposure before any repaired
capacity result is computed.

## Frozen taxonomy

### L2A — technical input-admission read

An exact window qualifies as L2A only when it was read by the P014 admission screen and **never**
entered the later P014 qualified semantic plan.

It may be reused for later semantic development under exact provenance. It is not called "unread":
reports must retain the fact that a technical market-input read occurred.

### L2B — semantic development read

Every exact P014 qualified-plan window remains consumed because later P014/P015/P016 work built
semantic facts, threshold basis and/or review/scoring evidence over that panel. Reuse is disjoint-
window only under the existing P013 rule.

P013 exact-plan windows remain consumed. Preserved 2021 identities remain globally excluded.

## Work sequence

| ID | Work | Dependency | State |
| --- | --- | --- | --- |
| P3-STAGE-021-A | Freeze L2A/L2B taxonomy before capacity results | P3-STAGE-020-B | closed taxonomy freeze |
| P3-STAGE-021-B | Metadata-only old-vs-repaired capacity recomputation | P3-STAGE-021-A | closed `CLOSED_REPAIRED_TAXONOMY_GLOBAL_IDENTITY_SHORTFALL`; all eight cells individually recover ≥32 identities, but unchanged 8×32 global uniqueness reaches 243/256; `../../config/stage-learning/stage-admission-exposure-capacity-result.v1.json` |
| P3-STAGE-021-C | Four-party review of repaired capacity | P3-STAGE-021-B | closed; keep final active-case identity independence, but do not spend global uniqueness on dormant fallback reservations; `../reviews/stage-p021-admission-exposure-capacity-review-20260915.md` |
| P3-STAGE-021-D | Close P021 and gate any P020 resource successor | P3-STAGE-021-C | closed `RECOMMEND_P022_PRIMARY_UNIQUE_CONTINGENT_FALLBACK`; P020 remains blocked |

## P021-B contract

The Job verifies immutable P013/P014 artifacts and reconstructs only the accepted listing/calendar
metadata reservoir. It compares:

```text
old consumed = P013 exact plan + all P014 admission-read exact windows
new consumed = P013 exact plan + P014 qualified semantic-plan exact windows
```

It reports exact restored L2A capacity per Era × Board cell and checks the unchanged 32 identities per
cell / 256 globally unique metadata-reserve feasibility. It cannot construct a new P020 semantic plan
or run admission.

## Boundaries

P021 cannot change the eight cells, 32/24+8 quota, historical horizon, board scope or protected
partitions. It cannot read market bars, Stage1/12 semantics, thresholds, reviewer opinions, 2021,
2022-2024 or 2025. A successful P021-B capacity result still does not reopen P020; P021-C/D must first
review and close the governance decision.
