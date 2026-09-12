# P3-STAGE-011：Stage 0/2 Joint Boundary and Orderly-Consolidation Repair

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | open |
| Work item | `P3-STAGE-011` |
| Parent | [LLD 03](lld/03-label-feature-system.md) |
| Predecessor | `P3-STAGE-010 / REVISE_OR_PARK_DEVELOPMENT_FAIL` |
| Machine design | `../../config/stage-learning/orderly-consolidation-boundary-repair-design.v1.json` |
| Machine design digest | `sha256:5d1e1ab3298df676829aff34a9c3b6117fa56053fb684f2c630ca04fbd380353` |
| Root-cause diagnostic | `../../config/stage-learning/orderly-consolidation-boundary-diagnostic.v1.json` |
| Review | [four-responsibility review](../reviews/stage-orderly-consolidation-boundary-repair-review-20260913.md) |

## 1. Why P3-STAGE-011 exists

P3-STAGE-010 repaired the earlier Stage-0 shared-core bottleneck. Its nearest development candidate
reached precision `0.9744`, positive-reference coverage `0.7037` and definite-negative FPR `0.0114`.
It still could not freeze because two cases simultaneously satisfied the frozen numeric Stage-2
predicate. Tightening the neighboring Stage-0 broad profile removed both overlaps but reduced coverage
to `0.6296`, so adding a tenth Stage-0 candidate would only tune around a frozen boundary rather than
repair the boundary itself.

The decisive new evidence is semantic, not merely numeric. On the sealed 160-case 2019 Primary review,
the legacy Stage-2 rectangle:

```text
abs(efficiency20) <= 0.33
range_width20     <= 0.18
width_change20    <= -0.20
abs(center_shift20) <= 0.04
```

hits only 6 of 16 definite Stage-2 references. All ten missed Stage-2 references satisfy the other
three gates and fail only the center-shift hard gate. At the same time the same rectangle asserts on
one definite Stage-0 reference and one definite `NEITHER` reference. The Stage-0 conflict is a
contract contradiction: both semantic roles called that case Stage 0 while the legacy numeric Stage-2
rule called it orderly consolidation.

P3-STAGE-011 therefore reopens the **Stage-0 / Stage-2 proposition boundary**, not P010's Stage-0
thresholds.

## 2. Root-cause conclusion

The old Stage-2 rule treated four summary measurements as an axis-aligned rectangle. That shape no
longer expresses the approved meaning of `orderly_consolidation_20`:

- `center_shift20_abs <= 0.04` is too strong as a mandatory truth gate. A contracting, bounded
  structure can move its center without becoming a coherent directional repricing.
- `range_width20 <= 0.18` is not sufficient proof of boundedness on the fresh cohort. The P010
  conflict appears around width `0.1627`, while the definite Stage-2 references are materially tighter
  as a group.
- adding a generic low-churn or low-counter-excursion gate would be semantically wrong. The definite
  Stage-2 references themselves often oscillate strongly inside a tight contracting band; their
  median counter-direction excursion is about `0.884` and median path turnover is about `3.93`.

The repair must therefore improve **positive bounded-contraction evidence**, not redefine “orderly”
as a smooth or one-way path.

## 3. Revised Stage-2 topology

`orderly_consolidation_20` is a positive proposition. It is not `NOT Stage0`, and it is not a residual
class.

The approved topology is:

```text
low directional efficiency
AND
materially narrow current range
AND
material range contraction
AND
coherent directional relocation = FALSE
        ↓
orderly_consolidation_20
```

The four clauses have distinct jobs:

1. **low directional efficiency** prevents an obvious trend from being called consolidation;
2. **material narrowness** establishes that the current structure is actually bounded;
3. **material contraction** establishes positive evidence of consolidation rather than a merely
   stable narrow range;
4. **negative coherent relocation** prevents a tightening directional repricing from being promoted
   to Stage 2.

The existing P3-STAGE-008 relocation guard remains exact and is not retuned by this successor.

