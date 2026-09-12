# P3-STAGE-008：Stage 0 方向边界与参考协议修复设计

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | blocked |
| Final disposition | `REVISE_OR_PARK_EVALUATION_FAIL` |
| Work item | P3-STAGE-008 |
| Parent | [LLD 03](lld/03-label-feature-system.md) |
| Predecessor | P3-STAGE-007 / `REVISE_TARGET_OR_PARK` |
| Machine design | `../../config/stage-learning/directional-boundary-repair-design.v1.json` |
| Machine design digest | `sha256:9621dfe333050cdc94812e07b21e46ae0c7d211b57d05224924ec8b2a273e2f6` |
| Four-party review | [review record](../reviews/stage-directional-boundary-repair-review-20260911.md) |
| Runtime authority | none |

## 1. Why this successor exists

P3-STAGE-007 did not fail because Stage 0 missed its positive examples. It failed because the old
R0 proposition treated many coherent directional repricings as direction-neutral disorder. On the
160 fixed T-only cases, Stage 0 had 28 true positives and 12 definite false positives; all twelve
false positives were independently described by both simulated business roles as directionally
controlled rises, declines, repricings or corrections rather than direction-neutral instability.
Stage 2 did not show this defect and remains frozen.

A second, separate defect existed in the reference process. At least 21 ambiguous cases allowed an
explicit 10-session recent condition to change or weaken the 20-session primary judgement even
though the governing semantics had already said recent conditions were modifiers, not hidden
primary vetoes. P3-STAGE-008 therefore repairs two boundaries together but does not conflate them:

1. the semantic boundary between **coherent directional relocation** and **direction-neutral
   structural instability**;
2. the evidence-governance boundary between the **20-session primary judgement** and the
   **10-session recent modifier**.

The owner explicitly authorized P3-STAGE-008-B on 2026-09-12. The bounded engineering
implementation is now accepted; this still authorizes no new market experiment, threshold search or
runtime Job.

## 2. Primary semantic decomposition

Stage 0 must no longer be reviewed or projected as one opaque yes/no concept. Every 20-session case
is decomposed into three atomic propositions, each with `TRUE / FALSE / UNKNOWN`:

- `structural_instability_20`: positive evidence that the current 20-session geometry is unstable;
- `coherent_directional_relocation_20`: positive evidence that the path is primarily completing a
  directional repricing rather than oscillating without directional control;
- `orderly_consolidation_20`: positive evidence of a bounded, orderly consolidation.

The derived meanings are deliberately asymmetric:

- Stage 0 requires instability `TRUE`, relocation `FALSE`, order `FALSE`;
- Stage 2 requires instability `FALSE`, relocation `FALSE`, order `TRUE`;
- coherent relocation is an explicit **Stage-0 guard**, not a Stage-0 subtype;
- coherent relocation is not automatically Stage 12 or any other stage: this 0/2 scope may return
  `NEITHER` and leave directional-stage projection to its owner;
- instability plus coherent relocation is retained as a mixed diagnostic condition outside the
  Stage-0/2 projection rather than force-fit into Stage 0;
- an unknown relocation judgement cannot be silently treated as `FALSE` to manufacture Stage 0.

The resolver must retain atomic opinions even when the final 0/2 result is `NEITHER` or
`AMBIGUOUS`. A positive relocation with order false resolves outside Stage 0 even when instability
is also positive; the mixed directional-instability reason stays visible. Conflicting positive order
with either instability or relocation is explicit `AMBIGUOUS_CONFLICT`. If the two semantic roles
resolve to different primary statuses, the result stays `AMBIGUOUS`; no majority or adjudication is
allowed. Stage 0 remains a positive proposition, never a residual fallback.

## 3. Why the old facts were insufficient

Existing direction efficiency is:

```text
(close_T - close_T-20) / sum(abs(adjacent close changes))
```

It correctly measures magnitude-weighted directional efficiency, but it compresses temporal path
shape. A path that advances directionally with meaningful pullbacks and a path that genuinely
oscillates can both land in the 0.2–0.4 band. Lowering the old `0.40` threshold would therefore be
post-result tuning, not a semantic repair.

