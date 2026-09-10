# P3-STRUCT-006 Trend Evolution Fact Family qualification

| Field | Value |
| --- | --- |
| Decision | `PASS_BOUNDED_TREND_EVOLUTION_FACT_FAMILY` |
| Work item | `P3-STRUCT-006-C`; closes D and parent after repository closeout |
| Result-bearing revision | `fc75f5993d342809c9e01dece3592c039e728b1b` |
| Event Worker image | `quanthus-event:git-fc75f59` |
| Event Worker image digest | `sha256:6b95edce2ef4c806d132773872516c94aebbae92aaa6bc9ab25f07d675b8dd79` |
| Job | `job_bb986ee743994981ab61267e70f6f7f7` |
| Run | `run_bb986ee743994981ab61267e70f6f7f7` |
| Job input digest | `sha256:535a5e94d10b7a3478e3cce7151e0f8323c50669b10a51aa03dd3b21532cf0a0` |
| Qualification request digest | `sha256:4d3ef80711685a2154054fa34791a1d6837a7f70f4269c29b0a19abe238c9a98` |
| Manifest | `sha256:b8b909db08d2b88aaa0d266ae486a38f77e40b3e347ede54805b95317a9656be` |
| Fact contract digest | `sha256:480db6df61901322c107da44eb41687bf4d19d2c6d1297a846a7af68cd224e03` |
| Trend Facts digest | `sha256:fa9c93f8ddc339d7f1f477d596f4a50485252f8b81aabf20673ab2f3db88b9a8` |
| Source-input digest | `sha256:85ca06b7b8ef5f5362f2a5d4b91d823f264b26005233dea936e89f0e499b7bac` |
| Result digest | `sha256:ec32fe6107bf6f8d8f760c48a28875a132809d1b01ff1454b8aa56411ab17ea9` |
| Market-data reads / DWD provider opens | `0 / 0` |

## 1. Qualification path

The qualification did not extract market data. It consumed exactly the two accepted immutable
`P3-STRUCT-005` source artifacts:

```text
run_237a3e986b49490381f380a7ed5d4d14
run_f371d3f2cb0440aea2c49ceaad1c237b
```

Each source artifact first passed the original Support-Evolution/F9 deep verifier. Their
`source-inputs.jsonl` payloads were byte-identical and matched the frozen source digest. The
qualification then re-admitted those exact published source rows and independently reconstructed the
Trend Evolution Fact Family for A and B.

Both `trend-facts-a.json` and `trend-facts-b.json` have the exact same semantic/file digest:

```text
sha256:fa9c93f8ddc339d7f1f477d596f4a50485252f8b81aabf20673ab2f3db88b9a8
```

The deep verifier repeated this reconstruction after publication and passed.

## 2. Qualified fact inventory

The bounded 2020Q1 three-security slice contains 174 complete observations and 1,914 typed
measurements:

| Concept | Measurements |
| --- | ---: |
| `structure.geometry.window-return` | 522 |
| `structure.geometry.directional-efficiency` | 348 |
| `structure.geometry.return-velocity` | 348 |
| `structure.geometry.velocity-delta` | 174 |
| `structure.geometry.range-position` | 174 |
| `structure.geometry.drawdown-from-window-high` | 174 |
| `structure.geometry.rebound-from-window-low` | 174 |

All 174 report observations had complete contiguous history in this bounded source slice. No
measurement was unavailable. This is a property of the qualification slice, not a promise that zero
range or history gaps cannot occur elsewhere; the explicit unavailable/reset behavior remains part of
the accepted contract and is covered by deterministic counterexamples.

## 3. Outcome-blind semantic review

The deterministic 12-row review sample was inspected without future outcomes or Stage labels. No
obvious contradiction was found.

A key counterexample validates the separation between mathematical velocity delta and semantic
acceleration. For `300750` on `2020-03-26`, the 20-session return is about `-19.97%`, while the
5-session return is about `+3.99%`; the 5-vs-20 velocity delta is therefore positive
(`+0.017964...`). Calling every positive delta "acceleration" would be semantically wrong: in this
context the recent path has rebounded/decelerated relative to a negative medium-term background.
The formal Fact correctly publishes only the signed velocity difference and leaves directional
interpretation to a later proposition layer.

Other sample rows show positive and negative directional efficiency, high/low range positions and
high/low distance measurements without inventing confirmed tops, bottoms or future reversal claims.

## 4. Accepted runtime binding

The seven concepts are accepted only for the bounded retrospective Trend Evolution scope. Their
first-version algorithms/windows are those frozen in `config/structure-trend-evolution.v1.json`.

This fact family is now a reusable substrate for later research into:

- Stage 12 downside context;
- Stage 1 nested deceleration/stabilization condition;
- Stage 8 upside acceleration/extension modifier;
- Stage 9 directional/high-position substrate before any separate activity relation.

The acceptance does not itself create any of those propositions or thresholds.

## 5. Claim ceiling

This PASS does **not** authorize or prove:

- Stage 1/12 or Stage 8/9 threshold selection;
- primary-stage arbitration;
- Stage 9 price-volume divergence (activity relation is still missing);
- external/forward validity or economic value;
- HUMAN_EXPERT_REFERENCE agreement;
- SHADOW/QUALIFIED/ACTIVE LabelCard publication;
- daily/as-known production inference;
- strategy, portfolio, notification, paper or live trading consumption;
- wholesale Structure B6/B7 implementation.

The 2022-2024 consumed arbitration partition was not reread and 2025 permanent blind remains
untouched. The next separately gated fact-family milestone remains **结构秩序事实族 / Structure Order
Fact Family**.
