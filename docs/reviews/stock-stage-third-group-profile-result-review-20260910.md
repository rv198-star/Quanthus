# Third-group morphology post-run review

| Field | Value |
| --- | --- |
| Scope | `P3-STAGE-003-D` post-profile semantic review |
| Source Job | `stage_313d48cfc06948e0a7fc7a3bd77b2657` |
| Source revision | `0a15ccc48ad00ab3f170f903e2ee0d27a385c26d` |
| Provenance | single assistant, separated quantitative / short-term-trading / fund perspectives; not human-expert signoff |
| Decision | `PASS_WITH_DIMENSIONAL_RECLASSIFICATION` |

## 1. Profile facts reviewed

The frozen 512-security 2020-2021 development profile completed successfully with 225,246 profiled
security-session observations. No Stage label, threshold search, future settlement, arbitration or
permanent-blind read occurred.

Key diagnostic shares were:

- 20-session direction: `up20=48.89%`, `down20=50.13%`, `flat20=0.98%`;
- recent velocity relative to 20-session velocity: `faster=49.49%`, `slower=50.15%`, `same=0.35%`;
- absolute 20-session directional efficiency: `low=76.10%`, `middle=22.46%`, `high=1.45%`;
- 10-session range width change: `contracting=51.42%`, `expanding=48.17%`;
- 60-session position: `lower-third=42.79%`, `middle-third=33.45%`, `upper-third=23.48%`.

The observed continuous distributions are broad and approximately centered around zero for returns,
center shifts and velocity deltas. This is useful morphology evidence, not a threshold selection.

## 2. `0 / 2` — disorder versus orderly consolidation

### Quantitative view

`low_abs_efficiency` covers 76.10% of all observations, so low directional efficiency is far too
common to identify either state by itself. Inside that low-efficiency population, 10-session width
contraction and expansion remain close to balanced (roughly 53% versus 46%). Exact `flat20` occurs in
less than 1% of observations and is therefore also not a viable standalone definition.

### Trading / fund view

The distinction remains useful, but it must be represented as **positive evidence of structure
order**, not as "anything without direction". An orderly consolidation requires a combination of
low directional efficiency, range contraction and center stability. Disorder requires evidence that
this organization is absent or unstable; it is not the residual bucket after every other rule fails.

### Disposition

- keep Stage codes `0` and `2` as candidate vocabulary;
- do not open a threshold search yet;
- next prerequisite is a narrow **Structure Order Fact Family / 结构秩序事实族** containing directional
  efficiency, range width/change and center shift/stability;
- no formal Stage 0/2 projection is authorized by this review.

## 3. `1 / 12` — downside lifecycle

### Quantitative view

The downside sample is large (`down20=112,906`). Within it, recent velocity is almost evenly split:
`faster_recent=57,800` and `slower_recent=55,106`. About 70% of down20 observations are also in the
lower third of the 60-session range. Therefore the data contain substantial examples of both
continuing downside and observable deceleration without requiring future lows.

### Trading / fund view

This pair is better modeled as a layered lifecycle than as two unrelated mutually exclusive labels:

- Stage 12 is a **downside context proposition**: current directional decline is established;
- Stage 1 is a **deceleration/stabilization condition proposition** inside or after that downside
  context, based only on T-known velocity/efficiency/position/rebound evidence.

This mirrors the successful Stage-10/11 pattern: a persistent context can coexist with a nested
current condition without forcing `primary_stage` immediately.

### Disposition

- retain both Stage 12 and Stage 1;
- prioritize a reusable **Trend Evolution Fact Family / 趋势演化事实族** as the next formal Facts
  milestone;
- candidate facts include multi-horizon returns, directional efficiency, short/medium velocity,
  velocity delta, drawdown/rebound and range position;
- no Stage-1/12 threshold grid is opened yet.

## 4. `8 / 9` — high-side morphology

### Quantitative view

Among observations that are both `up20` and in the 60-session upper third (`49,386` rows), recent
acceleration remains common (`28,220`, about 57%) while slowing remains material (`21,166`, about
43%). In the faster subset, 10-session range expansion dominates contraction (about 69.5% versus
30.5%). In the slower subset, contraction becomes more common than expansion (about 57.6% versus
42.4%).

This supports two distinct observable high-side conditions, but not two independent primary stages.

### Trading / fund view

The original names contain too much interpretation. The review freezes only the observable meanings:

- Stage 8 working meaning: **upside acceleration / extension modifier**;
- Stage 9 working meaning: **high-level price-volume divergence modifier**.

`赶顶` is not observable at T and `派发` implies actor/fund intent that the admitted data do not prove.
The profile contains useful marginal volume distributions but does not publish a joint
high-position × slowing-price × elevated-activity table. Therefore Stage 9 is not ready for a
formal modifier projection from this result alone.

### Disposition

- preserve codes `8` and `9` for traceability, but treat both as **modifier hypotheses**, not peer
  primary stages;
- Stage 8 can reuse the future Trend Evolution Facts plus range-position evidence;
- Stage 9 additionally needs a later narrow activity-relation profile/fact extension before any
  threshold search;
- no fund-intent or future-top wording may enter Facts or projection contracts.

## 5. Joint successor route

The review freezes the successor order:

1. **Trend Evolution Fact Family / 趋势演化事实族** — first; serves `1/12` and also provides the
   directional/acceleration substrate for `8/9`;
2. **Structure Order Fact Family / 结构秩序事实族** — second; serves `0/2`;
3. **high-side activity relation** — only after the first two, and only as the minimum additional
   evidence required for Stage-9 price-volume divergence.

No large B6/B7 implementation is authorized by implication. Each successor Facts milestone must be
narrow, versioned and independently gated.

## 6. Claim ceiling

This review does not authorize Stage labels, primary-stage arbitration, parameter search, external
validation, HUMAN_EXPERT_REFERENCE, daily/as-known production, strategy consumption or trading.
The 2022-2024 consumed arbitration partition remains closed to fresh reuse and 2025 remains untouched.
