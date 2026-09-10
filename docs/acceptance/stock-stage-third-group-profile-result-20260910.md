# P3-STAGE-003 third-group morphology profile result

| Field | Value |
| --- | --- |
| Decision | `PASS_PROFILE_WITH_DIMENSIONAL_RECLASSIFICATION` |
| Work item | `P3-STAGE-003-C/D`; closes parent after repository closeout |
| Result-bearing revision | `0a15ccc48ad00ab3f170f903e2ee0d27a385c26d` |
| Job | `stage_313d48cfc06948e0a7fc7a3bd77b2657` |
| Run | `stage-third-group-profile-20260910T031429Z` |
| Manifest | `sha256:351f749db94742e847cb873796105c2d855bdc402ed664400e04678ac9a121c7` |
| Input digest | `sha256:3a362fb1cb1664e4eeb894c49cd3b75e7b556f67eac3914320ace76da638e340` |
| Scope | frozen 512-security, 2020-2021 development-only morphology profile |
| Stage labels / parameter search | none / none |
| 2022-2024 / 2025 | not read / untouched |

## 1. Runtime result

The governed RX6800 Stage Worker completed the exact frozen profile successfully with exit code 0.
The artifact verifier bound the result to the exact revision/config/input identity and published
aggregate-only distributions.

```text
profile_observation_count = 225246
profiled_rows             = 225246
nonadmitted_resets        = 8
listing_or_coordinate_resets = 469
hidden_session_gap_resets = 0
```

The job emitted no security codes, prices, observation rows or Stage labels. It performed no
parameter search, state selection, synthetic panel, future settlement, arbitration, holdout
selection, strategy-result access or permanent-blind read.

## 2. Frozen diagnostic results

Main diagnostic counts and shares:

| Diagnostic | Count | Share of 225,246 |
| --- | ---: | ---: |
| up20 | 110,128 | 48.89% |
| down20 | 112,906 | 50.13% |
| flat20 | 2,212 | 0.98% |
| faster_recent | 111,475 | 49.49% |
| slower_recent | 112,972 | 50.15% |
| low_abs_efficiency | 171,403 | 76.10% |
| middle_abs_efficiency | 50,580 | 22.46% |
| high_abs_efficiency | 3,263 | 1.45% |
| contracting10 | 115,828 | 51.42% |
| expanding10 | 108,490 | 48.17% |
| lower_third | 96,394 | 42.79% |
| middle_third | 75,340 | 33.45% |
| upper_third | 52,896 | 23.48% |

The profile also published the preregistered quantile distributions for multi-horizon returns,
directional efficiency, range width/change, center shift, range position, drawdown/rebound, velocity,
velocity delta and activity ratios.

## 3. Subgroup findings

### 0 / 2

Low directional efficiency is common and does not separate disorder from orderly consolidation.
Inside the low-efficiency population, width contraction and expansion remain close to balanced.
Stage 0 therefore cannot be a residual fallback or simply `low efficiency`; Stage 2 cannot be simply
`flat/low return`. The next prerequisite is a Structure Order Fact Family combining efficiency,
range-width evolution and center stability.

### 1 / 12

The profile contains a large downside sample (`112,906` down20 rows). Within it, recent acceleration
and deceleration are both well represented (`57,800` versus `55,106`), and roughly 70% of down20
observations lie in the lower third of the 60-session range. This supports retaining Stage 12 as a
downside context proposition and Stage 1 as a nested deceleration/stabilization condition, without
future-low hindsight.

### 8 / 9

Within `up20 + upper-third` observations (`49,386` rows), recent acceleration accounts for about 57%
and slowing about 43%. Faster high-side observations are much more often range-expanding, while
slower high-side observations are more often range-contracting. This supports preserving two
observable high-side conditions but treating them as modifier hypotheses rather than peer primary
stages.

The profile does not publish the joint high-position × slowing-price × elevated-activity table
needed to formalize Stage 9 price-volume divergence. Therefore no Stage-9 threshold or projection is
accepted here.

## 4. Frozen successor decision

The post-run review `docs/reviews/stock-stage-third-group-profile-result-review-20260910.md` freezes:

1. **Trend Evolution Fact Family / 趋势演化事实族** first — reusable substrate for Stage 1/12 and
   high-side acceleration/slowdown;
2. **Structure Order Fact Family / 结构秩序事实族** second — prerequisite for Stage 0/2;
3. high-side activity relation only afterward and only as the minimum additional evidence for
   Stage-9 price-volume divergence.

Stage 8 working meaning becomes `upside acceleration / extension modifier`; Stage 9 working meaning
becomes `high-level price-volume divergence modifier`. Historical codes are retained. `赶顶` and
`派发` are not admitted as observable Facts.

## 5. Claim ceiling

This PASS is a development morphology/profile and structural-review result. It does not authorize or
prove formal Stage labels, primary-stage compression, parameter grids, external validation,
HUMAN_EXPERT_REFERENCE, B6/B7 wholesale implementation, daily/as-known publication, economic value,
strategy consumption or trading. The 2022-2024 consumed arbitration partition was not reread and
2025 permanent blind remains untouched.
