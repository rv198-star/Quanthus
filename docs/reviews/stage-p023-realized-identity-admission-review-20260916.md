# P3-STAGE-023 — Realized-Identity Admission Four-Party Review

Design status: approved review  
Delivery gate: close P023 fail-closed; open P024 design-only successor

The roles below are same-origin simulation, not independent human experts.

## Evidence

The sole P023 Job `stage_451124246782476ea51fa11b60c346ba` completed successfully as a governed
technical-admission experiment. It read no Stage1/12 semantics and opened no preserved partition.

- primary readiness: 142 / 192;
- primary technical failures: 50;
- activated fallbacks: 37;
- fallback market-read attempts: 42;
- fallback readiness failures: 5;
- fallback rows skipped because the identity was already realized elsewhere: 113;
- final realized panel: 179 rows / 179 identities, still globally identity-unique;
- seven Era × Board cells: 24 / 24;
- `STAR_PRE_CHINEXT_20PCT / STAR`: 11 / 24, deficit 13.

The bounded status does not expose per-cell reason counts for all 113 conflict skips, so the review does
not claim every conflict occurred in early STAR. It does establish that fallback readiness failure is
small relative to identity-conflict skipping and that the remaining no-pass is concentrated in the
historically smallest STAR cell.

## Short-term trader

The same stock observed in two non-overlapping market-rule eras is not automatically duplicate market
experience. For a development study whose whole point is time × board structure, forcing every era to
use entirely different STAR names removes useful regime comparisons and makes the shortest historical
STAR era structurally fragile. Keep 24 distinct names **inside each cell**, but do not pretend a
cross-era repeat is equivalent to two independent identities.

## Fund manager

The project should not consume newer years, drop STAR, or reduce the 24-case cell target to rescue this
batch. The data are adequate for seven cells and the early-STAR constraint is a consequence of the
short historical board universe plus the global-identity rule. Reusing the same company in a different,
non-overlapping era can be acceptable development evidence if the later evaluation remains protected
and concentration is reported honestly.

## Quant

P013 already defined the development resource unit as `economic_identity × non_overlapping_window ×
role` and allowed disjoint-window reuse with explicit loss of identity-independence claims. P023's
192-global-identity requirement is therefore a stricter local choice, not a foundational scientific
requirement. The correct repair is not more greedy matching. Freeze a clustered development contract:

- one case per identity inside each Era × Board cell;
- cross-cell same-identity reuse only across non-overlapping exact windows;
- repeated identities form an explicit cluster;
- case counts and cell balance remain 8 × 24;
- aggregate reports must include unique-cluster counts and must not treat 192 rows as 192 independent
  identities when repeats exist;
- any later preserved/final evaluation keeps identity-global separation.

A successor may reuse completed P023 readiness results and read only candidates that P023 skipped
solely because of the now-superseded global-identity conflict. It must not rerun successful/failed
readiness observations already consumed.

## Data governance

Do not rewrite P023 as PASS. Preserve its 179-row globally unique no-pass. P024 must be separately
preregistered **before** reading any previously skipped conflict candidate. The new rule must distinguish:

```text
development independence:
  identity unique within cell
  exact windows non-overlapping across repeated cross-era identity
  repeated identity explicitly clustered

preserved independence:
  identity-global exclusion remains unchanged
```

The successor may not use semantic outcomes, thresholds, reviewer opinions or post-result market
labels to choose which conflicts to read.

## Decision

```text
P3-STAGE-023: CLOSED_REALIZED_ADMISSION_CAPACITY_SHORTFALL
P3-STAGE-024: OPEN_CROSS_ERA_CLUSTERED_IDENTITY_DEVELOPMENT_CONTRACT
```

P020 remains blocked. P024 is governance/technical admission work only. A later P024 PASS may make a
separately frozen P020-C preregistration legal, but cannot start Stage1/12 review by itself.

## Claim ceiling

This review does not claim cross-era repeated identities are statistically independent. It selects an
explicit clustered-development representation because that matches the previously accepted P013
resource model and avoids burning the Stage program on an over-strict development-only independence
constraint. Preserved 2021, 2022-2024 and 2025 remain closed.
