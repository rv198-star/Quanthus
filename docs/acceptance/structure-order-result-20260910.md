# P3-STRUCT-007 Structure Order Fact Family qualification

| Field | Value |
| --- | --- |
| Decision | `PASS_BOUNDED_STRUCTURE_ORDER_FACT_FAMILY` |
| Work item | `P3-STRUCT-007-C`; closes D and parent after repository closeout |
| Result-bearing revision | `fc5a08ee34015cd29132dc4eb379edfcfaf3e4c6` |
| Event Worker image | `quanthus-event:git-fc5a08e` |
| Event Worker image digest | `sha256:b6b3fa5a67873df227d1d41515e24e804029cc2e04732ca3def3004d8693debb` |
| Job | `job_87fe531cec73477fbcc92df65a43a137` |
| Run | `run_87fe531cec73477fbcc92df65a43a137` |
| Job input digest | `sha256:708265f0e95970689c461efe044539a2df7b1421427f10146b0983d86cc54057` |
| Qualification request digest | `sha256:bbb00fb0098cb39171a82d65211459f72377d64d0fd5f5af55cc117e6866e5eb` |
| Manifest | `sha256:5deff11a24aeb0310315b96f5d9834f9936743ac60b1497d5534ea0ec4e392ee` |
| Fact contract digest | `sha256:57e5f60e000c4f238f83b4128d2df3acb1c7766fc01bc42829aeb085bbb8491d` |
| Structure Order digest | `sha256:1734db33f00195a3bcea2eec222773f13c437055b4539d7d255b9a12a2ad7450` |
| Source-input digest | `sha256:85ca06b7b8ef5f5362f2a5d4b91d823f264b26005233dea936e89f0e499b7bac` |
| Result digest | `sha256:9272ac067812e2d04c06e953510860f73ac73dbde4f837e0e63ab9d06b96bdc9` |
| Market-data reads / DWD provider opens | `0 / 0` |

## 1. Qualification path

The qualification consumed exactly the two already accepted immutable Support-Evolution/F9 cold
artifacts. Each source first passed its original deep verifier. Their exact published source-input
payloads were byte-identical and matched the frozen digest. The qualification then independently
re-admitted those rows and reconstructed the Structure Order Fact Family for A and B.

Both `structure-order-a.json` and `structure-order-b.json` are byte-identical and have the same
semantic digest:

```text
sha256:1734db33f00195a3bcea2eec222773f13c437055b4539d7d255b9a12a2ad7450
```

The deep verifier repeated the reconstruction after publication and passed.

## 2. Qualified fact inventory

The bounded three-security 2020Q1 slice contains 174 complete observations and 1,218 typed
measurements:

| Concept | Measurements |
| --- | ---: |
| `structure.geometry.range-width` | 522 |
| `structure.geometry.width-change` | 348 |
| `structure.geometry.range-center-shift` | 348 |

No report observation or measurement was unavailable in this qualification slice. That does not
remove the explicit zero-prior-width/reset failure semantics covered by deterministic tests.

Directional efficiency is **not** duplicated here. Stage-0/2 research must reuse the already
accepted `structure.geometry.directional-efficiency` from the Trend Evolution Fact Family.

## 3. Outcome-blind semantic review

The deterministic 12-row T-only review sample shows broad positive and negative width-change and
center-shift behavior without any need to assign a Stage label. Examples include strong width
contraction, strong width expansion, positive/negative center movement and different combinations of
those two dimensions.

No obvious semantic contradiction was found. The sample reinforces the original third-group
conclusion: range contraction by itself cannot mean orderly consolidation, and expansion by itself
cannot mean disorder. Stage 0/2 requires a later proposition combining these facts with directional
efficiency and explicit positive evidence rules.

## 4. Accepted runtime binding

The following concepts are accepted only for the bounded retrospective Structure Order scope:

- `structure.geometry.range-width` — v1 windows 10/20/60;
- `structure.geometry.width-change` — current 10 vs prior 10 and current 20 vs prior 20;
- `structure.geometry.range-center-shift` — current 10 vs prior 10 and current 20 vs prior 20.

The accepted family is a narrow B6 geometry subset. Broad B6 remains incomplete; no confirmed-pivot
or generic compression/expansion inventory is implied.

## 5. Claim ceiling

This PASS does **not** authorize or prove:

- Stage 0/2 thresholds, projection or primary-stage arbitration;
- accumulation/distribution intent;
- external/forward validity or economic value;
- HUMAN_EXPERT_REFERENCE agreement;
- SHADOW/QUALIFIED/ACTIVE LabelCard publication;
- daily/as-known production inference;
- strategy, portfolio, notification, paper or live trading consumption;
- wholesale Structure B6/B7 implementation.

The 2022-2024 consumed arbitration partition was not reread and 2025 permanent blind remains
untouched. The third-group price-structure substrate is now sufficient to separately design a
minimal Stage-0/2 proposition experiment. Stage-9 activity evidence remains a later optional
question.
