# Stock Stage third-group morphology profile

| Field | Value |
| --- | --- |
| Design status | approved |
| Delivery gate | accepted |
| Work item | `P3-STAGE-003` |
| Parent context | `stock-stage-historical-learning.md`; accepted `P3-STAGE-002` |
| Runtime config | `../../config/stage-learning/third-group-profile.v1.json` |
| Runtime module | `../../src/quanthus/stage_learning/third_group_profile.py` |
| Scope | accepted development-only 512-security T-only continuous profile; no Stage labels or parameter search |
| Result | `../acceptance/stock-stage-third-group-profile-result-20260910.md`; `../reviews/stock-stage-third-group-profile-result-review-20260910.md` |

## 1. Decision

The third group is `0 / 1 / 2 / 8 / 9 / 12`, but the six codes are not assumed to be six peer
primary stages. The profile first tests three semantic subgroups using continuous T-only morphology:

1. **Order / consolidation — 0 vs 2.** Stage 0 must mean observable disorder, not "none of the other
   rules matched". Stage 2 must mean observable orderly consolidation/compression, not an inferred
   accumulation intent.
2. **Downside lifecycle — 12 vs 1.** Stage 12 is continuing decline; Stage 1 is observable downside
   deceleration/stabilization. Neither is allowed to read a future low or claim a durable bottom.
3. **High-side modifiers — 8 vs 9.** The observable meanings are **upside acceleration/extension**
   and **high-level price-volume divergence**. `赶顶` and `派发` remain historical hypotheses, not
   observable facts. The profile explicitly tests whether 8/9 should be modifiers over an advancing
   context rather than peer primary stages.

No primary-stage compression is designed here. `P3-STAGE-002` already showed why proposition layers
must be understood before forcing a single stage value.

## 2. Why profile before formal Facts

Most third-group ideas depend on historical planning groups F4-F8, while Structure B6/B7 remain
unopened. That is intentional. A new Structure Fact family should be created only after a concrete
consumer question proves which measurements are stable and reusable.

Therefore this milestone computes the necessary measurements **job-locally** from admitted daily
rows. A successful profile does not register B6/B7, a new semantic concept, FeatureFact, LabelCard or
ADM. The later D gate decides which measurements deserve a formal Structure/indicator owner.

## 3. Frozen T-only measurements

All current windows include T; return baselines use exact prior admitted sessions. Every sequence
resets and rewarms after non-admission, listing/coordinate change or hidden session gap.

### 3.1 Direction and efficiency

- 5 / 10 / 20 / 60-session simple returns;
- signed directional efficiency over 5 and 20 sessions:
  `(C_t - C_{t-n}) / sum(abs(C_i-C_{i-1}))`, bounded to [-1, 1];
- return velocity = simple return / session count;
- 5-vs-20 velocity delta, used only as a descriptive acceleration/deceleration axis.

The velocity delta is not a final market-physics claim. It is a unit-normalized development profile
quantity whose usefulness must be reviewed before formalization.

### 3.2 Range organization

For 10 / 20 / 60 admitted-session windows:

`width = (H-L) / ((H+L)/2)`.

The profile also records current 10-window width versus the preceding disjoint 10-window width,
current 20 versus preceding disjoint 20, and their center shifts. Width contraction and center
stability are separate dimensions: a narrow range whose center is migrating is not automatically
"蓄势整理".

### 3.3 Position, drawdown and rebound

- close position inside current 20 / 60 range;
- close / current-window high - 1;
- close / current-window low - 1.

These are causal rolling-window measurements, not confirmed swing pivots and not future-bottom
knowledge.

### 3.4 Activity

- current volume / previous 20 admitted-session mean;
- recent 5-session mean volume / the preceding disjoint 20-session mean.

Missing volume suppresses these activity metrics only; it does not destroy otherwise valid price
morphology. Amount is not substituted for volume and no fund-flow intent is inferred.

## 4. Diagnostic bands are not stage thresholds

The profile uses fixed thirds only to summarize aggregate morphology:

- rolling-range position: lower / middle / upper third;
- absolute 20-session directional efficiency: low / middle / high third;
- signs of 20-session return, 5-vs-20 velocity delta and 10-window width change.

These bands are reporting coordinates. The Job may not rank, select or publish a candidate Stage
threshold from them. Any later search space must be frozen in a new contract after the profile is
reviewed.

## 5. Runtime boundary

Frozen runtime population and data policy:

```text
cohort selection: 2019 development history
cohort size:       512 securities
warm-up:           2019-07-01 onward
report:            2020-01-02 -> 2021-12-31
minimum sequence:  61 uninterrupted admitted sessions
view:              development retrospective only
```

The Job emits aggregate metric distributions and joint diagnostic counts only. It does not publish
security codes, market prices or observation rows. It reads no future settlement, 2022-2024
arbitration, 2025 permanent blind, strategy result or holdout population.

## 6. Post-profile decision questions

Gate D must answer before any third-group search:

- Can `0` be positively identified as disorder, rather than residual classification?
- Is `2` distinguishable through compression + center stability + low directional efficiency, or is
  the proposed name too interpretive?
- Can `12` and `1` be separated with direction/velocity/rebound evidence without future leakage?
- Do `8` and `9` form useful independent propositions, and are they better modeled as modifiers over
  an advancing context?
- Which job-local measurements are sufficiently stable/general to become formal Structure or shared
  indicator Facts? Only those may open a narrow B6/B7 follow-up.

Valid outcomes include retain, merge, rename to observable semantics, demote to modifier, park as
INCONCLUSIVE, or keep only continuous Facts. The milestone must not force six passing labels.

## 7. Accepted result and successor route

The frozen Job `stage_313d48cfc06948e0a7fc7a3bd77b2657` completed successfully at revision
`0a15ccc48ad00ab3f170f903e2ee0d27a385c26d` with 225,246 profiled observations. The post-run review
returns `PASS_WITH_DIMENSIONAL_RECLASSIFICATION`.

The accepted interpretation is:

- **0 / 2 remain candidate vocabulary**, but low directional efficiency is too common to separate
  disorder from consolidation. Their next prerequisite is a **Structure Order Fact Family / 结构秩序事实族**
  combining directional efficiency, range-width evolution and center stability. No Stage-0/2
  threshold search is opened.
- **12 / 1 are retained as a layered downside lifecycle**: Stage 12 is a downside context proposition;
  Stage 1 is a nested deceleration/stabilization condition that may coexist with the downside context.
  Their next prerequisite is a **Trend Evolution Fact Family / 趋势演化事实族**.
- **8 / 9 are reclassified as modifier hypotheses**, not peer primary stages. Their observable working
  meanings are `upside acceleration / extension` and `high-level price-volume divergence`. `赶顶` and
  `派发` remain historical wording only, not admitted Facts. Stage 9 still lacks the necessary joint
  high-position × slowing-price × elevated-activity evidence.

Successor order was frozen as: Trend Evolution Fact Family first; Structure Order Fact Family second;
a narrow high-side activity relation only afterward if Stage 9 still requires it. The first two
successors have now both closed: `P3-STRUCT-006` accepted **趋势演化事实族 / Trend Evolution Fact
Family**, and `P3-STRUCT-007` accepted **结构秩序事实族 / Structure Order Fact Family**, each through
bounded immutable-source A/B qualification with zero new DWD/provider reads. The third-group
price-structure substrate is therefore sufficient to separately design a minimal Stage-0/2
proposition experiment. A high-side activity relation remains optional and later, only if Stage 9
still requires it. None of these acceptances authorize Stage labels, primary-stage compression,
holdout use, daily inference, strategy consumption or trading.
