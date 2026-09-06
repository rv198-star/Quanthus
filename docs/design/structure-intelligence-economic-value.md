# Structure Intelligence complete-P2 economic value experiment

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | open |
| Authority | Development-only research comparison; no Shadow, paper, live, or state-family promotion |
| Governing work item | `P3-STRUCT-002` |
| Technical predecessor | `P3-STRUCT-001`; evidence `../acceptance/structure-intelligence-mvp-real-dwd-acceptance-20260906.md` |
| Frozen run config | `../../config/structure-economic-run.development.v1.json` |
| Preregistration | `../acceptance/structure-intelligence-economic-preregistration-20260906.md` |

## 1. Question

`P3-STRUCT-001` proved that real DWD can feed a thin Structure Research ADM, that coarse
structure facts can enter the accepted expert/aggregator path, and that the resulting decision can
execute through P2. It did **not** establish economic value.

`P3-STRUCT-002` asks the next smaller question:

> On the already accepted 2020-2021 formal-strategy development population, does adding the same
> causal structure information to the entry-scoring surface materially change the complete
> capital-constrained P2 path, and does a coarse state representation add value beyond a continuous
> representation of that same information?

This is deliberately an economic representation test, not a thirteen-state build and not a new
strategy search.

## 2. Three frozen variants

All variants use `candidate.p4-trend-risk@1.0.1`, the same P4 Event Library population, Direct-DWD
provider, position-risk expert, system guardians, concentration/allocation, fixed five-unit capital,
costs, settlement, corporate-action handling, held-position review, exits, cooldown, and P2 matcher.
Only the entry scoring surface differs.

| Variant | Entry scoring change |
| --- | --- |
| C0 / `variant.native-baseline` | None. Exact accepted formal strategy. |
| C1 / `variant.structure-continuous` | Add one research-only continuous structure score. |
| C2 / `variant.structure-coarse` | Add one research-only coarse-state structure score. |

C1 and C2 consume the **same Structure ADM trajectory and the same episode memory**. C2 is only a
coarser representation; it is not allowed more lookback, anchors, candidate information, or future
evidence than C1. If structure evidence is unavailable, both variants abstain and the action falls
back to the unchanged C0 path.

The structure score is a second normal `ScoringOutput`; it has no veto, constraint, funding,
position, or execution authority. Existing governance is evaluated first and remains authoritative.

## 3. Frozen score mapping

The exact executable mapping lives in `config/structure-economic-run.development.v1.json`.

Continuous C1 uses the same support/breakout episode facts as the coarse recognizer:

- range intact: `0`;
- support breach: `-1`;
- support threatened: linearly `-1` at the support level to `-0.5` at the top of the fixed 2%
  threat band;
- breakout attempt: base `+0.5`, up to `+0.25` for distance above the frozen attempt level and
  `+0.25` per post-attempt holding close, capped at `+1`;
- unavailable: abstain.

Coarse C2 maps the same recognized outcomes to
`0 / +0.5 / +1 / -0.5 / -1` for range / attempt / accepted / threatened / breach; unavailable
abstains.

These are frozen research mappings, not approved trading parameters. No result-dependent tuning is
admitted in this work item.

## 4. Population and P2 identity

The experiment reuses the accepted formal-strategy scope:

- development entry decisions: `2020-01-01` through `2021-12-30`;
- T+1 execution context through `2021-12-31`;
- parent accepted P4 Event Library population;
- DWD Base V2.1 plus correction-001;
- accepted `close-to-next-open-v1` execution profile;
- arbitration 2022-2024 and permanent-blind 2025 remain sealed.

Structure construction scans from `2019-10-08` only to establish the causal 20-session / episode
history before the first economic decision. It reuses the **same Direct-DWD session** already owned
by the complete-P2 runtime and does not build a second warehouse projection.

Before C1 or C2 may be interpreted, C0 must exactly reproduce the accepted formal baseline net
return `0.06704618` and maximum drawdown `0.07102827`. A mismatch stops the experiment.

## 5. Comparison order and materiality

Primary comparison: **C2 - C1**. This determines whether coarse state compression adds material
value beyond continuous facts under the same information budget.

Secondary comparisons: **C1 - C0** and **C2 - C0**. These determine whether either structure
representation materially changes the accepted formal strategy.

A comparison is material when at least one preregistered condition is met:

- absolute net total return delta >= `0.01`;
- absolute maximum-drawdown delta >= `0.01`; or
- final-action change rate >= `0.02`.

Return, drawdown, tail loss, exposure, time in market, turnover, fees, action changes, and missed
opportunities are reported. No per-trade deltas are summed as portfolio ROI; every variant reruns
the complete funding/capital path.

Uncertainty uses the accepted paired non-overlapping 22-session block-bootstrap shape with 2,000
resamples and 95% intervals over daily-return differences.

## 6. Lifecycle decision

The result can produce only one of four bounded dispositions:

1. `no_material_structure_signal` — neither structure representation materially differs from C0;
2. `prefer_continuous_structure_facts` — coarse state is economically dominated by continuous
   structure facts;
3. `continuous_facts_sufficient_no_material_coarse_increment` — structure matters, but C2 adds no
   material increment over C1;
4. `coarse_state_increment_detected_requires_human_review` — C2-C1 is material and not dominated;
   this authorizes review of the next bounded refinement, **not** automatic thirteen-state expansion.

Any outcome remains development historical simulator evidence. It cannot enable P6, Shadow, paper,
live execution, model training, or sealed-partition reads.

## 7. Reproduction and stop conditions

A result-bearing Job must seal immutable C0/C1/C2 artifacts, comparison evidence, request digest,
code revision, Structure ADM identity, and `READY`. A second independent Job under the same exact
revision/request must reproduce the semantic result and Structure ADM manifest digests before this
work item can close.

Stop without interpretation on baseline reproduction mismatch, incomplete P2 reconciliation,
request/config drift, undeclared scoring change, sealed-partition access, DWD/PIT failure, or
artifact verification failure.
