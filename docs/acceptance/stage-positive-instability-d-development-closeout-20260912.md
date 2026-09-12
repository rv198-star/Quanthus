# P3-STAGE-009-D Development Closeout — 2026-09-12

| Field | Value |
| --- | --- |
| Work item | `P3-STAGE-009-D` |
| Disposition | `REVISE_OR_PARK_DEVELOPMENT_FAIL` |
| D1 Job | `stage_93c9590d5df34f5e8f38e242077c1e3c` |
| D1 revision | `552555f23dd92c556db8290e8829d19ccd1d24bb` |
| D1 manifest | `sha256:c49bbf50f01a5319634bdd0e54a1a3716f14e78b462d5570bf3f6c028e64c6b5` |
| Development evidence | `sha256:136d362f24c65b3fdbd99341b0a48ad9a9de6f3197f58ba3d1d5f1a8ad2187e1` |
| Review ledger | `sha256:77e95605ea6fe7c915a8bc80f1fcec1d2634a9c4dce210848d5fc7cf7ff89a15` |
| Trial digest | `sha256:9150be5aec5db06713c240844b796640f7ab2727ce6841ba094e717d13456034` |
| Evaluation data read | **false** |

## 1. D1 held the preregistered boundary

D1 formed a third, task-disjoint 512-identity pool after excluding the previously exposed Stage-007
and Stage-008 identity surfaces. The pool was partitioned before review into 256 development-capacity
and 256 evaluation-capacity identities. The 2020 development selection planned 128 identities and
produced 122 Primary cases; six assigned identities had no eligible case in their frozen quarter and
were retained as shortfalls rather than backfilled.

D1 also froze a 160-identity 2021 evaluation plan, but **did not read any evaluation market data**.
The review transport exposed only Primary-scale facts, the two directional-path facts and bounded
T-time price history. It did not expose the threshold basis, candidate grid, candidate output,
explicit recent derived facts or future rows.

## 2. Development references were sealed before candidate scoring

The owner-session same-origin Primary review sealed 244 opinions over the 122 development cases:

- `STAGE_0`: 37
- `STAGE_2`: 11
- `NEITHER`: 59
- `AMBIGUOUS`: 15

The development denominator therefore exceeded the preregistered minimums of 96 complete cases,
15 definite Stage-0 positives and 50 definite negatives. No candidate or threshold basis was visible
during reference creation.

## 3. Frozen baseline already met the development quality floor

The frozen P3-STAGE-008 baseline produced:

| Metric | Baseline |
| --- | ---: |
| TP | 29 |
| FP | 6 |
| FN | 8 |
| Precision | 0.828571 |
| Positive-reference coverage | 0.783784 |
| Definite-negative FPR | 0.085714 |

So the new D2 screen was not trying to rescue an unusable denominator. It had to improve or preserve
precision/FPR while keeping enough positive coverage.

## 4. All 27 preregistered candidates failed

D2 ran the exact frozen 27-candidate Cartesian grid. No candidate was added, replaced or retuned after
seeing results. `eligible_count=0`, so the preregistered no-eligible disposition is mandatory.

The nearest-to-passing candidate was:

```text
core-lenient / expansion-lenient / reversal-balanced
range_width_min = 0.1852970795568982880161127895
positive_width_change_min = 0.474834437086092715231788080
absolute_directional_efficiency_max = 0.1960252935862691960252935863
range_traversal_max = 0.4461538461538461538461538462
counter_direction_excursion_min = 0.6363636363636363636363636364
```

It produced:

| Metric | Observed | Gate |
| --- | ---: | ---: |
| TP | 22 | — |
| FP | 1 | — |
| Precision | **0.956522** | >= 0.75 PASS |
| Coverage | **0.594595** | >= 0.70 **FAIL** |
| FPR | **0.014286** | <= 0.12 PASS |
| Stage-0 predictions | 23 | >= 12 PASS |
| Stage2 overlap | 0 | = 0 PASS |
| Coverage loss vs baseline | **0.189189** | <= 0.15 **FAIL** |

Thus the failure is specifically a **positive-coverage failure**, not a precision, FPR, Stage2 overlap
or denominator failure.

## 5. Failure attribution: shared-core bottleneck

A post-D2 read-only diagnostic was run against the sealed D1 Primary transport and sealed review
ledger. It did not search or change thresholds.

For the nearest candidate's 15 false-negative Stage-0 references:

- 8 failed the shared `range_width_material_20` core;
- 8 failed the shared `low_directional_efficiency_20` core;
- 1 failed both;
- all 15 had both positive channels FALSE after the shared core was applied;
- 3 also triggered the frozen 008 relocation guard, but all three were already rejected by the
  positive-instability core;
- none was attributed to Stage2 as the decisive rejection.

Among all 37 definite Stage-0 references, the nearest candidate activated `broad_reversal_disorder_20`
22 times and `expanding_disorder_20` only 6 times before Stage projection guards. Moreover, changing
`expansion-lenient / balanced / strict` while holding core and reversal profiles fixed produced
identical metrics throughout the frozen grid. The expansion threshold was therefore not the binding
D2 dimension.

The evidence supports the following bounded conclusion:

> The two-channel semantic direction remains plausible, but the D parameterization forced both
> channels through the same cross-sectional absolute-width and very-low-direction-efficiency core.
> That shared core is too restrictive for this development reference set and collapses the intended
> two-channel model into one narrow admission gate.

This is **not** authority to lower either threshold after seeing the result. Any successor must be a
new version with a separately frozen projection design and must treat this 2020 development evidence
as consumed.

## 6. Stop rule and claim ceiling

Per preregistration, `eligible_count=0` closes D immediately:

- no D3 evaluation preparation;
- no 2021 evaluation market-data read;
- no grid expansion;
- no automatic supplement;
- no threshold relaxation;
- no candidate replacement;
- no relocation-guard retuning;
- no Stage2 change;
- no 2022-2024 or 2025 access;
- no official Stage label, strategy, daily or trading authority.

`P3-STAGE-009` therefore closes as **`REVISE_OR_PARK_DEVELOPMENT_FAIL`**. The semantic root-cause
repair from A/B/C remains useful engineering evidence, but the quantitative projection is not
qualified and must not be activated.

Machine closeout:
`config/stage-learning/positive-instability-development-selection-result.v1.json`

Post-D2 diagnostic:
`config/stage-learning/positive-instability-development-failure-diagnostic.v1.json`
