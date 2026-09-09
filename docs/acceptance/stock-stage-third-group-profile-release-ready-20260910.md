# P3-STAGE-003 third-group morphology profile release-ready

| Field | Value |
| --- | --- |
| Decision | READY_FOR_ONE_DEVELOPMENT_PROFILE_RUN |
| Work item | `P3-STAGE-003-B`; opens `P3-STAGE-003-C` only |
| Runtime kind | `quanthus.stage-third-group-profile.v1` |
| Runtime config | `config/stage-learning/third-group-profile.v1.json` |
| Config SHA-256 | `sha256:9a31a8eb6f32e9aed5150e2076121933e89d1c730e4898da546dcf13aac79512` |
| Delivery-plan SHA-256 | `sha256:ec528c9e5c610714580b5f820062238ee805c9150ab59cdf63d513a38556193c` |
| Data scope | 512-security, 2020-2021 development-only retrospective profile |
| Stage labels | forbidden |
| Parameter search | forbidden |
| Formal B6/B7 registration | forbidden |

## 1. Frozen semantic decomposition

The profile does not assume six peer primary stages. It measures three subgroups:

- `0 / 2`: disorder versus orderly consolidation/compression;
- `1 / 12`: downside deceleration/stabilization versus continuing decline;
- `8 / 9`: observable upside acceleration/extension versus high-level price-volume divergence,
  with an explicit modifier hypothesis and no top/fund-intent claim.

No result from this Job may directly select a Stage threshold, primary stage, modifier status or
Structure Fact family.

## 2. Frozen continuous profile

The Job computes only T-only development morphology from uninterrupted admitted sessions:

- 5/10/20/60-session returns;
- signed 5/20-session directional efficiency;
- 10/20/60 range widths;
- current-vs-prior disjoint 10/20 width change and center shift;
- 20/60 range position, drawdown from rolling high and rebound from rolling low;
- 5/20 per-session return velocity and their delta;
- current-volume/prior-20 and recent-5/prior-20 volume ratios.

Fixed thirds and sign buckets are aggregate reporting coordinates only. They are not Stage gates.

## 3. Runtime and data boundary

```text
cohort scan: 2019 development history
cohort date: 2019-12-31
warm-up:     2019-07-01
report:      2020-01-02 -> 2021-12-31
cohort:      512 securities
history:     61 uninterrupted admitted sessions
```

The Job must reset and rewarm on non-admitted observations, listing/coordinate change or hidden
session gaps. Missing volume suppresses only volume metrics.

The Job publishes aggregate distributions and joint diagnostic counts only. Security codes, market
prices and observation rows are not published.

## 4. Explicitly closed authority

The run may not:

- emit `0/1/2/8/9/12` Stage labels;
- select or rank thresholds;
- register a formal Structure/indicator Fact or open broad B6/B7;
- read future settlement or strategy results;
- reread 2022-2024 arbitration;
- read/select 2025 permanent blind;
- select a holdout population;
- arbitrate `primary_stage`;
- enable daily, strategy, notification or trading use.

## 5. Post-run stop condition

After the one frozen profile completes, no implementation/threshold adjustment is allowed inside the
same batch. `P3-STAGE-003-D` must first review all three semantic subgroups and choose among retain,
merge, rename to observable semantics, demote to modifier, INCONCLUSIVE or continuous-facts-only.
Any later parameter grid or narrow B6/B7 Fact implementation requires a new preregistered contract.
