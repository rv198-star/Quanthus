# P3-STAGE-009：Positive Structural Instability Proposition Repair

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | blocked |
| Work item | P3-STAGE-009 |
| Parent | [LLD 03](lld/03-label-feature-system.md) |
| Predecessor | P3-STAGE-008 / `REVISE_OR_PARK_EVALUATION_FAIL` |
| Machine design | `../../config/stage-learning/positive-instability-repair-design.v1.json` |
| Machine design digest | `sha256:e5393eb71e7500966ad5e497a3918967c2a12a6aa4113b0db2e6888ef5aaddd6` |
| Root-cause evidence | [post-closeout validation](../acceptance/stage-directional-boundary-root-cause-validation-20260912.md) |
| Four-responsibility review | [review record](../reviews/stage-positive-instability-proposition-repair-review-20260912.md) |
| Runtime/data authority | none |

## 1. Why P3-STAGE-009 exists

P3-STAGE-008 fixed a real part of Stage 0: coherent one-way price relocation can no longer be treated
as direction-neutral disorder merely because range width or center position moved. The fresh 2021
evaluation showed that this relocation guard materially improved precision and false-positive rate,
but it also exposed a remaining conformance defect. The executable candidate still inherited the old
R0 instability predicate:

```text
low directional efficiency
AND
(width expansion OR center shift)
```

That means a large center migration can still create positive Stage-0 instability even when the
current range is contracting. The approved 008 design had already forbidden that semantic. The
post-closeout diagnostic found center-shift-only evidence behind 8 of 11 remaining false positives,
but only 2 of 15 definite Stage-0 references. Removing only that independent positive authority,
without changing the frozen relocation guard, produced a diagnostic-only counterfactual of precision
0.8125, coverage 0.8667 and definite-negative FPR about 0.0462. This is strong root-cause evidence,
not qualification and not permission to reuse the consumed evaluation for parameter selection.

P3-STAGE-009 therefore repairs the **positive definition of `structural_instability_20` itself**.
It does not retune the 008 relocation guard.

## 2. Positive proposition semantics

`structural_instability_20` means:

> There is positive, explainable evidence that the 20-session primary-scale price structure is
> materially disordered rather than merely located somewhere different from its prior range.

It remains a three-valued proposition: `TRUE / FALSE / UNKNOWN`. Stage 0 continues to require:

```text
structural_instability_20          = TRUE
coherent_directional_relocation_20 = FALSE
orderly_consolidation_20            = FALSE
```

A Stage-0 result is never produced from residual logic, top-1 selection, missing data, or the absence
of another stage.

### 2.1 Positive channel A — expanding disorder

`expanding_disorder_20` captures a structure whose current range is materially expanding relative to
the immediately prior equal non-overlap window **and** whose path is not organized as a coherent
one-way move.

This channel uses existing facts only:

- `structure.geometry.width-change@20_vs_prior20` — relative expansion/contraction;
- `structure.geometry.range-width@20` — current amplitude context;
- `structure.geometry.directional-efficiency@20` — path direction efficiency.

No one fact is sufficient by itself. In particular:

- width expansion alone does not prove disorder;
- a monotonic advance/decline may expand the range while remaining directional;
- a tiny range becoming slightly less tiny must not become structural instability merely because its
  relative width change is numerically large.

Exact quantitative thresholds are intentionally **not** frozen in 009-A.

### 2.2 Positive channel B — broad reversal disorder

`broad_reversal_disorder_20` exists because instability need not always be a newly expanding range.
A structure can remain broad and repeatedly reverse inside that range while its width is flat or even
contracting relative to an earlier extreme window.

This channel reuses:

- `structure.geometry.range-width@20`;
- `structure.geometry.directional-efficiency@20`;
- `structure.geometry.range-traversal@20`;
- `structure.geometry.counter-direction-excursion-share@20`.

The intended semantic shape is:

> material current amplitude + weak endpoint-direction efficiency + positive evidence of reversal or
> round-trip behavior.

For non-zero net displacement, counter-direction excursion can corroborate reversal. For zero net
displacement that Fact is intentionally unavailable; low range traversal plus low directional
efficiency and material range width can still establish a broad round-trip structure. The system must
not manufacture a direction merely to make counter-direction excursion defined.

### 2.3 Proposition composition

The two channels form a small explainable OR:

```text
if any positive channel is TRUE:
    structural_instability_20 = TRUE
elif every positive channel is FALSE:
    structural_instability_20 = FALSE
else:
    structural_instability_20 = UNKNOWN
```

This differs from old R0 in two important ways:

1. raw measurements are not independently promoted into Stage truth;
2. every positive result identifies an explicit semantic channel and its supporting Facts.

Opaque weighted composites are forbidden.

## 3. Center shift loses truth authority

`structure.geometry.range-center-shift@20_vs_prior20` remains a valid continuous Fact. It can describe:

- migration of the current structure relative to the prior window;
- context for repricing or relocation;
- a reason annotation after another instability channel is already positive.

It has **no place in the truth function** for `structural_instability_20`.

Therefore:

```text
strong contraction + large center shift
```

is not Stage 0 unless independent positive disorder evidence is present. This case may resolve to
`NEITHER`, `STAGE_2`, or `AMBIGUOUS` depending on the other primary propositions; center migration
alone can never force instability.

## 4. Interaction with relocation and order

