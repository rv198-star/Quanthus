# P3-STAGE-022 — Primary-Unique / Contingent-Fallback Four-Party Review

Design status: approved review  
Delivery gate: close P022 fail-closed; open admission-only P023

The roles below are same-origin simulation, not independent human experts.

## Evidence

The sole P022-B metadata Job `stage_cfe744f4b8c941deb7520d1faa31c232` completed without market-bar
or Stage1/12 semantic access. It established:

- 8 × 24 = 192 primary rows;
- 192 globally unique primary economic identities;
- all eight primary cells exactly 24/24;
- 62 dormant fallback rows against a ceiling of 64;
- every cell has eight dormant fallback candidates except `STAR_PRE_CHINEXT_20PCT/STAR`, which has six;
- no fallback identity overlaps the frozen 192 primary identities;
- worst-case simultaneous activation of the frozen dormant fallback pools can realize 60 unique
  fallbacks, but that worst-case is diagnostic only under the frozen P022 contract.

## Short-term trader

The active sample problem is solved: all eight board-era cells can carry 24 independent primary
identities. The remaining two-row shortage is infrastructure reserve, not evidence. Dropping STAR or
reducing the active panel would destroy the representation we deliberately protected. The early STAR
cell is simply the scarce one and should be handled by technical replacement logic rather than by
weakening the research sample.

## Fund manager

The economic independence claim applies to cases that actually enter the semantic review panel. A
planned primary whose 61-session input later fails admission never becomes an observation. Treating
that failed identity as permanently occupying the review panel is unnecessarily conservative. The
next step should therefore keep the 192 planned primary panel fixed, test readiness, and define the
realized identity set only from admission-ready primaries.

No newer years should be opened merely to manufacture two dormant reserve rows.

## Quant

P022 must remain a no-pass because its exact preregistered `8 fallback per cell` pool was not achieved.
However, its central hypothesis is supported: a 192-row globally identity-unique primary panel is
metadata-feasible.

The localized early-STAR fallback shortage is caused by the timing of the uniqueness constraint. P022
removed all 192 *planned* primary identities from all fallback pools. That is stronger than the later
scientific requirement, which is uniqueness among *realized* review cases.

The valid successor is not `8 -> 6`. It is a result-before-run admission protocol:

1. bind the exact immutable P022 192-row primary plan;
2. freeze an ordered same-cell fallback reservoir from metadata before readiness results;
3. admit all primary exact windows first;
4. build the realized identity set from admission-ready primaries only;
5. for each cell deficit, walk the frozen fallback order and admit/activate only candidates whose
   identities are not already realized elsewhere;
6. after each activation, add that identity to the realized set;
7. stop only when every cell has 24 admission-ready realized cases, or fail closed;
8. freeze that final realized plan before any Stage1/12 semantic review.

Fallback ordering and candidate membership must not use Stage1/12 labels, thresholds or future
returns.

## Data governance

The successor must preserve all prior history:

- P022's `62/64` no-pass remains immutable;
- P013 exact-plan and P014 semantic-consumed exact windows stay excluded under P021 L2B;
- P014 admission-only L2A reuse remains provenance-visible;
- fallback candidates may include identities that are *planned* primaries in another cell because
  planned status alone does not create a semantic exposure;
- an identity that is admission-ready as a primary anywhere is immediately unavailable for fallback
  activation everywhere else;
- a primary identity that fails technical readiness may later be considered through another disjoint
  exact window under the frozen fallback order, but never counted twice in the realized panel;
- preserved 2021, 2022-2024 and 2025 remain closed.

A bounded fallback reservoir should reuse the existing P014 candidate cap rather than invent a
post-result number. Up to 256 metadata identities per cell is already a prior, result-independent
technical screen ceiling; P023 may freeze a deterministic order within that ceiling and read only as
far as necessary to resolve technical readiness deficits.

## Decision

```text
P3-STAGE-022: CLOSED_DORMANT_FALLBACK_POOL_SHORTFALL
P3-STAGE-023: OPEN_REALIZED_IDENTITY_ADMISSION_AND_CONTINGENT_REPLACEMENT
```

P020 remains blocked. P023 is admission-only and cannot collect Stage1/12 opinions. If P023 realizes
24 admission-ready globally unique identities per cell and freezes the final 192-row plan, only then
may a separately frozen semantic successor return to P020-C.

## Claim ceiling

This review does not reinterpret P022 as PASS and does not authorize lowering the fallback ceiling.
It selects a different timing model for identity independence: planned resources may overlap in
contingency metadata, while the realized semantic panel must remain globally identity-unique.
