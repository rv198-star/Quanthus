# P3-STAGE-009-B Engineering Implementation Acceptance

| Field | Value |
| --- | --- |
| Work item | P3-STAGE-009-B |
| Governing design | `../design/stage-positive-instability-proposition-repair.md` |
| Contract | `../../config/stage-learning/positive-instability-contract.v1.json` |
| Contract digest | `sha256:2160cdb23ed43acb248bafd7bbdace20ff2dd936ab9c8ff5f3b3cda3d74cda7b` |
| Implementation | `../../src/quanthus/stage_learning/positive_instability.py` |
| Unit contract | `../../tests/unit/test_stage_positive_instability.py` |
| State | ENGINEERING IMPLEMENTATION ACCEPTED |

## 1. Implemented scope

B implements only the qualitative proposition-composition substrate approved by 009-A. It does not
convert continuous Facts into quantitative evidence and therefore contains no market thresholds.

The contract exposes five future threshold-policy projections as three-valued primitive evidence:

- material 20-session range width;
- material 20-vs-prior20 width expansion;
- low absolute 20-session directional efficiency;
- low 20-session range traversal;
- high 20-session counter-direction excursion.

These primitive values are composed into two explicit channels:

```text
expanding_disorder_20 =
    range_width_material
    AND width_expansion_material
    AND low_directional_efficiency

reversal_or_roundtrip_20 =
    low_range_traversal
    OR high_counter_direction_excursion

broad_reversal_disorder_20 =
    range_width_material
    AND low_directional_efficiency
    AND reversal_or_roundtrip

structural_instability_20 =
    expanding_disorder_20
    OR broad_reversal_disorder_20
```

All operators use explicit `TRUE / FALSE / UNKNOWN` logic. `AND` fails false if any required input is
false and remains unknown if no false input exists but at least one input is unknown. `OR` becomes
true if any input is true, false only when all inputs are false, and otherwise remains unknown.

## 2. Center shift cannot mutate truth

`assess_positive_instability()` has no center-shift argument. Center shift can be attached only through
`attach_modifiers()`, which records descriptive context and returns the original proposition status
unchanged.

Tests explicitly attach both small and large center shifts to the same assessment and require the
truth value to remain identical. This turns the root-cause finding into an executable structural
constraint rather than a documentation convention.

## 3. Adversarial behavior

The B tests cover the A-stage semantic counterexamples:

- expanding monotonic/one-way movement does not become instability merely because width expands;
- expanding low-efficiency disorder activates the expansion channel;
- broad reversal can remain instability even when current width is not expanding;
- zero-net broad oscillation may activate the reversal channel with counter-direction excursion
  `UNKNOWN`, without manufacturing a direction;
- tiny low-efficiency noise remains non-instability when amplitude is not material;
- a broad stable box remains non-instability when path disorder is absent;
- unknown primitive evidence stays unknown and the existing Primary resolver keeps it ambiguous,
  never residual Stage 0;
- positive instability plus coherent relocation stays `NEITHER` in the Stage0/2 projection with the
  mixed-directional-instability reason;
- the result ledger exposes primitive truth, channel truth and activated channels and contains no
  opaque numerical score.

## 4. Authority and claim ceiling

B did not:

- read market data;
- submit a runtime Job;
- select or search numerical thresholds;
- use consumed P3-STAGE-008 evaluation to set thresholds;
- modify the frozen 008 relocation guard;
- modify Stage 2;
- open 2022–2024 or 2025;
- authorize official labels, strategy use or trading.

The two Path Geometry Facts remain `implemented_unqualified`.

## 5. Exit

P3-STAGE-009-B is engineering-accepted once the focused tests and repository gate are green.
P3-STAGE-009-C may then use synthetic cases and already-consumed evidence for **diagnostic
conformance only**. C may not select numerical thresholds or qualify the proposition.
