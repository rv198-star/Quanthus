# P3-STAGE-010-B — Quantitative Projection Engineering Acceptance

## Disposition

```text
P3-STAGE-010-B: CLOSED / ENGINEERING IMPLEMENTATION ACCEPTED
P3-STAGE-010-C: READY / CONSUMED-EVIDENCE TOPOLOGY DIAGNOSTIC ONLY
MARKET DATA / RUNTIME JOB / PARAMETER SEARCH: NOT AUTHORIZED
```

## Implemented surface

The accepted implementation is bounded to:

- `config/stage-learning/positive-instability-quantitative-projection.v1.json`;
- `src/quanthus/stage_learning/positive_instability_quantitative_projection.py`;
- `tests/unit/test_stage_positive_instability_quantitative_projection.py`.

It accepts continuous measurements plus a separately versioned threshold bundle and emits an auditable
Boolean/three-valued projection. No numerical threshold values are frozen by B.

## Topology

The previous shared `range_width AND low_efficiency` core no longer exists.

`expanding_disorder_20` requires:

```text
material positive width expansion
AND
(material current width OR material absolute width gain)
AND
(low efficiency OR low traversal OR high counter-excursion)
```

`broad_reversal_disorder_20` requires:

```text
material current width
AND
at least two of:
  low efficiency
  low traversal
  high counter-excursion
```

The final instability proposition remains the OR of the two channels.

## Derived measurement

`absolute_range_width_gain` is deterministically derived from existing Facts:

```text
prior_width = current_width / (1 + width_change)
absolute_gain = current_width - prior_width
```

It is projection-local, fail-closed when the denominator is non-positive, and is not registered as a
new Catalog Fact.

## Adversarial coverage

Unit tests prove:

- tiny relative expansion without material amplitude/growth stays false;
- expanding disorder can pass below the broad-channel width floor;
- monotonic expansion fails when all path-disorder signals are false;
- a broad round trip can pass with moderate efficiency when traversal and counter-excursion agree;
- one broad low-efficiency signal alone is insufficient;
- zero-net counter-excursion UNKNOWN still permits two-of-three through low efficiency + low traversal;
- center shift is absent from primitives and derived truth inputs;
- invalid thresholds fail closed.

Focused result: `11 passed`.

## Claim ceiling

This acceptance does not claim threshold quality, market validity, qualification, runtime activation,
Stage-2 changes, relocation retuning, official labels, strategy or trading value. The unopened 2021
evaluation remains unopened.