The Stage-007 cases are permanently design-exposed, so they are used here only to reject weak Fact
designs. No threshold was searched. Among the 28 old Stage-0 true positives versus the 12 old
Stage-0 definite directional false positives:

| Diagnostic | True Stage 0 median | Directional false-positive median | Design use |
| --- | ---: | ---: | --- |
| abs 20D window return | 0.0444 | 0.2526 | reuse existing Fact |
| 20D range traversal | 0.3261 | 0.7695 | promote as proposed Fact |
| counter-direction excursion share | 0.6286 | 0.3272 | promote as proposed Fact |
| daily sign reversal rate | 0.4721 | 0.5000 | reject as primary Fact |

The result does **not** qualify a new rule. It says only that “count the reversals” is a poor design
route, whereas endpoint relocation and the severity of counter-direction excursion describe the
actual business distinction more directly.

## 4. Directional Path Geometry Facts v1

The minimum new Fact surface contains two threshold-free continuous observations. Existing
`window-return@20` and `directional-efficiency@20` stay owned by Trend Evolution and are reused,
not copied.

### 4.1 Range traversal

`structure.geometry.range-traversal@20`

```text
abs(close_T - close_T-20)
-----------------------------------------------
max(close_T-20 ... close_T) - min(close_T-20 ... close_T)
```

The value is in `[0, 1]`. A zero close range yields exactly `0` because both endpoint displacement
and observed close range are zero. Interpretation is intentionally limited: a high value means the
window start and end traverse a large share of the observed close range. It supports directional
relocation but cannot prove it alone.

This Fact catches the key geometry missed by the old rule: broad center migration can be ordinary
repricing when the path starts near one side of its range and ends near the other.

### 4.2 Counter-direction excursion share

`structure.geometry.counter-direction-excursion-share@20`

For positive net displacement:

```text
max(running_max_close_to_s - close_s) / close_range_20
```

For negative net displacement:

```text
max(close_s - running_min_close_to_s) / close_range_20
```

It is in `[0, 1]`. Zero net displacement makes the direction undefined and therefore returns
`unavailable:zero_net_direction`, rather than inventing an orientation.

This Fact prevents a second direction error that range traversal alone cannot catch. A path such as
`0 -> 10 -> 0 -> 10` has full endpoint range traversal but also a full counter-direction excursion;
it must not be called coherently directional just because it ends at the range edge.

### 4.3 Deliberately excluded Facts

- sign-reversal rate is not promoted: the exposed diagnostic showed essentially no useful group
  separation;
- daily direction-agreement share is not promoted in v1: it adds limited information and overlaps
  directional efficiency;
- linear-trend R² is not promoted: it can be diagnostically useful but is less interpretable and
  unnecessarily model-like for the minimum semantic repair.

Facts remain observations. No Stage thresholds, labels or support-score cutoffs belong in their
identity.

## 5. Future projection shape: two predicates, not one score

A future Stage-0 candidate must first construct two independently explainable predicates:

1. **instability evidence** — based on expansion, migration and path geometry;
2. **directional relocation guard** — based on existing 20D return/efficiency plus range traversal
   and counter-direction excursion.

Stage 0 may assert only when instability is positively supported and coherent relocation is
negatively resolved. The design explicitly forbids:

- center shift alone implying Stage 0;
- range traversal alone implying relocation;
- a weighted composite score whose correlated terms can hide which proposition actually fired;
- lowering the old direction-efficiency threshold on the Stage-007 cohort;
- changing Stage 2 while repairing Stage 0.

Center migration aligned with a strongly traversed directional path is evidence of repricing, not
by itself disorder. Large center migration with low traversal and substantial counter-direction
excursion remains compatible with instability.

No machine thresholds are frozen by P3-STAGE-008-A. Threshold learning, if later authorized, must
occur on a fresh task-disjoint development cohort.

## 6. Reference Protocol v2: primary first, recent second

The reviewer workflow becomes two-phase and mechanically ordered.

