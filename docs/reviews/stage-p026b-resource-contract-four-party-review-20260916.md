# P3-STAGE-026-B Stage8 resource/reference preregistration review

Date: 2026-09-16  
Review type: same-origin simulated four-perspective pre-result review  
Result: `PASS_FREEZE_ONE_CAPACITY_LIMITED_STAGE8_REFERENCE_ROUTE`

Machine contract: `../../config/stage-learning/stage-high-side-modifier-blind-reference.v1.json`  
Semantic parent: `../../config/stage-learning/stage-high-side-modifier-concept.v1.json`

## Evidence available to the review

This review occurs before any P026 admission read, Trend Fact packet read or Stage8 opinion. It may use
only the already accepted exposure model/taxonomy and historical resource receipts.

Known facts include:

- eight structurally present Era × Board cells remain the correct time/board stress axes;
- the P021 taxonomy distinguishes exact L2A technical admission reads from semantic consumption;
- P013 exact-plan and P014 qualified-plan windows are already consumed exact windows;
- P015's 60 additional blind-review rows must be verified as a subset of the P014 qualified plan,
  preventing an untracked semantic-exposure surface;
- P020's 187 exact reviewed identity-windows are separately consumed and must not become "fresh"
  Stage8 evidence;
- whole-identity burn-down is not the resource model: a later disjoint window from the same economic
  identity may be used, but dependence remains explicit at the identity-cluster level;
- preserved 2021 identities remain globally excluded and 2022-2024/2025 remain closed.

No Stage8 prevalence, reference label or candidate result is visible.

## Short-term trading perspective

**Decision: preserve broad regime coverage without forcing another square panel.**

Stage8 is a high-side modifier whose interpretation can vary materially across trading-rule eras and
board families. Every cell therefore needs meaningful representation. But P020 showed that insisting
on exactly 24 realized rows in every cell can turn resource mechanics into the research objective.

The selected route targets 20 technically ready rows per cell, but treats 16 per cell as the hard
coverage floor. The target guides deterministic collection; it is not a PASS requirement. This keeps
early/short-lived STAR regimes visible while allowing a capacity-limited result to remain scientifically
usable if the total panel is still broad.

**Veto:** no dropping STAR, no cross-cell fill that hides a cell shortfall, and no post-read quota
revision to make the panel look balanced.

## Fund-management perspective

**Decision: issuer diversity remains the independence unit.**

A maximum 160-row panel may contain the same issuer in non-overlapping eras. Those observations are
useful regime stress evidence but are not independent economic identities. The contract therefore
requires at least 128 unique economic-identity clusters and one selected row per identity within each
cell. Cross-cell/era repeat identities are legal only on non-overlapping exact windows and remain one
cluster for inference.

A 128-cluster minimum preserves substantial issuer diversity without recreating the false requirement
that every row be globally identity-unique. It also leaves a clear gap between descriptive row count
and independent evidence count.

**Veto:** repeated identities cannot inflate reference-sufficiency denominators and overlapping windows
from the same identity cannot both enter the panel.

## Quantitative-methodology perspective

**Decision: freeze coverage guardrails, not a retrospective power calculation.**

The route uses:

- target 20 rows per cell, maximum 160 rows;
- minimum 16 rows in each cell;
- minimum 144 total rows;
- minimum 128 unique identity clusters;
- at most 64 precommitted identities per cell;
- at most two precommitted candidate windows per identity;
- one technical-readiness fallback sequence frozen before any read.

These values do not claim market representativeness or statistical power. They are deliberately
upstream of the semantic reference gate. The later blind-reference gate remains independent and
reuses the pre-opinion cluster-aware floors: at least 80 definite advancing-context clusters, 20
positive and 20 negative; and inside resolved advancing context, at least 40 definite Stage8 clusters,
20 positive and 20 negative, with the same 15% unjudgeable ceilings.

A resource pass therefore cannot imply a semantic pass. Conversely, if the realized panel satisfies
resource coverage but lacks enough advancing/Stage8 references, P026 closes at reference sufficiency
rather than changing resource selection to enrich positives.

**Veto:** no high-side morphology prefilter, no fact-value stratified sampling, no threshold-aware
selection, and no gate lowering after opinions.

## Data/governance perspective

**Decision: one combined resource/admission/packet route, then stop.**

P026-B precommits the candidate ordering from metadata and prior immutable artifacts. The single
result-bearing Job may then read only technical input readiness in that order, select the exact ready
rows under the frozen resource rules, and build T-only Trend Evolution packets for those rows. It may
not open a second capacity Job or repair route.

Exact semantic-consumed windows from P013, P014 and P020 are excluded before any new read. P014 L2A
admission-only exact windows remain reusable with explicit provenance because no semantic/model/review
result was consumed there. P015 is verified mechanically as a subset of the already excluded P014
qualified plan. Packet failure remains unavailable/no-replacement.

If resource minima fail, the route ends `CLOSED_STAGE8_RESOURCE_CAPACITY_SHORTFALL_NO_REPAIR` and
returns to portfolio review. It does not create P027/P028 resource repair automatically.

**Veto:** no new years, no protected identities, no P020 exact-window reuse, no Stage9 activity or
opinion, and no second resource route.

## Joint decision

The four perspectives agree to freeze P026-B before any result-bearing read.

```text
P3-STAGE-026-B: PASS_FREEZE_ONE_CAPACITY_LIMITED_STAGE8_REFERENCE_ROUTE
Stage8 resource target: 20 rows/cell, 160 max
Stage8 resource hard floors: >=16/cell, >=144 rows, >=128 identity clusters
Candidate budget: <=64 identities/cell, <=2 precommitted windows/identity
P020 exact reviewed windows: excluded
P014 L2A technical-only windows: reusable with provenance
Stage9: BLOCKED_MISSING_ACTIVITY_RELATION
automatic resource repair: forbidden
```

A pass of the resource gate authorizes only blind Stage8 reference collection under the already frozen
semantic protocol. Candidate scoring, Stage9, preserved evaluation, strategy and trading remain
closed.

## Claim ceiling

This review validates the preregistration design, not the future Stage8 result. The resource minima are
coverage/admission guardrails. They do not prove that enough advancing contexts or positive/negative
Stage8 references exist, and they do not estimate market prevalence.
