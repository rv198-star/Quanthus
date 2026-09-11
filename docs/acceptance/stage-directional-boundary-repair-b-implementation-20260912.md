# P3-STAGE-008-B Implementation Acceptance

| Field | Value |
| --- | --- |
| Work item | P3-STAGE-008-B |
| Scope | threshold-free Directional Path Geometry Facts + primary-first Reference Protocol v2 |
| Governing design | `../design/stage-directional-boundary-repair.md` |
| Four-party review | `../reviews/stage-directional-boundary-repair-review-20260911.md` |
| Market-data read | 0 |
| Runtime Job | 0 |
| Parameter search | 0 |
| Stage-007 threshold reuse | 0 |
| Stage-2 change | none |
| Result | ENGINEERING IMPLEMENTATION ACCEPTED / 008-C NOT OPENED |

## 1. Implemented Fact surface

`config/structure-directional-path-geometry.v1.json` and
`src/quanthus/structure_directional_path_geometry.py` implement exactly two new threshold-free
20-session concepts:

- `structure.geometry.range-traversal`;
- `structure.geometry.counter-direction-excursion-share`.

The builder consumes 21 contiguous admitted final-daily rows (`T-20 ... T`) and inherits the
same-listing, same-coordinate, nonadmitted-row and hidden-session-gap reset behavior used by the
other Structure Fact families. It has no provider or market-data acquisition path.

Range traversal is endpoint absolute displacement divided by the observed close range. A zero close
range is exactly zero. Counter-direction excursion is the largest movement against the endpoint net
direction divided by the same close range. Zero net displacement makes only that measurement
unavailable with reason `zero_net_direction`; it does not manufacture a direction.

The implementation deliberately does not duplicate window return or directional efficiency and
contains no Stage threshold or Stage projection.

## 2. Reference Protocol v2

`config/stage-learning/order-reference-protocol.v2.json` and
`src/quanthus/stage_learning/order_reference_protocol_v2.py` implement the two-phase review contract.

Phase P builds a primary packet containing only:

- T-only 20-session primary Facts;
- the two Directional Path Geometry Facts;
- the last 21 raw primary-path rows and their exact source references.

Explicit 10-session derived Facts, candidate outputs and future rows are absent. The reviewer commits
three atomic primary propositions:

- `structural_instability_20`;
- `coherent_directional_relocation_20`;
- `orderly_consolidation_20`.

The commit is content-digested and revalidated by the resolver rather than trusted merely because a
hash is self-consistent.

Only after a valid primary commit does Phase R expose explicit 10-session width/center Facts. The
recent schema contains only width, center and relation (`SUPPORTS / CONTRADICTS / NEUTRAL /
UNKNOWN`) plus evidence/reason fields. It has no primary proposition field. The primary resolver
accepts no recent modifier argument, and the combined resolver returns recent relations separately
with `recent_can_modify_primary=false`.

Cross-role disagreement remains `AMBIGUOUS`; there is no majority or adjudication path.

## 3. Atomic projection semantics frozen in code

The role-level resolver implements the design precedence exactly:

- unavailable primary -> `INPUT_UNAVAILABLE`;
- positive order together with positive instability or relocation -> `AMBIGUOUS_CONFLICT`;
- instability `TRUE`, relocation `FALSE`, order `FALSE` -> `STAGE_0`;
- instability `FALSE`, relocation `FALSE`, order `TRUE` -> `STAGE_2`;
- relocation `TRUE` with order `FALSE` -> `NEITHER`; retain `mixed_directional_instability` when
  instability is also `TRUE`;
- instability `FALSE` with order `FALSE` -> `NEITHER`;
- remaining UNKNOWN-dependent combinations -> `AMBIGUOUS`.

This makes residual Stage 0 structurally impossible in the reference resolver.

## 4. Semantic Catalog integration

The two new concepts are registered as `reserved` Structure semantic concepts with reviewed Chinese
business descriptions. The deterministic Data & Indicator Catalog snapshot moves from 758 to 760
entries and from 31 to 33 semantic concepts. This catalog registration does **not** add either Fact to
`structure-facts-v2-blueprint.v1.json` runtime accepted bindings; runtime qualification remains
separate.

## 5. Verification performed

Focused engineering verification covers:

- monotonic rise and decline;
- full `up -> down -> up` reversal where traversal alone is misleading;
- zero-net direction;
- one-step repricing then flat;
- scale invariance;
- nonadmitted/listing/coordinate/gap resets and rewarm;
- prefix replay stability;
- exact protocol/fact contract binding;
- primary packet exclusion of explicit recent Facts;
- primary-commit requirement before recent packet;
- inability of primary opinions to cite explicit recent Fact references;
- full atomic resolution precedence;
- cross-role disagreement without majority;
- recent contradiction leaving Stage-2 primary unchanged;
- recent schema rejection of any injected primary field;
- fail-closed unavailable-primary behavior;
- deterministic Catalog snapshot and reviewed Chinese presentation.

No Stage-007 case was used to learn or tune a threshold in this implementation stage.

## 6. Claim ceiling and next state

P3-STAGE-008-B proves only that the approved abstractions can be implemented as deterministic,
auditable, threshold-free Facts and a mechanically ordered review protocol. It does not prove that a
future relocation predicate has the right threshold, that Stage 0 is repaired on new market cases,
or that Stage 2 remains statistically strong on a new cohort.

Therefore:

```text
P3-STAGE-008-B: CLOSED / ENGINEERING ACCEPTED
P3-STAGE-008-C: BLOCKED — not executed in this approval
P3-STAGE-008-D: BLOCKED — no runtime authority
MARKET READ / JOB / PARAMETER SEARCH: NOT AUTHORIZED
2022-2024 / 2025: NOT OPENED
OFFICIAL LABEL / DAILY / STRATEGY / TRADING: NOT AUTHORIZED
```