### Phase P — primary

The reviewer receives only the T-only primary-scale packet and raw primary path needed to answer
the three atomic 20-session propositions. Candidate output, future rows and explicit derived 10-day
facts are absent. The complete primary opinion is committed and receives a digest before Phase R.

The raw 20-session path naturally contains its last ten sessions, so full cognitive isolation of
“recent history” is neither claimed nor possible. The enforceable rule is narrower and stronger:
**explicit 10-day derived facts cannot be used as a post-hoc primary veto.**

### Phase R — recent modifier

Only after the primary commit does the reviewer receive the 10-session width and center conditions.
The reviewer may record:

- width: contracting / expanding / stable / unknown;
- center: stable / shifting / unknown;
- relation to primary: supports / contradicts / neutral / unknown.

Phase R has no field capable of modifying Phase-P primary propositions. The resolver ignores recent
conditions when deriving the primary Stage-0/2 status. A primary `UNKNOWN` reason may not cite an
explicit recent Fact.

This directly prevents the P3-STAGE-007 failure mode where “recent expansion” converted a valid
20-session primary judgement into ambiguity.

## 7. Direction-error adversarial cases

The implementation contract, if later opened, must encode at least these counterexamples before any
real-data experiment:

| Path | Required semantic outcome |
| --- | --- |
| monotonic rise | relocation positive; Stage 0 false |
| monotonic decline | relocation positive; Stage 0 false |
| stair-step direction with small pullbacks | relocation supported or ambiguous; never residual Stage 0 |
| up-down-up ending at the range edge | traversal alone insufficient; counter excursion exposes reversal |
| broad symmetric oscillation ending near start | relocation negative; instability may be positive |
| one-step repricing then flat | relocation positive; not direction-neutral disorder |
| narrow contracting box | Stage 2 may be positive |
| narrow contracting box + explicit recent expansion | primary Stage 2 unchanged; recent modifier contradicts |
| large center shift aligned with one-way repricing | center shift cannot create Stage 0 |
| large center shift + low traversal + large counter excursion | instability may be positive |
| listing/coordinate reset | unavailable, never Stage 0 |
| missing primary input | unavailable, never Stage 0 |

These are semantic contract cases, not market-performance tests.

## 8. Data, lineage and execution boundaries

If the Fact family is later implemented:

- it consumes the same contiguous admitted daily close closure as existing Structure Facts;
- same-listing, same-coordinate and hidden-gap reset semantics remain exact;
- shared source materialization is built once and reused by Trend Evolution and Directional Path
  Geometry consumers inside a Job;
- it does not duplicate window return or directional efficiency;
- no temporary diagnostic table is promoted to ADM merely to reuse computation;
- catalog/lineage entries must be added only when the Fact implementation becomes active.

The existing Stage-007 160 cases are permanently exposed for design diagnostics. They cannot tune
thresholds, qualify P3-STAGE-008 or be rebranded as fresh evidence. 2022–2024 remain previously
consumed/exposed for their existing purposes and 2025 remains closed.

## 9. Successor delivery sequence

The four-party review approves the design direction but intentionally leaves delivery blocked:

1. **P3-STAGE-008-A — design and four-party adversarial review:** closed;
2. **P3-STAGE-008-B — implement threshold-free Facts and two-phase reference contract:** closed;
   engineering acceptance is `../acceptance/stage-directional-boundary-repair-b-implementation-20260912.md`;
3. **P3-STAGE-008-C — conformance/regression using synthetic paths plus already-exposed 007 cases:**
   closed `PASS_DIAGNOSTIC_CONFORMANCE_ONLY`; no threshold learning or qualification claim;
4. **P3-STAGE-008-D — one fresh task-disjoint development screen:** closed
   `REVISE_OR_PARK_EVALUATION_FAIL`; the preregistered calibration/evaluation sequence completed
   without post-evaluation tuning or supplementation.

C itself created no Job, market-data read or parameter search. D later consumed only the separately
preregistered bounded 2019-2021 development path and closed all runtime authority after evaluation.

