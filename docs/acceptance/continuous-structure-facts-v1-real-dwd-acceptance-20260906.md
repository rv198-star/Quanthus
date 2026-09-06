# Continuous Structure Facts v1 Real-DWD acceptance (2026-09-06)

| Field | Value |
| --- | --- |
| Work item | `P3-STRUCT-003` |
| Acceptance | ACCEPTED / CLOSED |
| Result-bearing revision | `fd69b35b725310133a06ec19d27955bb15e5ba6f` |
| Run A | `job_8ce5f20bdbafa9f0040348e950797108` |
| Run B | `job_27181468d6da96eb0110bac567e1cd6a` |
| Outcome-blind review | `continuous-structure-facts-v1-outcome-blind-review-20260906.md`, 12 / 12 PASS |
| Authority | development research fact product only |

## 1. Qualification lineage

The first qualification attempt on revision `1ea8929c6d111f9deefd067882b9d41af9f05b69`
was canceled before result publication after an independent pre-result audit found one schema
invariant gap: `attempt_volume_ratio20` was episode-bound by design but was not included in the
row-level episode-field consistency check. Both original Jobs reached terminal `canceled` with no
`result_ref` and no result access.

The bounded pre-result repair was committed as
`fd69b35b725310133a06ec19d27955bb15e5ba6f` and recorded in
`continuous-structure-facts-v1-pre-result-schema-repair-20260906.md`. The repair tightened schema
validation and corrected a compatibility-expert docstring; it did not change the builder's emitted
facts, price-only episode trigger, anchors, scoring behavior, run scope, or thresholds.

## 2. Exact A/B reproduction

Both result-bearing Real-DWD Runs used the same fixed request and exact code revision.

```text
request digest:
sha256:b97f0db7ef3e7fdb55be7fac7e8d71a82f45210836c96d1400b5f25bb8f185f1

input digest:
sha256:fcbfa532cde280ae88d53cd7fd3d25f4bda9c7f96b6deab3c98e38753b24da94

result digest:
sha256:1875a25d13e1daacd3c41769a2e45dcc6cb24eb5dfa0cf3dfebc45685716f31c

ADM manifest digest:
sha256:708c80beaa07e1932aea2ef8cf2cb860ea569c3ffdc4975545cead35ddbbb010

artifact manifest SHA-256:
sha256:3a4d1d536479ee1b9fa39337b99d394d3cef891202cc2ae2b5122716bd3fa8f6

structure-facts.jsonl SHA-256:
sha256:fb45ba329010f315a5669fb4a85270e1b855e72f6a52f74e5b3f8757bb9ddcef

summary.json SHA-256:
sha256:979c12346739537def3f498deab6ecfc4d921f359790f2e181f146e017723027
```

All of the above identities reproduced exactly in A and B.

## 3. Real-DWD fact and consumer evidence

The accepted bounded scope produced 174 report rows:

```text
rows:                  174
available:             174
unavailable:             0
active episode rows:    64
ended episode rows:      3
episode attempt rows:    2
history reset rows:      0
```

The explanation-only coarse renderer produced:

```text
breakout_accepted:     60
breakout_attempt:       4
range_intact:          86
support_breach:        13
support_threatened:    11
```

These renderer counts are not part of fact authority. Continuous fact rows themselves contain no
coarse-state field.

The compatibility consumer demonstrated direct `Continuous Facts -> FeatureFact -> Expert ->
Aggregator` ingestion:

```text
evaluated rows: 174
applicable:       64
abstained:       110
trial_entry:      64
```

The existing P2 compatibility path then executed five bounded probes and produced five fills in
both A and B. These fills prove engineering compatibility only; they are not portfolio or economic
acceptance evidence.

## 4. PIT, episode, and explainability acceptance

The accepted contract is:

- prior-20 support/resistance excludes T;
- breakout episode creation is price-only (`T close > prior rolling resistance`);
- volume is an independent descriptive fact and does not gate episode existence;
- attempt breakout/support anchors and attempt identity freeze at episode creation;
- later rows publish current rolling anchors separately from frozen episode anchors;
- active rows advance episode age/hold progress without rewriting earlier facts;
- a close below the frozen breakout level ends the old episode explicitly;
- a later new crossing receives a new episode identity;
- gap/reset clears causal sequential state and never means bearish damage or forced sale;
- active/ended rows are locally explainable from their own fields; and
- coarse states remain read-only explanation projections rather than computational fact authority.

Synthetic adversarial tests cover reset/missing-data behavior and low-volume price-only episode
creation. The frozen Real-DWD report slice contained no reset row, so no real-sample reset claim is
made.

## 5. Outcome-blind semantic review

The review rule was committed before Run A result access at `6a4e0e1`. It selected 12 rows using
only security/date and episode lifecycle fields, with a deterministic round-robin fill. The review
projection exposed only T-close-visible facts.

Result:

```text
12 / 12 PASS
```

A separate Decimal-based check recomputed rolling and episode margins exactly and verified
active/ended lifecycle consistency. No coarse-state authority or future/outcome field was present.
Detailed evidence is in `continuous-structure-facts-v1-outcome-blind-review-20260906.md`.

## 6. Engineering gate disposition

| Gate | Result |
| --- | --- |
| Fact schema | PASS |
| PIT / causal semantics | PASS |
| Anchor identity | PASS |
| Episode identity | PASS |
| Gap/reset semantics | PASS by synthetic contract; no reset row in Real-DWD review slice |
| Real-DWD deterministic A/B | PASS |
| Expert direct consumption | PASS |
| Aggregator/P2 compatibility | PASS |
| Coarse renderer decoupling | PASS |
| Outcome-blind review | PASS, 12 / 12 |
| Economic improvement | NOT REQUIRED / NOT CLAIMED |
| 13-state | NOT AUTHORIZED |
| ML | NOT AUTHORIZED |
| Shadow / paper / live | NOT AUTHORIZED |

## 7. Accepted disposition and claim ceiling

```text
P3-STRUCT-003: CLOSED
Continuous Structure Facts v1: ACCEPTED for development research consumption
Production Structure Expert: NOT AUTHORIZED
Economic superiority: NOT CLAIMED
13-state expansion: NOT AUTHORIZED
Model training: NOT AUTHORIZED
Shadow / paper / live: NOT AUTHORIZED
```

The accepted architectural direction is now that continuous anchors, signed margins, independent
volume evidence, and episode memory form the primary Structure Intelligence fact layer. Coarse
states may remain as replaceable explanation/UI projections. Future value work must be a separate,
preregistered consumer/expert milestone rather than a silent extension of this acceptance.
