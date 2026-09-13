# P3-STAGE-012-E7 Catalog-Facing Representation Acceptance — 2026-09-14

## Disposition

```text
P3-STAGE-012-E7: CLOSED
STATE: PASS_CATALOG_FACING_MEASUREMENT_REPRESENTATION
CATALOG REGISTRATION: NOT PERFORMED
ACTIVE B6-PATH CONTRACT: UNCHANGED
E5 ALGORITHM / 20-10 PROFILE: UNCHANGED
MARKET DATA: NOT READ
PRESERVED 2021: UNREAD
NEXT: P3-STAGE-012-E8 REGISTRATION DECISION REVIEW ONLY
```

E6 rejected direct registration of the E5 proposal because the provisional
`result_kind=structured_path_trace` was not part of the approved semantic Catalog result-kind set.
E7 changes only that representation boundary.

## Representation decision

The concept identity remains:

```text
structure.geometry.close-envelope-resolution-trace
```

Its Catalog-facing proposed `result_kind` is now:

```text
measurement
```

This is one T-ending Structure observation measuring close-path organization under the frozen E5
20/10 EvaluationSpec. The measurement value is a deterministic structured record containing status,
availability reason, exact profile, ordered escape/re-entry explanation records and their deterministic
descriptors.

This does **not** turn each escape into a registered event or create a persistent Episode. Re-entry
resolution remains knowledge as of T and is never backdated to the escape session. The rolling prior
close envelopes remain EvaluationSpec context, not separately governed anchors.

## Why measurement is compatible

The existing Structure object model binds an Observation to concept/spec/coordinate/time/evidence plus
a `value_digest`; it does not require a measurement result to be a scalar. Therefore E7 can preserve
the complete structured E5 value without extending the Catalog type system or coercing the object into
an event, relation or process.

The active Catalog already admits `measurement` under `structure.geometry`. The proposed concept's
family resolves to the owned `structure.geometry` namespace, while `catalog.resolve()` still rejects
the concept because E7 intentionally does not register it.

## Synthetic representation conformance

The E7 runner replays all nine frozen E5 Structure cases. For every case:

- status is unchanged;
- profile is unchanged;
- ordered event trace is unchanged;
- descriptors are unchanged;
- the measurement value digest equals the exact digest of that structured value;
- no descriptor or event becomes an independent registered Fact.

The existing `ObservationRevision` accepts the structured value digest without a new result kind. The
runner closes with no failed checks.

Frozen result:
`config/structure-directional-path-envelope-e7-representation-result.v1.json`.

## Claim ceiling

E7 proves only Catalog-facing representational compatibility. It does not prove market separability,
window optimality, predictive value, economic usefulness, B6-PATH runtime qualification or Stage
qualification.

Reserved semantic Catalog registration is still a separate decision. If later approved, that
registration remains naming/boundary authority only and does not itself authorize runtime use or
market qualification.

## Hard boundaries

E7 does not:

- mutate `config/structure-semantic-catalog.v1.json`;
- mutate the active `config/structure-directional-path-geometry.v1.json` family contract;
- change the E5 escape/re-entry algorithm or 20/10 profile;
- add a Catalog result kind;
- split trace descriptors into Stage-specific Facts;
- read market data or preserved 2021;
- retune P008 relocation, Stage0, Stage2 or any candidate threshold;
- authorize daily, strategy or trading use.

The only next gate is `P3-STAGE-012-E8`, a registration-decision review. E8 itself may not mutate the
Catalog.
