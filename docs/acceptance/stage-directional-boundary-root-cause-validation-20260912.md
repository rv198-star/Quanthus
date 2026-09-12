# P3-STAGE-008 Post-closeout Root-cause Validation

| Field | Value |
| --- | --- |
| Scope | post-closeout diagnostic only |
| Source evaluation | `config/stage-learning/directional-boundary-evaluation-result.v1.json` |
| Source ledger | `config/stage-learning/directional-boundary-evaluation-review.v1.tsv` |
| Replay diagnostic | `../../scripts/diagnose_stage_directional_boundary_root_cause.py` |
| Machine result | `../../config/stage-learning/directional-boundary-root-cause-diagnostic.v1.json` |
| New market-data read | 0 |
| New parameter trial | 0 |
| Candidate replacement | 0 |
| Qualification claim | none |
| Result | ROOT CAUSE DIRECTION CONFIRMED / NEW SUCCESSOR REQUIRED |

## 1. Question

P3-STAGE-008-D improved the Stage-0 candidate but still failed the preregistered precision and
false-positive-rate gates. The closeout attributed eight of the eleven remaining false positives to
an inherited old-R0 branch in which a large 20-session center shift could independently assert
structural instability, even when the current range was contracting or was not expanding.

This diagnostic asks a narrower question:

> Is `center_shift` retaining independent positive Stage-0 authority the main remaining semantic
> defect, or would removing that authority also destroy a large fraction of true Stage-0 cases?

It does not ask which replacement threshold should be used and does not reopen the frozen
10% / 0.50 / 0.60 relocation guard.

## 2. Evidence decomposition

The sealed 2021 evaluation contains 95 cases with 15 definite `STAGE_0`, 65 definite negatives and
15 ambiguous primary references. The frozen P3-STAGE-008 candidate produced 15 true positives and
11 false positives.

The remaining false positives split as follows:

- **8 / 11 (72.7%)** are `center_shift-only` old-R0 positives: directional efficiency remains inside
  the old Stage-0 ceiling, `width_change20 < 0.20`, but `abs(center_shift20) >= 0.07` independently
  keeps the old instability predicate positive. Their sealed case indices are
  `30, 44, 46, 50, 53, 67, 76, 93`.
- **3 / 11** are not center-only. Their sealed indices are `17, 29, 68`; each retains coherent
  directional geometry while its absolute 20-session return lies between about 8.38% and 9.78%, just
  below the calibration-frozen 10% relocation guard. This is a secondary generalization boundary;
  post-evaluation threshold retuning remains forbidden.

Against the 15 definite Stage-0 references, only **2 / 15 (13.3%)** depend on the old
`center_shift-only` branch. Their sealed case indices are `26, 48`. The other 13 definite Stage-0
references already have independent positive range-expansion evidence under the old R0 geometry.

The asymmetry is therefore strong: the center-only branch represents 72.7% of remaining false
positives but only 13.3% of definite positives.

## 3. Semantic counterfactual

One diagnostic counterfactual was evaluated without changing any relocation-guard threshold:

> `center_shift` may remain a continuous descriptive Fact and modifier, but cannot independently
> establish the positive `structural_instability_20` proposition. For this diagnostic only, the
> inherited old-R0 positive instability branch therefore requires the existing width-expansion
> branch; the frozen relocation guard is otherwise unchanged.

This is **not** a proposed final Stage-0 rule and is not qualification evidence. It isolates the
suspected semantic defect.

| Metric | Frozen 008 candidate | Counterfactual |
| --- | ---: | ---: |
| True positives | 15 | 13 |
| False positives | 11 | 3 |
| False negatives | 0 | 2 |
| Precision | 57.69% | **81.25%** |
| Positive-reference coverage | 100% | **86.67%** |
| Definite-negative FPR | 16.92% | **4.62%** |

The counterfactual clears all three numerical D evaluation thresholds (`precision >= 0.80`,
`coverage >= 0.55`, `FPR <= 0.10`) while leaving the three near-10%-return directional errors
untouched. Because this counterfactual was formed after evaluation was observed, it **cannot** convert
P3-STAGE-008-D into a pass. Its only valid use is root-cause attribution and successor design.

## 4. Four-responsibility review

This review is a same-origin simulated peer review, not four independent external experts.

### Short-term trading view

**PASS direction.** A moving range center can arise from an orderly repricing, correction or a box
that has simply migrated. Center movement without positive disorder/expansion evidence is not enough
to call the structure unstable. A successor should keep center shift descriptive but require
separate positive instability evidence.

### Fund-manager view

**PASS direction.** The strongest counterexample is a contracting range whose center migrates. That
shape is compatible with repricing or transition and should not become Stage 0 solely because the
center moved. The current center-only path overstates disorder.

### Quantitative-research view

**PASS direction with claim ceiling.** The 72.7%-FP versus 13.3%-TP concentration and the isolated
counterfactual improvement are strong evidence that the center-only branch is the dominant remaining
defect. However, the same evaluation sample generated the diagnosis, so no threshold or final
replacement predicate may be selected from it. The three near-guard false positives remain a
secondary generalization issue rather than justification to change the 10% guard post hoc.

### Data/evidence-governance view

**PASS direction; classify as conformance defect.** P3-STAGE-008-A had already frozen
`center_shift_alone_cannot_assert_stage_0=true`, yet D retained the old R0
`width expansion OR center shift` positive-instability route. This is not merely weak statistical
performance: the implementation/evaluation candidate did not fully realize the approved semantic
decomposition. The successor must repair the positive instability proposition itself before any new
fresh validation is opened.

## 5. Disposition

Root-cause direction is confirmed strongly enough to open a new bounded successor:

> **Repair the positive `structural_instability_20` proposition so center shift cannot independently
> establish Stage 0. Preserve center shift as descriptive/modifier evidence. Do not retune the frozen
> P3-STAGE-008 relocation guard on the consumed 2021 evaluation.**

The successor must start from semantic design and adversarial counterexamples, then implement the
positive instability contract, and only afterward use task-disjoint evidence. P3-STAGE-008 remains
closed and failed; its evaluation is permanently diagnostic-only for the successor.
