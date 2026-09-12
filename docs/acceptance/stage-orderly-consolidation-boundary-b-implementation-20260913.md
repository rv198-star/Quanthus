# P3-STAGE-011-B — Orderly-Consolidation Projection Engineering Acceptance

## Disposition

```text
P3-STAGE-011-B: CLOSED / ENGINEERING IMPLEMENTATION ACCEPTED
P3-STAGE-011-C: READY / CONSUMED-EVIDENCE TOPOLOGY DIAGNOSTIC ONLY
MARKET DATA / RUNTIME JOB / THRESHOLD SELECTION: NOT AUTHORIZED
PRESERVED 2021 EVALUATION: UNREAD
```

## Implemented contract

B adds one deterministic proposition-layer module:

- contract: `config/stage-learning/orderly-consolidation-projection.v1.json`;
- contract digest: `sha256:11569752a0a3ae7c5a6c3f2ecf07b51710442c0f2445a848800203df3cda146c`;
- implementation: `src/quanthus/stage_learning/orderly_consolidation_projection.py`;
- unit tests: `tests/unit/test_stage_orderly_consolidation_projection.py`.

The implementation consumes only:

- absolute 20-session directional efficiency;
- current 20-session normalized range width;
- 20-vs-prior-20 width change;
- a separately resolved coherent-directional-relocation truth value.

It derives only:

```text
contraction_magnitude20 = max(0, -width_change20)
```

This derivation is projection-local and is not registered as a Catalog Fact.

## Truth function

The implemented Stage2 proposition is exactly:

```text
low_directional_efficiency
AND materially_narrow_current_range
AND material_range_contraction
AND negative_coherent_relocation
```

Center shift, range traversal, counter-direction excursion and path turnover do not enter the Stage2
truth function. Range traversal and counter excursion remain owned by the separately resolved frozen
relocation guard. The implementation does not duplicate or retune that guard.

Missing required measurements and unknown relocation fail closed through three-valued logic. A
separate definite-false prerequisite still dominates unrelated unknowns, so the implementation does
not manufacture uncertainty when the proposition is already impossible.

## Adversarial coverage

Synthetic tests cover:

- positive tight + contracting + low-efficiency + non-relocating consolidation;
- each mandatory clause failing independently;
- center motion having zero truth authority;
- high in-band churn having zero direct truth authority;
- coherent relocation vetoing an otherwise tight contracting band;
- narrow but non-contracting structures staying false;
- missing measurements / unknown relocation propagating UNKNOWN;
- deterministic contraction magnitude;
- invalid ranges and thresholds failing closed.

No numeric development threshold is selected by these examples; their numbers are test fixtures only.

## Regression verification

Focused implementation/contract verification:

```text
19 passed
Ruff: PASS
git diff --check: PASS
```

Adjacent Stage0/Stage2 regression slice:

```text
43 passed
```

That slice includes the existing P3-STAGE-008 directional-boundary development, P3-STAGE-009
positive-instability development and P3-STAGE-010 quantitative projection tests. No existing behavior
was modified by B.

## Claim ceiling

B does not:

- select one of the future three Stage2 profiles;
- score any Stage0/Stage2 pair;
- add a tenth P010 Stage0 candidate;
- change the frozen relocation guard;
- read market data or submit a runtime Job;
- read preserved 2021, 2022-2024 or 2025;
- create an official Stage label, daily activation, strategy or trading authority.

C may now replay only consumed evidence to verify that the accepted topology addresses the diagnosed
contract shape without selecting any numeric threshold.
