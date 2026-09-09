# P3-STAGE-001-H — Stage 7/10/11 repair/failure profile freeze

| Field | Value |
| --- | --- |
| Work item | `P3-STAGE-001-H` |
| Scope | Development-only second-group repair/failure morphology |
| Config | `config/stage-learning/second-group-repair-profile.v1.json` |
| Runtime | `quanthus.stage-second-group-repair-profile.v1` |
| Data | 2020-01-02 through 2021-12-31 development only; 512-security deterministic cohort |
| Entry | First confirmed Stage-4 observation under frozen first-group candidate |
| Holdout | No 2022-2024 reread; no 2025 access; no holdout population selection |
| Output | Summary-only continuous morphology; no Stage 7/10/11 labels or parameter winner |
| Disposition | RELEASE-READY FOR DEVELOPMENT-ONLY PROFILE |

## 1. Decision carried into the profile

The owner explicitly keeps Stage 5, 6, 10 and 11 as distinct stage codes. Related background/process
roles do not authorize merging. The same policy is frozen for all five second-group codes
`5/6/7/10/11` in `second-group-experiment.v1.json` and the repair-profile config.

This profile therefore changes evidence requirements, not stage identity:

- Stage 7 is no longer approximated as merely a deeper Stage-6 pullback. It requires deeper damage
  plus observable T-only repair/rebuild evidence.
- Stage 10 is no longer approximated by one support crossing. It requires persistent failure of a
  predeclared support role without prompt same-role reclaim.
- Stage 11 requires an already Stage-10-grade failure sequence and measures rebound only while the
  same failed support remains unreclaimed.
- A later reclaim/repair does not rewrite or delete an earlier valid Stage-10/11 observation.

## 2. Frozen measurements

### Stage 7 repair evidence

For each causal damage cycle after Stage-4 acceptance:

- causal running peak;
- causal trough since that peak;
- damage depth = `trough / peak - 1`;
- repair fraction = `(current - trough) / (peak - trough)` while a damaged cycle exists;
- complete repair when price reaches the prior causal peak again.

The already frozen deep-damage development candidates `12% / 16% / 20%` are used only as reporting
strata. This Job cannot choose a Stage-7 threshold.

### Stage 10 support failure evidence

The Job compares, but does not select between, two support roles already bound to the Stage-4 episode:

1. `acceptance_anchor` = frozen breakout/acceptance level;
2. `initial_low_support_candidate` = the pre-T low captured when the breakout episode began.

For each role it records first break, same-role reclaim, maximum unreclaimed age, depth below support
and reclaim distributions. The development persistence candidates are `3 / 5 / 8` sessions, derived
from the completed v1 reclaim-time profile and frozen before this new result is read. They are not
Stage-10 truth thresholds.

### Stage 11 unreclaimed rebound evidence

After a first support break, rebound is measured from the causal post-break running low only while
the same support remains unreclaimed. The reclaim session itself is excluded from unreclaimed-rebound
evidence. This prevents a generic rebound from being renamed Stage 11 without a prior failure chain.

## 3. Fail-closed boundaries

The profile must not:

- merge Stage 5/6 or Stage 10/11;
- emit Stage 7/10/11 or a primary stage;
- select a support role;
- select parameter winners;
- run a synthetic semantic panel or future settlement;
- read 2022-2024 or 2025;
- publish a formal Fact/Feature family, LabelCard, daily schedule, consumer or trading authority.

Missing/nonadmitted rows and listing/coordinate changes censor the path. They cannot manufacture
structural failure. Daily path rows and repair cycles are correlated process evidence, not independent
semantic reference samples.

## 4. Verification before release

Focused unit coverage must prove:

- all five second-group stage identities remain distinct;
- repair fraction is separate from damage depth;
- first break and same-role reclaim are tracked separately for both support roles;
- persistence candidates are reporting strata only;
- rebound on/after reclaim is not counted as unreclaimed Stage-11 evidence;
- missing data censors rather than confirms Stage 10;
- Worker submission maps exactly to the frozen config and does not touch the consumed qualification
  budget.

No market-result claim is made by this freeze document. Runtime results must be recorded separately
before any Stage-7/10/11 search space is frozen.
