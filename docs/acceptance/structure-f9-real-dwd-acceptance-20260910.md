# P3-STRUCT-005 F9 support-facts real-DWD acceptance

| Field | Value |
| --- | --- |
| Decision | PASS / bounded Route-A acceptance |
| Work item | `P3-STRUCT-005-C` / #251; closes `P3-STRUCT-005-D` / #252 and parent #253 |
| Result-bearing revision | `44571610b1da1792a80bbd45ee7f180d59e8495a` |
| Runtime request | `config/structure-f9-run.development.v1.json` |
| Job A | `job_237a3e986b49490381f380a7ed5d4d14` |
| Job B | `job_f371d3f2cb0440aea2c49ceaad1c237b` |
| Run A | `run_237a3e986b49490381f380a7ed5d4d14` |
| Run B | `run_f371d3f2cb0440aea2c49ceaad1c237b` |
| Evidence scope | fixed three-security 2020Q1 development-retrospective slice |
| Official Stage labels | not authorized |
| B6/B7 / economic / strategy / trading | not authorized |

## 1. Frozen execution scope

Both Jobs used exactly the preregistered request:

```text
scan:    2019-10-08 → 2020-03-31
report:  2020-01-02 → 2020-03-31
stocks:  000001 / 300750 / 600519
scales:  20 / 60
support horizon: 60 sessions
view:    retrospective
```

The Event Worker ran `quanthus.structure-mvp.v1` at the exact result-bearing revision and dispatched
only `quanthus.structure_f9_run`. Both artifacts passed the deep verifier, which reconstructed
admission, base Structure v2, F9 support facts and the Stage-10/11 research projection from published
source rows.

## 2. Independent cold A/B reproduction

| Semantic identity | Job A | Job B | Result |
| --- | --- | --- | --- |
| input digest | `sha256:ade8e3dc9e5e14327c029c24208b48d01b59d1170eefd781a2e4e875b8e52d00` | same | PASS |
| input-readiness | `sha256:ff61690b80bae61070440a83ee3683f458135a7555bbfe942b7453bc5c93f4e6` | same | PASS |
| base Structure | `sha256:cd1b79db7b3ab4d6c38a970ad1b7747a03cbac5be93d02dd83948243891208fc` | same | PASS |
| F9 support facts | `sha256:6abbd976591739ede4451fd15ad2a11e8dd98519cc8039c2374f575c0035aff8` | same | PASS |
| Stage-10/11 projection | `sha256:191afc216c4f4abb829a572b26eb08a0afcad216100a1eb2afe1dca621c6bded` | same | PASS |
| summary/result | `sha256:77161ab0291dddf153afcebf5f5af66c4d889653a7737d4ed6a845efcd3e41dc` | same | PASS |
| deterministic review sample | 12 rows | byte-equivalent semantic rows | PASS |

The manifest hashes differ, as expected, because `runtime.json` contains real elapsed times. That
physical execution difference is deliberately outside semantic Fact identity.

Both Jobs report:

```text
provider_builds = 1
cross_job_cache_hits = 0
provider_lifecycle = job_local_TMP
attempted/admitted/rejected = 174 / 174 / 0
p2_probe_count = 0
p2_fill_count = 0
execution_authorized = false
official_label = false
```

Therefore the two Jobs are independent cold constructions rather than one cached result presented
twice.

## 3. Bounded F9 facts observed on real DWD

The reproduced result contains:

| Fact/process count | Value |
| --- | ---: |
| support bindings | 6 |
| signed support-distance observations | 241 |
| support breaks | 3 |
| support reclaims | 3 |
| unreclaimed rebound measurements | 25 |
| support-process observations | 241 |
| intact observations | 213 |
| unreclaimed observations | 25 |
| reclaimed observations | 3 |
| Stage-10 confirmation refs in research projection | 2 |
| Stage-10 active observations | 10 |
| Stage-11 active observations | 6 |

No source row was rejected in the report denominator. No Stage projection was published as a formal
LabelCard or consumer-facing state.

## 4. Outcome-blind semantic review

The preregistered 12-row deterministic review sample was inspected only for internal T-only semantic
consistency; no future outcome or economic result was used.

Observed behavior matches the frozen contract:

- pre-confirmation rows at unreclaimed age 0-4 remain outside Stage 10 even when price is materially
  below support;
- one process confirms Stage 10 at age 5 with support distance about `-4.92%` and rebound about
  `+3.65%`, therefore Stage 11 is still false at that point;
- later rows at ages 6-7 with rebound above 5% become Stage 11 under the same immutable Stage-10
  confirmation reference;
- most importantly, on `2020-02-14` the support distance has recovered to about `-1.44%`, so a
  current-depth recomputation would no longer satisfy the 2% Stage-10 depth rule, yet the support is
  still unreclaimed and rebound is about `+7.43%`; Stage 11 correctly remains active because it
  consumes the prior immutable Stage-10 confirmation;
- unrelated unreclaimed observations with only three sessions or insufficient depth/rebound remain
  outside the confirmed projection.

No obvious semantic contradiction was found in the 12-row sample. This directly validates the
Route-A correction that Stage 11 depends on prior confirmed structural failure, not on re-proving
Stage 10 from the current bar.

## 5. Accepted runtime binding

The following continuous capabilities are accepted only for the bounded retrospective Structure
research scope:

- exact breakout-start sibling rolling-low support candidate binding;
- same-support close break and reclaim interactions;
- current signed distance to the same frozen support;
- uninterrupted unreclaimed session age;
- causal post-break running low and post-break rebound while unreclaimed;
- append-only support-failure sequence preserving earlier break/confirmation evidence.

Stage 10/11 thresholds remain projection policy and are **not** part of Fact identity. The accepted
F9 facts do not state that the rolling low is universally strong support and do not imply a trading
action.

## 6. Claim ceiling and closeout

`P3-STRUCT-005` is accepted for the bounded engineering/semantic Route-A scope only.

This acceptance does **not** prove:

- external or forward validity of Stage 10/11 thresholds;
- economic value or portfolio benefit;
- HUMAN_EXPERT_REFERENCE agreement;
- official Stage 10/11 LabelCard qualification;
- daily/as-known readiness;
- strategy, notification, paper or live trading authority;
- B6/B7 or any other broader Structure capability.

Stage 5/6/7 remain `INCONCLUSIVE_PARKED`. The existing accepted B1-B5 slice remains unchanged; F9 is
an additive bounded B3/B4/B5 extension. The 2022-2024 consumed arbitration partition was not read and
2025 permanent blind remains untouched.