## 10. P3-STAGE-008-B implementation result

The approved design is now implemented in two deliberately separate surfaces.

`config/structure-directional-path-geometry.v1.json` and
`src/quanthus/structure_directional_path_geometry.py` own the two new 20-session threshold-free
measurements. They require 21 contiguous admitted rows, inherit listing/coordinate/gap reset
semantics, expose exact source-closure/revision identity, and contain no Stage threshold or provider
opening path. Zero net displacement makes only counter-direction excursion unavailable; range
traversal remains defined.

`config/stage-learning/order-reference-protocol.v2.json` and
`src/quanthus/stage_learning/order_reference_protocol_v2.py` implement the Primary-P then Recent-R
ledger mechanically. The primary packet has no explicit 10-session derived facts; a content-digested
primary opinion is required before a recent packet can be built. Recent commits have no primary
field, and the primary resolver accepts no recent-modifier argument. A combined resolver returns the
recent relation separately and explicitly states that it cannot modify primary status.

The role-level atomic precedence from section 2 is executable: relocation-positive mixed conditions
remain outside Stage 0, unknown-dependent combinations remain ambiguous, and cross-role disagreement
cannot be majority-voted away. Stage 2 code/thresholds are untouched.

The two semantic concepts are registered in the Data & Indicator Catalog as reserved Structure
concepts with reviewed Chinese descriptions. They are intentionally **not** added to accepted
Structure runtime bindings because 008-D has not qualified them. Detailed B evidence is in
[008-B engineering acceptance](../acceptance/stage-directional-boundary-repair-b-implementation-20260912.md).

## 11. P3-STAGE-008-C conformance result

C executed the frozen conformance contract without opening new data. The existing Stage-007 prepare
was read through the bounded review-transport index plus twenty pages, for 21 read-only status calls.
All 160 exposed cases were read; the transport contained neither candidate output nor future rows.

The old frozen Stage-0 rule plus the frozen Stage-007 semantic ledger reproduced exactly the A-design
regression groups: 28 old Stage-0 true positives and 12 old Stage-0 false positives with definite
`NEITHER` references. The B implementation then reproduced all A-frozen diagnostic medians exactly
to four decimals: TP vs directional-FP absolute 20-session return `0.0444 / 0.2526`, range traversal
`0.3261 / 0.7695`, and counter-direction excursion share `0.6286 / 0.3272`.

These are regression checks on already exposed evidence, not learned cutoffs. C performed zero
threshold searches, zero parameter trials, zero new market-data reads and zero runtime Jobs. The
result is `PASS_DIAGNOSTIC_CONFORMANCE_ONLY`, with evidence in
[008-C conformance acceptance](../acceptance/stage-directional-boundary-repair-c-conformance-20260912.md).

## 12. P3-STAGE-008-D preregistration

D is now preregistered under
`config/stage-learning/directional-boundary-development-screen.v1.json` and the owner-approved
A→B→C→D sequence. This state transition does not reopen Stage-007 evidence as fresh data and does
not touch 2022-2025.

The fresh screen reconstructs the same historical candidate population but excludes both the 515
previously excluded economic identities and the **entire 512-identity Stage-007 pool**. A deterministic
new 512-identity pool is split into 256 calibration-capacity identities and 256 evaluation-capacity
identities with zero overlap.

Calibration uses 2020 and may select at most 64 cases: 16 hash coverage, 32 old-instability-seed and
16 unchanged Stage-2-shaped controls. Calibration references are sealed before a fixed 27-point
relocation-guard grid is scored. The grid searches only 20-session absolute return, range traversal
and counter-direction excursion thresholds; Stage-2 thresholds remain frozen.

Evaluation uses 2021 but its 96 identities and quarter assignments are frozen in D1 **before their
2021 market data is read**. Evaluation data cannot be opened unless D2 freezes one eligible candidate.
The evaluation sample is morphology-blind and has no result-dependent backfill. Hard gates require
Stage-0 precision >=0.80, coverage >=0.55 and definite-negative FPR <=0.10, plus no degradation versus
old R0 beyond the preregistered limits. Even a pass is only `PROMISING_EXPLORATORY_REPAIR`.

