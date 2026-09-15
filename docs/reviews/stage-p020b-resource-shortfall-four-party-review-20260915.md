# P3-STAGE-020-B — Resource Shortfall Four-Party Review

Design status: approved review  
Delivery gate: close P020-B fail-closed; open governance-only P021

The roles below are same-origin simulation, not independent human experts.

## Evidence

The exact R1 recovery Job `stage_1b0888224e684c7c9593c881ae01d4d4` published a metadata-only
artifact before any 61-session market admission read. Under the frozen P020-B rule that excludes all
P013 exact-plan windows and every exact window ever read by the P014 admission screen:

- `STAR_PRE_CHINEXT_20PCT / STAR`: 0 remaining identity / 0 window;
- `STAR_AND_CHINEXT_20PCT / STAR`: 0 / 0;
- the other six cells retain 85,203 metadata candidate windows in aggregate;
- no Stage1/12 reference, threshold, candidate, preserved partition or admission bar was read.

The result therefore identifies a governance-capacity boundary, not a semantic/data-volume result.

## Short-term trader

Dropping STAR to make the batch fit would directly defeat the time × board representation principle
that motivated P013. The two STAR cells are structurally important and should remain hard strata.
Do not reduce the 32-per-cell quota or replace STAR with MAIN simply because the governance rule
burned the available windows.

## Fund manager

Opening 2022–2024 should not be the first response. Those years remain valuable modern development
and future-comparison capital. Before consuming them, distinguish a purely technical eligibility read
from a research read that actually shaped propositions, thresholds or reviews. Reuse of unexposed
admission-only windows may preserve both representation and newer data.

## Quant

The current exposure taxonomy is too coarse for the pipeline architecture created after P013.
P014 admission was deterministic, candidate/threshold/reference blind, retained no OHLC, and existed
specifically to separate input readiness from semantic development. Treating that technical read as
identical to a semantic development exposure creates an avoidable contradiction.

However, the rule was frozen before P020-B and the observed capacity shortfall is now known. It must
not be silently relaxed inside P020-B. A separate result-free governance work item should define an
`admission_only` exposure class ex ante, then mechanically recompute capacity. P014 windows that later
entered threshold-basis / review / scoring must remain consumed at their stronger exposure level.

## Data governance

Keep the original P020-B result immutable. The successor should refine, not erase, P013 exposure
semantics:

```text
L2A technical_input_admission_read
    raw bars read only to verify deterministic input readiness
    no OHLC retained/published
    no structure facts, threshold basis, candidate output or review opinion
    reusable for later semantic development under exact provenance

L2B semantic_development_read
    semantic facts or model/candidate basis computed
    exact window consumed; only disjoint-window reuse allowed
```

P014's exact 256 qualified plan must remain consumed because the later P014/P015/P016 pipeline built
threshold basis and/or reviewer evidence over that panel. Admission-ledger windows that never entered
the semantic panel may be eligible for reuse after P021 freezes the distinction.

## Decision

```text
P3-STAGE-020-B: CLOSED_FRESH_WINDOW_CAPACITY_SHORTFALL_BEFORE_ADMISSION
P3-STAGE-021:   OPEN_GOVERNANCE_ONLY_ADMISSION_EXPOSURE_TAXONOMY_REPAIR
```

P021 must not score Stage1/12, read preserved partitions, reduce the per-cell quota, drop STAR, add new
years, or choose resources based on semantic outcomes. It may only reclassify the already-known
technical admission exposure boundary and rerun metadata capacity under the newly frozen taxonomy.

## Claim ceiling

This review does not pre-approve reuse of P014 admission windows. It only selects the exposure-
taxonomy question as the next bounded governance problem. Reuse becomes legal only after P021
machine contracts and verification pass before any new semantic result is observed.
