# P3-STRUCT-005 F9 support-facts release-ready freeze

| Field | Value |
| --- | --- |
| Evidence status | pre-result / release ready |
| Work item | `P3-STRUCT-005-B` |
| Design | `docs/design/structure-support-facts.md` |
| Runtime request | `config/structure-f9-run.development.v1.json` |
| Runtime authority | fixed development-retrospective slice only |
| Real-DWD result budget | exactly 2 cold Jobs |
| B6/B7 | not opened |
| Official Stage labels | not authorized |
| Economic / strategy / trading | not authorized |

## 1. Frozen semantic boundary

The Route-A implementation formalizes continuous F9 evidence only:

- exact breakout-start sibling `structure.anchor.rolling-low` binding;
- same-support `close-cross-below` and `close-reclaim` interactions;
- signed distance to the frozen support;
- unreclaimed session age, with break session age = 0;
- causal post-break running low and `structure.geometry.post-break-rebound`;
- append-only `structure.episode.support-failure-sequence` observations.

The rolling low remains a support **candidate role**, not a universal support-strength claim. Missing
input, calendar discontinuity or listing/coordinate change censors the process and cannot create a
failure observation.

## 2. Stage 10/11 projection freeze

The research-only projection preserves the P3-STAGE-001-H development closeout:

```text
Stage 10 confirmation = unreclaimed age >= 5 AND distance <= -2%
Stage 11 = prior immutable Stage-10 confirmation ref
           AND same support remains unreclaimed
           AND post-break rebound >= 5%
```

Thresholds are outside Fact identity. Stage 11 explicitly does **not** require current distance to
remain 2% below support after Stage 10 has already been confirmed. Reclaim terminates current 10/11
projection without rewriting prior valid observations.

## 3. Pre-result counterexamples

Focused tests cover:

- exact sibling rolling-low identity rather than same-valued or moving anchors;
- first break and reclaim on the same support revision;
- break-day age=0 and subsequent uninterrupted session persistence;
- reclaim session excluded from unreclaimed rebound;
- Stage 11 after a prior Stage-10 confirmation even when current depth has recovered inside 2%;
- missing-data censoring without fabricated failure;
- prefix replay stability and no usage/run clocks in semantic fact identity;
- exact artifact deep reconstruction and rejection of tampered projection output;
- Event Worker admission of only the fixed repository F9 config.

## 4. Fixed real-DWD qualification

The only result-bearing scope is:

```text
scan:     2019-10-08 → 2020-03-31
report:   2020-01-02 → 2020-03-31
stocks:   000001 / 300750 / 600519
lookback: 20 / 60
support horizon: 60 sessions
view:     retrospective
cold Jobs: 2
```

Each Job must independently build the Direct-DWD provider (`cross_job_cache_hits=0`). The deep
verifier reconstructs admission, B3-B5 base Structure, F9 facts, projection counts and deterministic
review sample from published source rows.

## 5. Stop conditions and claim ceiling

Any Job failure, deep-verifier mismatch, semantic A/B drift, unexpected runtime cache reuse or
outcome-blind sample contradiction blocks P3-STRUCT-005-C. Do not repair after reading one Job result
inside the same frozen batch.

A PASS proves only bounded engineering/semantic reproducibility of F9 continuous facts. It does not
prove the Stage 10/11 thresholds are externally valid, profitable, QUALIFIED, daily-ready or suitable
for live trading. 2022-2024 arbitration and 2025 permanent blind are not read by this milestone.
