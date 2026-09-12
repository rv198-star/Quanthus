# P3-STAGE-010：Positive Instability Quantitative Projection Repair

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | open |
| Work item | P3-STAGE-010 |
| Parent | [LLD 03](lld/03-label-feature-system.md) |
| Predecessor | P3-STAGE-009 / `REVISE_OR_PARK_DEVELOPMENT_FAIL` |
| Machine design | `../../config/stage-learning/positive-instability-quantitative-projection-design.v1.json` |
| Machine design digest | `sha256:b69ba0f9c22569c521b43c29efa252770d1b1b6e8630f66522cb61f60e306b62` |
| Delivery state | `../../config/stage-learning/positive-instability-quantitative-projection-delivery.v1.json` |
| Review | [four-responsibility review](../reviews/stage-positive-instability-quantitative-projection-review-20260912.md) |
| Runtime/data authority | none |

## 1. Why P3-STAGE-010 exists

P3-STAGE-009 repaired the qualitative proposition correctly enough to remove the old `center_shift`
truth leak and to distinguish expanding disorder from broad-reversal disorder. Its fresh 2020
development screen nevertheless failed before evaluation: all 27 preregistered candidates improved
precision/FPR but lost too much positive coverage.

The failure attribution is narrower than another Stage-0 semantic rewrite. Fifteen true Stage-0
references missed the nearest candidate. Eight failed the shared absolute-range-width core, eight
failed the shared low-directional-efficiency core, and one failed both. Expansion-profile changes did
not change any metrics inside a fixed core/reversal profile. The common entrance, not the two-channel
idea, became the binding bottleneck.

P3-STAGE-010 therefore owns only the **continuous-Fact → qualitative-primitive projection**. It does
not reopen Stage-0 ontology, Stage 2, the relocation guard, or `center_shift` authority.

## 2. Shared core is forbidden

The quantitative projection must not require both positive channels to first satisfy one common
`range_width AND low_efficiency` gate. Channel-specific evidence must be allowed to establish its own
case.

This is not permission to loosen every threshold. It is a structural separation:

```text
expanding disorder                     broad-reversal disorder
------------------                     -----------------------
material relative expansion            material current range width
AND                                     AND
(amplitude OR absolute growth)          at least 2 of 3:
AND                                       low efficiency
at least 1 path-disorder signal           low traversal
                                          high counter-excursion
```

The final Stage-0 projection still passes through the existing relocation and Stage-2 guards.

## 3. Expanding-disorder projection

A rapidly expanding structure can be unstable before its current absolute range reaches the same
cross-sectional width floor needed by the broad-reversal channel. The former P3-STAGE-009 shared core
incorrectly required that broad-channel property first.

The revised channel requires:

1. **material positive width change** versus the immediately prior equal non-overlap 20-session
   window;
2. **material amplitude or material absolute width growth**;
3. at least one explicit sign that the path is not a coherent one-way move:
   - low directional efficiency; or
   - low endpoint traversal; or
   - high counter-direction excursion.

The amplitude/growth clause prevents a tiny range becoming slightly less tiny from qualifying purely
because its relative percentage growth is large.

### 3.1 Projection-local absolute width gain

No new source Fact is introduced. From the existing deterministic Facts:

```text
current_width = range_width@20
change = width_change@20_vs_prior20
prior_width = current_width / (1 + change)
absolute_width_gain = current_width - prior_width
```

The derived measurement is projection-local and replayable. It is not registered as an independent
Catalog Fact. It is used only when the source values are available and `change > -1`.

## 4. Broad-reversal projection

Broad-reversal disorder still requires a materially broad current range. What changes is the path
corroboration.

P3-STAGE-009 made low directional efficiency a mandatory gate shared with the expanding channel. The
new projection treats three path-disorder observations symmetrically:

- low directional efficiency;
- low endpoint traversal;
- high counter-direction excursion.

At least **two of the three** must be positive.

This preserves protection against a broad but orderly trend. One weak signal is not enough. It also
supports the frozen zero-net case: if counter-direction excursion is unavailable because net
endpoint displacement is exactly zero, low efficiency plus low traversal can still satisfy two of
three without inventing a direction.

## 5. Unchanged boundaries

The following remain exact:

- `structural_instability_20 = expanding_disorder_20 OR broad_reversal_disorder_20`;
- Stage 0 requires instability TRUE, relocation FALSE and order FALSE;
- instability + relocation stays outside Stage 0;
- Stage 2 semantics and thresholds are unchanged;
- `center_shift` remains description/modifier context with zero truth-function authority;
- missing inputs fail closed to UNKNOWN / INPUT_UNAVAILABLE, never residual Stage 0;
- the P3-STAGE-008 relocation guard is not retuned.

## 6. Consumed evidence boundary

The P3-STAGE-009 2020 development set is consumed evidence. It may demonstrate why the shared-core
shape failed and may be replayed for conformance diagnostics, but it cannot select P3-STAGE-010
thresholds and cannot qualify the successor.

The P3-STAGE-009 D1 2021 evaluation identity/quarter plan remains unopened. It may be preserved exactly
for later P3-STAGE-010 evaluation only after a P3-STAGE-010 candidate is frozen from a different,
new development cohort. It may not be reselected or backfilled.

## 7. Fresh threshold-development plan

The future development source is 2019 and uses a **new fourth economic-identity pool**. The pool must
exclude:

- all Stage-007 exposed identities;
- the entire Stage-008 fresh pool;
- the entire Stage-009 512-identity fresh pool, including the still-sealed evaluation identities.

Thresholds are produced from unlabeled continuous observations before semantic references are scored.
The candidate surface is intentionally reduced to **nine** combinations:

```text
3 expansion profiles × 3 broad-reversal profiles = 9 candidates
```

Each profile is a preregistered bundle of quantile coordinates rather than independent free knobs.
There is no grid expansion after results.

## 8. Adversarial cases

The implementation must satisfy these before any market-data read:

| Case | Required interpretation |
| --- | --- |
| tiny range with large relative expansion but negligible absolute growth | expanding disorder false |
| moderate range with material absolute growth and reversal | expanding disorder may be true without broad width floor |
| monotonic expanding advance/decline | expanding disorder false because all path-disorder corroborators are false |
| broad round trip with moderate efficiency | broad-reversal may be true if traversal and counter-excursion both show disorder |
| broad low-efficiency path with no reversal | broad-reversal false because only one of three disorder signals is positive |
| zero-net broad round trip | low efficiency + low traversal may satisfy two-of-three while counter excursion stays unknown |
| contracting orderly structure with large center shift | center shift cannot change truth |
| instability + coherent relocation | outside Stage 0 under the existing resolver |

## 9. P3-STAGE-010-A disposition

The four-responsibility adversarial review closed all design-direction blockers. The approved sequence
is:

```text
P3-STAGE-010-A  CLOSED / DESIGN APPROVED
P3-STAGE-010-B  READY / IMPLEMENTATION ONLY
P3-STAGE-010-C  BLOCKED / CONSUMED-EVIDENCE DIAGNOSTIC ONLY
P3-STAGE-010-D  BLOCKED / FRESH 2019 DEVELOPMENT + PRESERVED 2021 EVALUATION
```

No market-data read, runtime Job, parameter trial, evaluation read, Stage-2 change, relocation retune,
official label, strategy or trading authority is opened by A.