Positive instability remains independent of directional relocation so mixed states remain visible.
The resolver keeps the existing precedence:

- instability `TRUE` + relocation `FALSE` + order `FALSE` -> Stage 0;
- instability `TRUE` + relocation `TRUE` -> outside Stage 0, preserve
  `mixed_directional_instability` as reason metadata;
- order `TRUE` together with instability `TRUE` or relocation `TRUE` -> `AMBIGUOUS_CONFLICT`;
- relocation `TRUE` + order `FALSE` -> `NEITHER` for the Stage0/2 projection;
- all positive propositions `FALSE` -> `NEITHER`, not residual Stage 0;
- explicit missing/unknown inputs remain `UNKNOWN` / `INPUT_UNAVAILABLE` according to the existing
  Primary protocol.

No Stage-2 threshold or semantic change is part of P3-STAGE-009.

## 5. Do we need another Fact?

No new threshold-free Fact is authorized by 009-A.

The failure is a proposition-composition defect rather than an observational gap. Existing accepted or
implemented-unqualified Facts already separate the required dimensions:

- amplitude: range width;
- expansion/contraction: width change;
- direction efficiency: directional efficiency;
- endpoint traversal: range traversal;
- adverse reversal: counter-direction excursion;
- structure migration: center shift, now modifier-only.

A new Fact would add feature surface before demonstrating an actual missing observable. If later
synthetic conformance finds a counterexample that cannot be represented by these dimensions, that is a
new design question rather than authority to silently add a feature.

## 6. Adversarial semantics

009-A freezes the following outcomes before implementation:

| Case | Required interpretation |
| --- | --- |
| monotonic up/down with expanding range | relocation can be positive; expansion alone cannot make instability positive |
| one-step repricing then flat | coherent relocation, not instability |
| strong contraction + large center shift | not instability without another positive disorder channel |
| broad two-sided reversal while range contracts | broad-reversal channel may still make instability positive |
| symmetric broad oscillation ending near start | instability may be positive even though counter-direction excursion is unavailable at zero net |
| up-down-up ending at range edge | high traversal alone cannot prove relocation; high adverse reversal remains visible |
| tiny noisy range with low efficiency | no instability without material amplitude/expansion |
| broad but stable box with no reversal/expansion | broadness alone cannot prove instability |
| expanding low-efficiency disorder | expanding-disorder channel may assert instability |
| instability + coherent relocation | preserve both propositions; do not emit Stage 0 |
| missing primary Fact | unknown/unavailable; never residual Stage 0 |

## 7. Threshold boundary

009-A freezes **semantic form only**. It deliberately does not select numerical thresholds for:

- material range width;
- material expansion;
- low directional efficiency;
- low traversal;
- high counter-direction excursion.

The 008 2021 evaluation and its post-closeout counterfactual are permanently consumed diagnostic
evidence. They may test future implementation conformance but cannot select 009 thresholds or qualify
009. The existing 008 relocation thresholds `10% / 0.50 / 0.60` also cannot be retuned from that
consumed evidence.

Any quantitative search requires a separate preregistered development source, candidate grid, result
budget and later task-disjoint validation.

## 8. P3-STAGE-009-A disposition

The same-origin four-responsibility review found no open directional design blocker after requiring:

- center shift to have zero proposition-truth authority;
- two explicit positive channels rather than one raw-Fact OR;
- a zero-net path rule that does not invent direction;
- no new Fact in A;
- no threshold selection from consumed 008 evidence;
- no Stage-2 change.

Therefore:

```text
P3-STAGE-009-A: CLOSED / DESIGN APPROVED
P3-STAGE-009-B: CLOSED / ENGINEERING IMPLEMENTATION ACCEPTED
P3-STAGE-009-C: CLOSED / PASS_DIAGNOSTIC_CONFORMANCE_ONLY
P3-STAGE-009-D: READY / PREREGISTRATION ONLY, NO DATA ACCESS
```

B has implemented the proposition/channel contract, synthetic adversarial cases and reason ledger in
`config/stage-learning/positive-instability-contract.v1.json` and
`src/quanthus/stage_learning/positive_instability.py`. The implementation contains no numeric
thresholds, market-data provider, runtime Job path, relocation retuning or Stage-2 mutation. Detailed
evidence is in
[009-B engineering acceptance](../acceptance/stage-positive-instability-b-implementation-20260912.md).

C then replayed the sealed 95-case P3-STAGE-008 evaluation through the read-only Primary transport.
The two center-shift-only definite Stage-0 cases (`26`, `48`) are broader, less direction-efficient,
lower-traversal and higher-counter-excursion than the eight center-shift-only false positives at the
group median, and each of the two true cases individually lies on the more-disorder-like side of all
four false-positive medians. Of the 15 definite Stage-0 references, 13 have positive width change;
the only two negative-width-change positives are exactly those broad-reversal cases. C therefore
closed `PASS_DIAGNOSTIC_CONFORMANCE_ONLY` without selecting any threshold or opening fresh data.
Detailed evidence is in
[009-C conformance acceptance](../acceptance/stage-positive-instability-c-conformance-20260912.md).

D may now be preregistered. No D market-data read is authorized until source partitions, economic
identity isolation, quantitative candidate grid, result-bearing Job budget, reference protocol, hard
gates and claim ceiling are frozen in a separate preregistration checkpoint.
