# P3-STAGE-001-H 第二组 development morphology profile 结果

| 字段 | 值 |
| --- | --- |
| Work item | `P3-STAGE-001-H` |
| Job | `stage_58983238b6364013b6d531ce9c6b0b70` |
| Code revision | `bc8bfd48e0f278e2b206185609faa30b6582e3be` |
| Run | `stage-second-group-profile-20260909T115619Z` |
| Manifest | `sha256:bbf557378e1b18078fd3cb28584e292a1eb7a36c57fcd9a1f7bbacc90eefa4e5` |
| Disposition | PASS for development morphology evidence only |
| Data authority | 2020-2021 development only; 2022-2024 not read; 2025 permanent-blind not read |
| Population | deterministic 512-security development cohort |
| Label authority | none; no official 5/6/7/10/11 labels produced |

## 1. Execution result

The governed Stage Worker completed successfully with exit code 0. The profile starts only from an episode that satisfies the frozen Stage-4 development candidate and then observes up to 60 eligible sessions without using future settlement, arbitration, strategy results, or any post-2021 market row.

Observed volume:

- Stage-4 accepted episode starts: **3,425**.
- Path observations: **186,221**.
- 20-session anchor scale: 2,211 accepted episodes / 120,913 path observations.
- 60-session anchor scale: 1,214 accepted episodes / 65,308 path observations.
- Path stops: 290 coordinate/listing changes, 2 non-admitted rows, 3,133 horizon/report-end stops.

This is ample development density for second-group semantic work. It is not independent qualification evidence.

## 2. Stable morphology observations

### 2.1 Pullback depth is similar across the two scales

Current drawdown from the running post-acceptance peak:

| Quantile | 20-session scale | 60-session scale |
| --- | ---: | ---: |
| p75 | -2.83% | -3.48% |
| p50 | -8.44% | -10.15% |
| p25 | -15.58% | -18.53% |
| p10 | -22.98% | -26.63% |
| p05 | -28.14% | -31.43% |

The cross-scale alignment is strong enough to support a bounded development search for a shallow-pullback versus deeper-damage boundary. It does **not** prove one fixed market threshold.

Maximum drawdown within the observed path has median -19.39% on scale 20 and -21.82% on scale 60. Therefore the maximum path drawdown cannot itself define a contemporaneous pullback state; a path commonly contains both advance and later damage.

### 2.2 Advance background should be process memory, not mutually exclusive with pullback

Maximum extension from the Stage-4 acceptance close:

| Quantile | 20-session scale | 60-session scale |
| --- | ---: | ---: |
| p25 | +2.98% | +2.90% |
| p50 | +10.27% | +11.42% |
| p75 | +25.45% | +28.47% |
| p90 | +48.41% | +54.60% |

A security can first establish a meaningful advance and later be in a local pullback. This supports the already reviewed dimension rule: **Stage 5 is a trend/background hypothesis while Stage 6/7 are local structural processes; they must not be forced into a single mutually exclusive slot before evidence proves that compression is valid.**

### 2.3 First support-candidate break is not Stage 10

Using the initial low anchor from the accepted breakout episode only as an exploratory support candidate:

| Measure | 20-session scale | 60-session scale |
| --- | ---: | ---: |
| accepted episodes | 2,211 | 1,214 |
| episodes with first support-candidate break | 633 (28.63%) | 157 (12.93%) |
| broken episodes later reclaiming the same support candidate | 504 / 633 (79.62%) | 121 / 157 (77.07%) |
| median sessions to reclaim after first break | 3 | 3 |
| p75 sessions to reclaim | 8 | 8 |

This is a direct counterexample to treating one close below a support candidate as `10/结构失效`. Most observed breaks later reclaim, often quickly. A Stage-10 candidate therefore needs additional **persistent failure / failed reclaim / support-role** evidence. The accepted B1-B5 same-anchor loss primitive is useful evidence but is not a complete Stage-10 classifier.

### 2.4 Rebound magnitude alone is not Stage 11

Among paths with a support-candidate break, the maximum rebound from the post-break running low has a median near 13% on both scales and p75 near 23%. Because many such paths subsequently reclaim the support candidate, rebound size alone cannot distinguish `11/反抽` from successful repair/reclaim. A Stage-11 candidate must bind a prior persistent failure and explicitly prove that the relevant support has **not** been reclaimed at T.

## 3. Second-group disposition

The second group should now split by evidence readiness rather than by numeric code order:

- **5 / advancing background**: proceed to a bounded development rule family based on post-Stage-4 extension/trend persistence. Preserve coexistence with local pullback.
- **6 / pullback test**: proceed to bounded development search using current drawdown and intact-structure evidence. Candidate thresholds must be learned/reviewed, not hard-coded from one quantile.
- **7 / structural rebuild**: not yet ready for a final rule. It needs a T-only recovery/rebuild measurement after deeper drawdown; deeper drawdown alone is damage, not proof of repair.
- **10 / structural failure**: not ready for a final rule. First support-candidate break is explicitly rejected as sufficient evidence; add persistence/failed-reclaim and support-role semantics.
- **11 / post-break rebound**: not ready for a final rule. It requires prior Stage-10-grade failure evidence plus a rebound that remains below the same failed support role.

This is a semantic convergence result, not a failure to produce all five labels. The project must not add indicators merely to preserve the original numbering.

## 4. Next development gate

Next implementation should add only the missing T-only measurements needed to test the above hypotheses, then run a finite development reference/search cycle:

1. process memory that marks when an accepted breakout has established an advance background;
2. current recovery from the running trough after a meaningful drawdown, for 6/7 separation;
3. sessions continuously below the same support candidate and current distance below it;
4. rebound from the post-break running low while the same support remains unreclaimed;
5. explicit support-role identity so a failed breakout level is not silently upgraded to whole-trend failure.

Any parameter grid must be frozen before the next result-bearing reference/learning run. 2022-2024 fresh reuse remains forbidden and 2025 permanent-blind remains unopened/unselected.
