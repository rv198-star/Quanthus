# P3-STAGE-021 — Admission Exposure Capacity Four-Party Review

Design status: approved review  
Delivery gate: close P021 fail-closed; open P022 resource-contract design only

The roles below are same-origin simulation, not independent human experts.

## Evidence

P021-A froze the L2A/L2B exposure taxonomy before P021-B execution. The sole metadata-only Job
`stage_683c30a44be648e284e9168d3fb269b1` then completed without reading market bars or Stage1/12
semantics.

It found:

- P014 admission ledger: 4,608 exact windows;
- P014 qualified semantic plan: 256 exact windows;
- admission-only L2A windows restored to the metadata reservoir: 4,352;
- all eight Era × Board cells individually recover at least 32 unique identities;
- STAR cells recover from 0 to 48 and 38 identities;
- the unchanged requirement for **all 256 reserve rows** to be globally identity-unique reaches only
  243, leaving a 13-row shortfall.

P020 remains closed and no admission/read/review/threshold work was reopened.

## Short-term trader

The important representation problem is fixed: STAR is back without dropping the board or consuming
newer years. The remaining 13-row shortfall is not evidence that the market sample is inadequate. It
comes from demanding cross-cell identity uniqueness for fallback slots that may never be used.

The trading-facing requirement should be that the **actual reviewed sample** remains independent and
balanced. An unused backup list does not create a market observation merely by existing.

## Fund manager

Primary evidence and contingency capacity should not be conflated. The 192 intended primary cases
should carry the strongest identity-independence rule. Fallbacks exist to handle input/readiness
failures; forcing all 64 unused fallbacks to be globally unique can consume scarce STAR identities
without improving the final evidence if those fallbacks are never activated.

Any revised contract must still prohibit a fallback from creating duplicate identities in the final
reviewed set.

## Quant

The original `8 × 32 = 256 globally unique` rule was a conservative implementation shortcut, not the
statistical estimand. The quality gates operate on realized reviewed cases, not on unused reserve
entries.

A better contract is therefore:

1. freeze 24 primary identities per cell with **global identity uniqueness across all 192 primaries**;
2. freeze deterministic fallback candidate lists per cell from the remaining metadata-eligible pool;
3. fallback lists may overlap across cells because they are not observations yet;
4. when a fallback is activated, a deterministic resolver must skip any identity already active in
   another cell and select the next prefrozen candidate;
5. if a cell cannot realize 24 globally unique **active** cases after admission, stop before semantic
   review; never lower 24 or fill from another cell.

This preserves the statistical independence of the realized review slice without spending identity
uniqueness on dormant contingency rows.

## Data governance

The exposure model should distinguish reservation from consumption consistently. A fallback candidate
is L1 reserved until its exact window is actually read for admission or semantic evidence. Cross-cell
appearance in dormant fallback lists is metadata duplication, not semantic exposure.

The successor must be frozen before checking whether the 192-primary contract succeeds. It may not
choose a different fallback policy after seeing capacity. P013/P014 semantic-consumed windows and
preserved identities remain excluded exactly as in P021.

## Decision

```text
P3-STAGE-021: CLOSED_REPAIRED_TAXONOMY_GLOBAL_IDENTITY_SHORTFALL
P3-STAGE-022: OPEN_PRIMARY_UNIQUE_CONTINGENT_FALLBACK_DESIGN_ONLY
```

P022 should first answer, metadata-only, whether 24 globally unique primaries per cell can be frozen
under the P021 taxonomy and whether each cell has a sufficiently deep deterministic fallback list to
support later admission. No semantic review is authorized by this decision.

## Claim ceiling

This review does not authorize overlapping identities in the realized Stage1/12 review set. It only
rejects the requirement that **unused fallback reservations** must already be globally identity-unique.
The final active case set must remain globally identity-unique and Era × Board balanced.
