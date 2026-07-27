# Stock Stage State Machine

| Field | Value |
| --- | --- |
| Phase | P3 candidate label model |
| Status | Draft concept; not active in research, backtest, simulation, or trading |
| Version | 0.1 |
| Governing work item | `P3-LABEL-002` |

## 1. Purpose and boundary

This model describes the primary price/volume stage of one security. It is a candidate
single-valued label for the P3 feature and label system; it is not the position lifecycle state
machine and has no direct authority to create orders, force an exit, or bypass expert/veto logic.

The name "12-state model" refers to the twelve directional lifecycle states `1` through `12`.
The emitted vocabulary also contains neutral state `0` and freeze sentinel `X`, so an evaluator
may output fourteen distinct codes. This distinction must remain explicit in schemas and reports.

No numerical threshold, lookback, key-level algorithm, confidence rule, or minimum dwell time is
approved yet. Those details require versioned label cards and point-in-time acceptance evidence
before the model can leave draft status.

## 2. State vocabulary

### Neutral and exception control states

| Code | State | Semantic intent |
| --- | --- | --- |
| `0` | 无序震荡 (unordered oscillation) | Default state when price action is directionless or evidence is insufficient for a directional stage. |
| `X` | 数据异常 (data exception) | Suspension, consecutive one-price limit sessions, event gap, or another condition that makes ordinary stage inference invalid; freeze the stage decision. |

`X` is fail-closed control flow, not evidence of structural damage. It blocks or freezes a new
stage decision according to the future label card, while missing/invalid data alone must not force
a sale. Event gaps and one-price sessions remain observed facts with reason codes; they must not be
silently replaced by synthetic bars.

### Bottoming and accumulation

| Code | State | Semantic intent |
| --- | --- | --- |
| `1` | 止跌企稳 (stabilizing after decline) | Downward momentum is exhausted and stopping volume appears, but structure is not repaired. |
| `2` | 蓄势整理 (accumulation/consolidation) | Volatility contracts, the center of gravity stabilizes, and price action embeds an upward expectation. |

### Breakout and advance

| Code | State | Semantic intent |
| --- | --- | --- |
| `3` | 试探突破 (breakout probe) | First attack on a key level with expanding volume. |
| `4` | 突破接受 (breakout acceptance) | Price holds above the key level, pullback does not break it, and turnover confirms acceptance. |
| `5` | 上升推进 (advancing trend) | Trend expands with supporting price/volume behavior. |
| `6` | 回踩测试 (pullback test) | Contracting-volume retracement toward support without a broken trend. |
| `7` | 修复重建 (structural rebuild) | A deeper pullback is rebuilding structure and awaits a second confirmation. |

### Topping and decline

| Code | State | Semantic intent |
| --- | --- | --- |
| `8` | 加速赶顶 (accelerating top) | Volume and price accelerate, extension expands, and sentiment is exhausted; pointed-top path. |
| `9` | 高位分歧/派发 (high-level divergence/distribution) | High-volume stalling and gradual turnover; rounded-top path. |
| `10` | 结构失效 (structural failure) | A key support breaks and reversal is confirmed. |
| `11` | 反抽 (post-break rebound) | Technical rebound after failure, with insufficient volume or failure to reclaim the key level. |
| `12` | 下行推进 (declining trend) | Continuing decline or panic acceleration. |

## 3. Candidate transition rules

- `3 -> 4`: breakout confirmed.
- `3 -> 0` or `3 -> 2`: breakout fails without breaking the prior structure.
- `3 -> 10`: the probe resolves into structural failure.
- `6 -> 5`: pullback succeeds and advance resumes.
- `6 -> 7`: pullback is deeper and enters structural rebuilding.
- `7 -> 5`: rebuilding succeeds.
- `7 -> 9` or `7 -> 10`: rebuilding fails into distribution or structural failure.
- `11 -> 12`: rebound ends and decline resumes.
- `11 -> 1`: rare direct bottoming transition.
- `12 -> 1`: declining momentum exhausts, closing the directional cycle.

Breakout failure is intentionally an exit branch from `3`, not a separate state. It may be
promoted only if replay shows enough examples, stable distinct behavior, and measurable decision
value after costs. The transitions above are the currently proposed high-value constraints, not a
complete permission graph; unspecified transitions must not be guessed during implementation.

## 4. Promotion requirements

Before implementation or activation, each state needs a label card containing exact PIT inputs,
features and versions, mathematical rules, thresholds, lookbacks, minimum history, reason codes,
confidence/abstention behavior, and counterexamples. Acceptance must include:

- deterministic golden cases for every code and listed transition;
- explicit precedence for `X`, with tests proving it cannot become an automatic structural exit;
- transition-frequency, dwell-time, conflict, and unreachable-state audits by market era;
- separation of pointed-top `8` and rounded-top `9` behavior;
- an audit of the `3 -> 0/2` breakout-failure branch before considering a new state;
- stratified human review and multi-year out-of-sample stability;
- no use of future bars, future key levels, or current classifications in historical replay.

Until these gates pass, the vocabulary is documentation-only and may be used for discussion or
shadow annotation, never as an official feature, expert input, or action rule.
