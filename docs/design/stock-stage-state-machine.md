# Stock Stage State Machine

| Field | Value |
| --- | --- |
| Phase | P3 candidate label model |
| Status | Draft concept; not active in research, backtest, simulation, or trading |
| Version | 0.2 |
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

## 3. Candidate transition graph

```mermaid
flowchart TD
    START([Start]) --> S0["0 无序震荡"]
    S0 -->|volatility contracts| S2["2 蓄势整理"]
    S1["1 止跌企稳"] -->|structure repairs| S2
    S2 -->|first breakout attempt| S3["3 试探突破"]
    S3 -->|breakout confirmed| S4["4 突破接受"]
    S4 -->|trend expands| S5["5 上升推进"]

    S3 -.->|retreat; structure intact| S2
    S4 -.->|acceptance fades; structure intact| S2
    S5 -.->|trend degrades; structure intact| S2
    S3 -->|key structure breaks| S10["10 结构失效"]
    S4 -->|key structure breaks| S10
    S5 -->|key structure breaks| S10

    S5 -->|contracting-volume pullback| S6["6 回踩测试"]
    S6 -->|pullback succeeds| S5
    S6 -->|deeper pullback| S7["7 修复重建"]
    S7 -->|rebuild succeeds| S5
    S7 -->|secondary stalling| S9["9 高位分歧/派发"]
    S7 -->|rebuild fails| S10

    S5 -->|volume acceleration| S8["8 加速赶顶"]
    S5 -->|high-level stalling| S9
    S8 -->|sentiment recedes| S9
    S8 -->|pointed-top reversal| S10
    S9 -->|support breaks| S10

    S10 -->|technical rebound| S11["11 反抽"]
    S10 -->|direct decline| S12["12 下行推进"]
    S11 -->|rebound ends| S12
    S11 -->|rare direct bottom| S1
    S12 -->|momentum exhausts| S1

    SX["X 数据异常：freeze classification"]
```

`X` is an interrupt applicable to any current state. When the input becomes valid again, the
evaluator resumes from the last valid state and current PIT evidence under rules that remain to be
specified; the diagram intentionally does not draw fourteen bidirectional edges to `X`.

### 3.1 Core transition rules

- `3 -> 4`: breakout confirmed.
- `4 -> 5`: breakout acceptance develops into an advancing trend.
- `3`, `4`, or `5 -> 2`: the bullish path retreats or degrades, but its key structure remains
  intact; return to accumulation/consolidation rather than declaring failure.
- `3`, `4`, or `5 -> 10`: the relevant key support breaks and structural reversal is confirmed.
- `5 -> 6 -> 5`: a normal pullback test followed by successful trend resumption.
- `6 -> 7`: the pullback is deeper and enters structural rebuilding.
- `7 -> 5`: rebuilding succeeds.
- `7 -> 9` or `7 -> 10`: rebuilding fails into distribution or structural failure.
- `5 -> 8 -> 9/10` and `5 -> 9 -> 10`: pointed-top and rounded-top paths remain distinct.
- `10 -> 11 -> 12` or `10 -> 12`: post-failure rebound or direct decline.
- `11 -> 1`: rare direct bottoming transition.
- `12 -> 1`: declining momentum exhausts, closing the directional cycle.

The shared `3/4/5 -> 2/10` exits make structural integrity the branch condition. A failed
breakout from `3` remains a transition to `2`, not a separate state; loss of acceptance from `4`
and trend degradation from `5` follow the same high-level distinction. State `0` is reserved for
directionless or insufficient evidence and is no longer a structured breakout-failure target.

The transitions above are the currently proposed high-value constraints, not a complete
permission graph. Unspecified transitions must not be guessed during implementation.

## 4. Promotion requirements

Before implementation or activation, each state needs a label card containing exact PIT inputs,
features and versions, mathematical rules, thresholds, lookbacks, minimum history, reason codes,
confidence/abstention behavior, and counterexamples. Acceptance must include:

- deterministic golden cases for every code and listed transition;
- explicit precedence for `X`, with tests proving it cannot become an automatic structural exit;
- transition-frequency, dwell-time, conflict, and unreachable-state audits by market era;
- separation of pointed-top `8` and rounded-top `9` behavior;
- an audit of the shared `3/4/5 -> 2/10` structural-integrity branch, including the `3 -> 2`
  breakout-failure path, before considering a new state;
- stratified human review and multi-year out-of-sample stability;
- no use of future bars, future key levels, or current classifications in historical replay.

Until these gates pass, the vocabulary is documentation-only and may be used for discussion or
shadow annotation, never as an official feature, expert input, or action rule.
