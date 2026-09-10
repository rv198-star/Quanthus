# P3-STRUCT-006 Trend Evolution Fact Family release-ready freeze

| Field | Value |
| --- | --- |
| Decision | `READY_FOR_IMMUTABLE_SOURCE_QUALIFICATION` |
| Work item | `P3-STRUCT-006-B`; authorizes C only after repository gates pass |
| Fact family | 趋势演化事实族 / Trend Evolution Fact Family |
| Fact contract digest | `sha256:480db6df61901322c107da44eb41687bf4d19d2c6d1297a846a7af68cd224e03` |
| Qualification request digest | `sha256:4d3ef80711685a2154054fa34791a1d6837a7f70f4269c29b0a19abe238c9a98` |
| Market-data reads in qualification | 0 |
| DWD provider opens in qualification | 0 |
| Stage projection / search | not authorized / not authorized |

## 1. Frozen formal scope

The first version formalizes only seven threshold-free Structure concepts:

- `structure.geometry.window-return` at 5/20/60 sessions;
- `structure.geometry.directional-efficiency` at 5/20 sessions;
- `structure.geometry.return-velocity` at 5/20 sessions;
- `structure.geometry.velocity-delta` at 5 versus 20 sessions;
- `structure.geometry.range-position` at 60 sessions;
- `structure.geometry.drawdown-from-window-high` at 60 sessions;
- `structure.geometry.rebound-from-window-low` at 60 sessions.

Range-width/center-stability and activity are explicitly outside this milestone. They remain future
Structure Order / high-side-activity work rather than being pulled into Trend Evolution for inventory
completeness.

## 2. Frozen semantic corrections

`velocity_delta = return_velocity_5 - return_velocity_20` is a raw mathematical difference. Its sign
is not named `accelerating` or `slowing` in the Fact schema because the interpretation reverses with
direction context. Stage/proposition layers must bind a direction before applying those words.

Rolling 60-session high/low are causal window extrema, not confirmed swing tops or bottoms. A later
reversal cannot backfill a different meaning into the original measurement.

Every fact requires same-listing/same-coordinate contiguous admitted daily history. Nonadmission,
hidden session gaps or identity/coordinate changes reset and rewarm. Zero range suppresses only the
range-position measurement.

## 3. Frozen qualification sources

No new DWD extraction is authorized. Qualification consumes exactly the two previously accepted
`P3-STRUCT-005` cold artifacts:

```text
run_237a3e986b49490381f380a7ed5d4d14
run_f371d3f2cb0440aea2c49ceaad1c237b
```

Both were independently constructed at result-bearing revision
`44571610b1da1792a80bbd45ee7f180d59e8495a`. Their exact source-input payload digest is:

```text
sha256:85ca06b7b8ef5f5362f2a5d4b91d823f264b26005233dea936e89f0e499b7bac
```

Each source artifact must first pass the original F9/Support Evolution deep verifier. Only then may
its immutable `source-inputs.jsonl` rows be re-admitted and used to build Trend Evolution Facts.

## 4. Frozen acceptance

C passes only if:

1. both original F9 artifacts pass their original deep verifier;
2. their source-input payloads are byte-identical and equal the frozen digest;
3. independently reconstructed Trend Evolution payloads and semantic digests are byte-identical;
4. formal metric tests match the preregistered third-group formulas exactly;
5. deterministic review samples match across A/B and show no obvious T-only semantic contradiction;
6. `market_data_reads=0` and `dwd_provider_opens=0`;
7. no Stage projection, parameter search, LabelCard or execution authority is emitted.

A failure does not authorize reading DWD, adjusting formulas after seeing the result, or expanding the
fact inventory. The failed evidence must be retained and reviewed before a new version.

## 5. Claim ceiling

This release candidate can establish bounded engineering/semantic reproducibility only. It cannot
establish Stage 1/12 thresholds, Stage 8/9 modifier thresholds, future predictive value, economic
value, HUMAN_EXPERT_REFERENCE agreement, daily/as-known readiness or trading authority. The
2022-2024 consumed arbitration partition remains closed and 2025 remains untouched.