The exact preregistration, four-phase budget and claim ceiling are recorded in
[D preregistration](../acceptance/stage-directional-boundary-repair-d-preregistration-20260912.md).

## 13. P3-STAGE-008-D fresh evaluation result

D1 selected 64 calibration cases from 2020 and froze a separate 96-identity 2021 evaluation plan
without reading evaluation market data. After sealing 128 calibration opinions, the complete
27-candidate grid produced exactly one eligible relocation guard: absolute 20-session return >=10%,
range traversal >=0.50 and counter-direction excursion <=0.60. The candidate was committed before
D3 opened evaluation data.

D3 preserved the morphology-blind quarter/hash plan. Ninety-five of the 96 frozen identities produced
one Primary case; the one shortfall was retained because backfill was forbidden. D4 then sealed 190
same-origin Primary opinions before scoring. Fifteen cross-role disagreements remain `AMBIGUOUS`.

The frozen repair reduced old-R0 false positives from 23 to 11 and retained all 15 definite Stage-0
positives. Precision improved from 0.3947 to 0.5769 and definite-negative FPR from 0.3538 to 0.1692,
with positive coverage fixed at 1.0. These are meaningful improvements but fail the preregistered
absolute gates of precision >=0.80 and FPR <=0.10, so the final disposition is
`REVISE_OR_PARK_EVALUATION_FAIL`.

Attribution shows the repair itself is incomplete rather than merely mis-tuned. Eight of the eleven
remaining false positives still enter through the inherited R0 center-shift arm even when the current
range is contracting or not expanding. That contradicts the already-approved rule that center shift
alone cannot assert Stage 0. The other three cases are directionally coherent but fall just below the
calibration-frozen 10% absolute-return guard; changing that threshold after evaluation is forbidden.

No evaluation-time parameter search, candidate replacement, supplement or hidden rerun occurred.
2022-2024 and 2025 remained closed, Stage 2 was unchanged, and Path Geometry stays
`implemented_unqualified`. Final evidence is
[D evaluation closeout](../acceptance/stage-directional-boundary-repair-d-evaluation-closeout-20260912.md)
and the machine result is `../../config/stage-learning/directional-boundary-evaluation-result.v1.json`.

## 14. Post-closeout root-cause validation and successor boundary

A bounded post-closeout diagnostic tested whether the inherited center-shift-only instability branch
is actually the dominant remaining semantic defect rather than a convenient narrative. No new data,
threshold trial or candidate replacement was opened.

Eight of eleven remaining false positives (72.7%) depend on `center_shift` independently keeping the
old R0 instability predicate positive, whereas only two of fifteen definite Stage-0 references
(13.3%) depend on that same branch. A diagnostic-only counterfactual removed only the independent
positive authority of `center_shift` while preserving the frozen relocation guard. It changed the
frozen-candidate metrics from 15 TP / 11 FP to 13 TP / 3 FP: precision 0.8125, positive-reference
coverage about 0.8667 and definite-negative FPR about 0.0462. The numerical gates would be crossed,
but because the counterfactual was formed after evaluation was observed it is **not** qualification
or permission to promote that exact predicate.

The four-responsibility review therefore confirms the root-cause direction: P3-STAGE-008 repaired the
directional-relocation boundary but did not fully replace the inherited positive-instability
proposition. This is also a design/implementation conformance defect because section 2 already froze
that center shift alone cannot assert Stage 0.

The next owner is **P3-STAGE-009 — Positive Instability Proposition Repair**. It must redesign the
positive `structural_instability_20` evidence before implementation, keep center shift descriptive or
modifier-only, preserve Stage 2, and forbid use of the consumed P3-STAGE-008 evaluation for threshold
selection. Root-cause evidence:
[post-closeout validation](../acceptance/stage-directional-boundary-root-cause-validation-20260912.md);
work-item route: [P3-STAGE-009](../work-items/stage-positive-instability-proposition-repair.md).
