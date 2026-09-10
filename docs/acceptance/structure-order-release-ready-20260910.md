# P3-STRUCT-007 Structure Order Fact Family release-ready freeze

| Field | Value |
| --- | --- |
| Decision | `READY_FOR_IMMUTABLE_SOURCE_QUALIFICATION` |
| Work item | `P3-STRUCT-007-B`; authorizes C only after repository gates pass |
| Fact family | 结构秩序事实族 / Structure Order Fact Family |
| Fact contract digest | `sha256:57e5f60e000c4f238f83b4128d2df3acb1c7766fc01bc42829aeb085bbb8491d` |
| Qualification request digest | `sha256:bbb00fb0098cb39171a82d65211459f72377d64d0fd5f5af55cc117e6866e5eb` |
| Market-data reads in qualification | 0 |
| DWD provider opens in qualification | 0 |
| Stage 0/2 projection/search | not authorized / not authorized |

## 1. Frozen formal scope

The first version reuses two already registered concepts and adds only one missing concept:

- `structure.geometry.range-width`: 10 / 20 / 60 sessions;
- `structure.geometry.width-change`: current 10 vs prior 10 and current 20 vs prior 20;
- `structure.geometry.range-center-shift`: current 10 vs prior 10 and current 20 vs prior 20.

Accepted Trend Evolution `structure.geometry.directional-efficiency` is explicitly reused and not
redefined by this family. No volume, Stage, actor-intent or future-outcome concept is added.

## 2. Frozen semantic boundaries

Range width is midpoint-normalized. Width-change and center-shift compare the current window with the
immediately prior equal-length non-overlapping window. A negative width-change is only contraction;
it is not called accumulation or Stage 2. A near-zero center shift is only center stability; it is
not sufficient by itself to establish an orderly regime.

If prior width is zero, width-change alone is unavailable while center-shift remains independently
computable. Listing/coordinate changes, hidden session gaps and nonadmitted observations reset and
rewarm the sequence instead of carrying old geometry forward.

## 3. Frozen qualification sources

No new DWD extraction is authorized. Qualification consumes exactly the two accepted immutable
Support Evolution/F9 cold artifacts:

```text
run_237a3e986b49490381f380a7ed5d4d14
run_f371d3f2cb0440aea2c49ceaad1c237b
```

Their result-bearing source revision is
`44571610b1da1792a80bbd45ee7f180d59e8495a` and their exact source-input payload digest is:

```text
sha256:85ca06b7b8ef5f5362f2a5d4b91d823f264b26005233dea936e89f0e499b7bac
```

Each source must first pass its original F9 deep verifier. The two Structure Order builds must then
produce byte-identical payloads and semantic digests.

## 4. Frozen failure action

Any source-verification, formula, identity, reset, A/B reproducibility or deterministic-review
failure is retained as failure evidence. It does not authorize opening DWD, adding indicators,
changing formulas after seeing the result, or beginning a Stage-0/2 threshold search.

## 5. Claim ceiling

This release candidate can establish only bounded engineering/semantic reproducibility for rolling
range organization facts. It cannot establish Stage 0/2 thresholds, economic value,
HUMAN_EXPERT_REFERENCE agreement, daily/as-known readiness, strategy consumption or trading.
The 2022-2024 consumed arbitration partition remains closed and 2025 remains untouched.
