# P3-STAGE-013-B — Four-party review of observed capacity and quota freeze

## Scope

This review consumes only the verified metadata-only `P3-STAGE-013-A` capacity census. It does not read price paths, Structure morphology, atomic references, candidate outputs, preserved-2021 market rows, 2022-2024 market rows or 2025. The four perspectives are same-origin simulated responsibilities, not independent humans or independent models.

Observed Job:

- `stage_0d24e5a8158445fabb2ae26bec742ada`
- code revision `cf3aae3f209e65d8cb6b0c220d6e49e0a7375141`
- artifact manifest `sha256:354a936dc9dc7262e1cc4f7377e5048cfa8a00b8d9585bb3fe7d39ede47c03c9`
- terminal state `succeeded`

The census found 4,194 economic identities and 104,833 metadata-eligible identity-window tokens. After the frozen exposure and preserved-evaluation rules, 90,067 identity-window tokens remain available, versus only 18,967 under the legacy whole-identity burn-down. The new accounting therefore releases 71,100 development-capacity tokens and 2,795 economic identities that the old rule would have discarded.

The scarce cell is `STAR_PRE_CHINEXT_20PCT / STAR`: 57 available unique identities and 132 available identity-window tokens. The later STAR cell has 102 available unique identities. Every structurally present era-board cell therefore exceeds a 32-identity reserve target before any result-bearing read.

## Short-term trading perspective

**Verdict: PASS / equal cell representation is preferable to population-proportional sampling.**

The board axis is not cosmetic. MAIN, ChiNext and STAR operate under materially different trading-rule structures, and the 2019-07-22 / 2020-08-24 boundaries represent real changes in what price paths can look like. A population-proportional draw would allow MAIN to dominate and would leave the two STAR regimes as diagnostic footnotes.

The proposed 24 primary cases per structurally present cell is sufficient for a bounded semantic-development screen because the objective is to expose the atomic propositions to distinct trading environments, not to estimate how often each board appears in the full market. Eight pre-frozen fallbacks per cell are acceptable only because they are fixed before prices or references are read and may replace input-unavailable cases only within the same cell.

**Veto:** no post-result random replacement, no cross-cell fallback, and no use of “technology bull”, “small-cap bull” or other outcome-defined market narratives as sample labels.

## Fund-manager perspective

**Verdict: PASS / treat the design as balanced stress coverage, not market prevalence.**

Equal era-board quotas deliberately overrepresent scarce but structurally important regimes relative to their market capitalization or listing count. That is appropriate for development of a semantic state recognizer: a state proposition that only works on the large MAIN population is not robust merely because MAIN is numerous.

The review rejects interpreting the balanced sample as an estimate of A-share stage prevalence. Aggregate reporting must therefore use an equal-cell macro view as the primary diagnostic and keep pooled micro metrics secondary. Any future market-prevalence or portfolio-value claim requires a separately weighted evaluation design.

**Veto:** do not convert the equal development strata into investment weights, sector weights or expected market frequencies.

## Quantitative research perspective

**Verdict: PASS / 256 reserve and 192 primary cases are now re-derived rather than inherited.**

The old 512 requirement represented two 256-identity panels. P013 no longer creates a second new evaluation panel because the exact preserved-2021 evaluation remains separately bound. Carrying the old 512 number forward therefore had no experimental role.

P013-B instead derives capacity from the selected structure:

- eight structurally present era-board cells;
- 24 primary review cases per cell = 192 total;
- eight pre-frozen fallback identity-windows per cell;
- 32 reserved identity-windows per cell = 256 total.

The scarcest cell has 57 available unique identities, so the per-cell reserve has 25 identities of metadata headroom before considering fallback mechanics. Selection must enforce global economic-identity uniqueness across the current 256-reserve batch. If the deterministic scarcity-first allocator cannot satisfy that condition, the process stops before market read; it does not silently reuse identities. Disjoint-window reuse remains a resource for later preregistered development iterations, not a reason to weaken current-batch independence when capacity is ample.

The fixed 61-open-session windows are capacity units, not claims that every security has complete future price evidence. P013-C must freeze actual identity-window assignments and the same-cell fallback order before any price read. Later input-admission failure may consume that pre-frozen fallback list, but reference or candidate results may not influence replacement.

**Veto:** no morphology-guided identity/window choice, no result-dependent refill, no inherited 512 pool, and no claim that repeated identity windows are independent validation observations.

## Data and evidence governance perspective

**Verdict: PASS / the census stayed inside the authorized metadata surface and provides sufficient evidence to freeze quotas.**

The executed census used governed listing identity / PIT `board_code`, trading-calendar metadata and prior immutable Stage exposure artifacts. It did not read security-bar values, atomic truths or candidate scores. The exact preserved-2021 identity plan remained market-data unread and continues to impose identity-global exclusion; 2022-2024 and 2025 were not opened.

The quota freeze is therefore legitimately pre-result. P013-C may now materialize a deterministic identity-window plan from the metadata ledger, but it must bind the census manifest, quota-freeze digest, source manifests, selection seeds and all 256 reserve identities/windows before result-bearing execution authority exists.

**Veto:** static/current industry membership remains non-authoritative for historical stratification; sector/theme does not enter the hard quota until a separately governed PIT history exists.

## Decision

All four responsibilities select:

`PASS_FREEZE_EQUAL_ERA_BOARD_RESERVE_AND_REVIEW_QUOTAS`

Frozen structure:

- structurally present cells: 8;
- reserve per cell: 32;
- primary review per cell: 24;
- pre-frozen fallback per cell: 8;
- total reserve: 256 identity-windows;
- total primary review: 192;
- total pre-frozen fallback: 64;
- current-batch economic identities: globally unique or fail before market read;
- no PRE_STAR/STAR cell because STAR did not exist there;
- no cross-cell fallback;
- no post-result supplementation;
- preserved 2021 remains unread and globally identity-protected.

The numerical coincidence that the reserve total is 256 does not inherit the old 256/256 split. It is independently derived as `8 cells × 32 reserves`, while the old unused second 256-identity fresh panel is removed.

## Next gate

`P3-STAGE-013-C` is authorized for metadata-only plan materialization and successor preregistration. It must freeze exact identity-window assignments, fallback ordering, source bindings and the unchanged atomic candidate/gate surface before a new result-bearing development Job can be admitted.

No result-bearing market read is authorized by this review.