## 4. Center shift becomes modifier context

`range-center-shift@20_vs_20` remains a governed continuous Fact, but it no longer has direct truth
function authority for Stage 2.

This does **not** mean center movement is irrelevant. It remains useful descriptive context and may be
used in later attribution. The narrower rule is only:

> center stability is not a mandatory prerequisite for a positive orderly-consolidation proposition.

A large center shift aligned with a coherent directional path is already handled by the unchanged
relocation guard. A center shift without coherent relocation cannot by itself veto a tight, materially
contracting structure.

## 5. No new path-order Fact in this repair

The consumed 2019 diagnostic explicitly rejects a tempting but weak repair: adding “low churn” or
“low counter excursion” as an order gate.

True Stage-2 references frequently contain substantial two-sided movement inside a narrow band. High
counter excursion therefore does not mean structural disorder when the amplitude is already tightly
bounded and contracting. Conversely, high traversal remains useful for coherent relocation, where it
is already governed by P3-STAGE-008.

P3-STAGE-011 introduces no new source Fact family. It reuses:

- directional efficiency;
- range width;
- width change;
- the already frozen directional-relocation result.

For threshold development only, `contraction_magnitude20 = max(0, -width_change20)` is a deterministic
projection-local derivation, not a Catalog Fact.

## 6. Future Stage-2 profile surface

No numeric threshold is selected by A. A future fresh development phase may construct exactly three
bundled Stage-2 profiles from unlabeled development observations:

| Profile | efficiency max | range-width max | positive contraction min |
| --- | --- | --- | --- |
| order-lenient | Q70 | Q40 | Q30 |
| order-balanced | Q60 | Q30 | Q50 |
| order-strict | Q50 | Q20 | Q70 |

The quantile method must be frozen before any fresh result-bearing read. Individual thresholds are not
independent knobs and the profile surface cannot expand after results.

The existing P010 Stage-0 topology remains a nine-profile **design surface**, not a selected candidate.
A future joint development may therefore contain at most `9 × 3 = 27` preregistered Stage-0/Stage-2
pairs. Zero simultaneous Stage-0/Stage-2 assertion on the complete development denominator remains a
hard pair gate. No precedence rule may hide a genuine proposition conflict.

## 7. Adversarial cases

| Case | Required interpretation |
| --- | --- |
| tight, strongly contracting band with high in-band churn | Stage 2 may be true; churn alone cannot veto order |
| mid-width contracting two-sided disorder | Stage 2 must not follow merely from contraction + low efficiency |
| narrow contraction with center drift but no relocation | center movement alone cannot veto Stage 2 |
| narrow contraction with coherent directional repricing | relocation vetoes Stage 2 |
| narrow but non-contracting structure | Stage 2 false |
| contracting but not materially narrow structure | Stage 2 false |
| missing required primary measurement | fail closed to UNKNOWN |
| Stage-0 instability and Stage-2 order both positive | explicit conflict; never resolved by priority |

## 8. Consumed-evidence boundary

The P010 2019 review is consumed. It may prove why the legacy topology is defective and may be replayed
for diagnostic conformance, but it cannot select a new Stage-2 numeric threshold or a Stage-0/2 pair.
The P009 2020 development is also consumed.

The exact 160-identity 2021 plan remains unread. P3-STAGE-011 does not open it. Any later use requires
one pair to be frozen first from a new disjoint development cohort under a separately accepted
preregistration.

## 9. Delivery sequence

```text
P3-STAGE-011-A  CLOSED / DESIGN APPROVED
P3-STAGE-011-B  READY / IMPLEMENTATION + SYNTHETIC ONLY
P3-STAGE-011-C  BLOCKED / CONSUMED-EVIDENCE TOPOLOGY CONFORMANCE
P3-STAGE-011-D  BLOCKED / FRESH JOINT DEVELOPMENT
```

A authorizes implementation and synthetic conformance only. It opens no market-data read, runtime
Job, parameter search, 2021 evaluation, official label, daily, strategy or trading authority.
