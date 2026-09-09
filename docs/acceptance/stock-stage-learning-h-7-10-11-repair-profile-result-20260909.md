# P3-STAGE-001-H — 7/10/11 Development Repair Profile Result

| Field | Value |
| --- | --- |
| Work item | `P3-STAGE-001-H` |
| Job | `stage_2aba1a0744a94fe4aaa1be64b82a32cb` |
| Revision | `db48c9f4c326377a8e25e57d27f2ea9c21b5bbe1` |
| Run | `stage-second-group-repair-profile-20260909T134119Z` |
| Manifest | `sha256:da98655c905cdbc63208c806fda8f91bb08ecd6edb6d01ddadd0ac1aa5cca113` |
| Population | 512 securities, 2020-2021 development only |
| Stage-4 starts | 3,425 |
| Repair cycles | 11,567 |
| Repair observations | 175,475 |
| Support observations | 43,303 |
| Disposition | **PASS AS DEVELOPMENT DESIGN EVIDENCE** |

## 1. Authority and claim ceiling

The Run read only the already-consumed development window. It did not reread 2022-2024 arbitration, did not read 2025 permanent blind, did not select a holdout, did not perform parameter search, and did not emit Stage 5/6/7/10/11 labels.

Stage 5, 6, 7, 10 and 11 remain distinct stage codes. This evidence does not authorize merging them, official LabelCards, daily scheduling, consumers, strategies or trading.

## 2. Stage 7 — depth is not repair

Across both 20- and 60-session scales, repair-fraction distributions are stable enough to freeze a bounded development search.

For deep-damage candidates 12% / 16% / 20%:

- median T-only repair fraction is roughly 11%-14%;
- p75 is roughly 26%-33%;
- p90 is roughly 48%-57%;
- only about 13%-34% of deep-damage cycles fully repair within the bounded path;
- among cycles that fully repair, median trough-to-full-repair time is about 9-14 sessions.

Therefore a deep drawdown alone cannot define Stage 7. The next development contract freezes repair-fraction candidates 25% / 40% / 50% against the already frozen deep-damage candidates 12% / 16% / 20%, for 9 bounded combinations.

Stage 7 remains a repair/rebuild state, not a synonym for a large drawdown. A current confirmed unreclaimed Stage-10-grade failure blocks Stage 7; a later reclaim may legitimately transition an immutable prior Stage 10/11 observation into a new Stage 7 observation.

## 3. Stage 10 — support role and persistence

The profile strongly separates the two provisional support roles.

### 3.1 Break incidence

| Scale | Acceptance anchor breaks | Initial-low support breaks |
| ---: | ---: | ---: |
| 20 | 1,707 / 2,211 = 77.2% | 633 / 2,211 = 28.6% |
| 60 | 898 / 1,214 = 74.0% | 157 / 1,214 = 12.9% |

### 3.2 Time to first break

| Scale | Acceptance anchor median | Initial-low support median |
| ---: | ---: | ---: |
| 20 | 6 sessions | 26 sessions |
| 60 | 5 sessions | 28 sessions |

The acceptance anchor behaves like a local breakout-acceptance boundary: it is crossed frequently and early. It is not sufficient by itself to carry the stronger Stage-10 structural-failure claim.

The deeper `initial_low_support_candidate` is therefore frozen as the **primary candidate support role for Stage-10 development search**, while explicitly remaining a development candidate rather than proven universal market truth.

### 3.3 One crossing remains insufficient

Even the deeper support is often reclaimed:

- 20-session scale: 504 / 633 = 79.6% eventually reclaimed;
- 60-session scale: 121 / 157 = 77.1% eventually reclaimed;
- reclaim median remains 3 sessions on both scales.

Therefore Stage 10 must require persistence plus material distance below the same support. The next bounded search freezes:

- unreclaimed persistence: 3 / 5 / 8 sessions;
- failure depth: 2% / 4% / 6% below support;
- support role: `initial_low_support_candidate`.

This yields 9 Stage-10 development candidates. Same-support reclaim terminates the current Stage-10 observation but does not rewrite its earlier PIT validity.

## 4. Stage 11 — prior failure plus unreclaimed rebound

Stage 11 remains a distinct stage code. It requires an already frozen Stage-10-grade failure reference and a rebound from the post-break running low while the same failed support remains unreclaimed.

At the 8-session persistence candidate on the deeper support, rebound distributions are especially stable across scales:

| Quantile | 20 scale | 60 scale |
| ---: | ---: | ---: |
| p50 | 2.63% | 3.24% |
| p75 | 5.20% | 5.01% |
| p90 | 8.24% | 7.35% |

This supports a later sequential Stage-11 search of 3% / 5% / 8% rebound after one Stage-10 candidate has been frozen. Rebound amplitude alone does not define Stage 11, and reclaim ends the unreclaimed-rebound thesis.

## 5. Sequential learning decision

To avoid an unnecessary combinatorial grid, the next development learning order is frozen as:

1. **Stage 10 first** — calibrate 3/5/8-session persistence × 2/4/6% failure depth on the deeper support candidate.
2. **Stage 7 second** — calibrate 12/16/20% deep damage × 25/40/50% T-only repair fraction, with Stage-10 conflict semantics available.
3. **Stage 11 third** — after Stage 10 is frozen, calibrate 3/5/8% unreclaimed rebound using that exact failure contract.

The executable design contract is `config/stage-learning/second-group-repair-experiment.v1.json`.

## 6. Remaining boundaries

- Runtime parameter search is still not authorized by this result artifact alone.
- Synthetic expert references remain development evidence, not human expert qualification.
- No official second-group label is published.
- 2022-2024 remains consumed and cannot become fresh again.
- 2025 remains unopened and unselected.
- Third-group states, daily operation, consumers and strategy/economic evaluation remain separately gated.
