# ADR 0016: Value-proportional warehouse data quality and product-scoped availability

- **Status:** Accepted as an architectural principle; implementation and production cutover remain separately gated
- **Date:** 2026-10-08
- **Authority:** explicit Owner instruction following the 2026-09-24 one-minute data recovery review
- **Scope:** permanent P10 data acquisition, warehouse ODS/DWD, data products and their consumer handoffs
- **Predecessor:** ADR 0015 (P10 ownership); P1/PIT historical contracts remain authoritative for their accepted products
- **Follow-ups:** P10-DATA-WH-001 / #495 (quality tiers and consumer contract); P10-DATA-WH-002 / #496 (daily-core publication independence)

## Core rule — 数仓铁律

> **数据质量以业务决策的可靠性为目标，而非以理论上的绝对精度为目标。质量要求必须与数据用途、实际风险和修复成本相匹配；局部、非关键的数据缺陷不得无条件阻断其他合格数据的交付。**
>
> **Do not optimize data precision in isolation from material consumer value. Preserve source truth, PIT, lineage and safety, and isolate optional defects to the smallest affected product/consumer.**

This is a default **decision rule for future P10 design and incident handling**, not a waiver of the existing 1m, ODS/DWD, protected research or trading admission contracts.

## Context and failure mode

The September 24 1m recovery repeatedly tried to prove all-vendor, all-minute numerical identity:

- 2,672 securities had two individually qualified, whole-security 1m candidates but unequal source-native minute OHLCV semantics. Exchange time-boundary conventions differ; numerical inequality alone is not proof that every stock's useful observations are unusable.
- 20 of 4,992 securities still lacked a qualified full 240-observed-minute source. Cross-frequency checks later showed 53 missing actual 1m positions and exact 48-bucket volume conservation against real Tencent-native m5, but did **not** supply missing 1m OHLC.
- The operational daily orchestrator requires four ODS lane READY states for a whole-date DWD attempt, while LLD 01 explicitly identifies 1m as **archive-only research data outside the accepted P1 scope**. A strict optional-minute gate thus became an unconditional daily business-delivery blocker.
- The extended incident cost dominated progress on consumer value. Absence of public complaints does **not** prove a source is correct; nor does a lack of perfect source equivalence prove it is unusable for every consumer.

The mistake was not preserving exact raw evidence; it was applying the **highest possible precision contract to every downstream product and making unrelated delivery dependent on it**.

## Decision

### 1. Every quality rule must name the affected product and consumer

P10 must define each product's actual prerequisites, purpose, acceptable observation semantics, quality grade, known missing/conflicting coverage and authorized consumers. Quality is not a universal "green" bit. A problem blocking a strict 1m replay is not automatically a problem blocking a correctly built daily-bar product.

Do not quietly downgrade or relabel a precision-sensitive consumer. Existing strategy, training, protected-partition, independent-reference and activation gates remain in force until separately reviewed by their owners.

### 2. Minimum cross-product hard invariants do not relax

For every quality grade and all warehouse publications, preserve:

- correct security/listing identity, trading date/session, units, price basis and source representation;
- point-in-time knowledge/effective time, frozen windows and protected partition boundaries;
- provenance, original raw SHA/manifest, lineage, reproducible transformation and immutable release/correction semantics;
- source-observed versus inferred/derived/unavailable explicitly distinguished; **no fabricated trades, OHLC, volumes, timestamps, residual share allocation or cross-source minute stitching disguised as observed truth**;
- consumer-specific stop gates for materially invalid, insufficient or ambiguous inputs, plus existing safety and trading activation controls.

Never use a degraded label to conceal a broken essential invariant. Source conflicts may remain unresolved; they cannot be silently declared identical or used in strict equal-semantics consumers.

### 3. Classify quality by consumer purpose, not by one global precision ceiling

| Product/consumer class | Admission goal | Allowed disposition |
| --- | --- | --- |
| **Daily core / foundational warehouse** | Reliable source-native daily data, identity, daily semantics, PIT, lineage and actually required upstream datasets | Publish independently when **its own** versioned contract is satisfied, even if unrelated archive-only 1m is unavailable |
| **Routine intraday / research 5m or 1m** | Native interval/source semantics, date/volume/price checks and observable coverage | Publish an **explicitly limited or unavailable** separate product where its versioned consumer contract allows; source conflicts/gaps remain visible |
| **Precision-sensitive minute, tick, execution or risk research** | Source-authentic observations and independent accuracy/timing proof at the demanded resolution | Fail closed when required completeness, exchange-boundary evidence, PIT or price/volume precision is missing |

An observation may have enough quality for one declared purpose and insufficient quality for another. Different source-native products are not interchangeable without an explicit reconciliation contract.

### 4. Local failure is local; independent delivery is a versioned architecture decision

A missing/conflicted optional lane must generate precise issue, status, manifest and retry/disposition evidence **without automatically failing otherwise qualified independent daily products**.

A new product-scoped READY or daily DWD release is **not** authorized by this ADR alone. #496 must first inventory actual consumers/dependencies and version the schema, publication, rollback and CN01/Hermes/OCI mirror acceptance. Existing four-lane READY and frozen artifacts remain unchanged until approved controlled implementation.

No silent drop of formerly required inputs, retroactive acceptance of old receipts, false whole-date READY or use of a daily product to satisfy an intraday consumer.

### 5. Time and precision expenditure require evidence of decision impact

Before a costly source chase, correction or global requalification, record:

1. the exact affected date/securities/fields and affected **business consumer**;
2. a credible severity estimate: does it change the consumer's states, entries, risk, attribution or backtest conclusion?
3. a bounded sample/reproduction and an explicit time/request/cost budget;
4. the simplest qualified alternative, including independent product delivery, bypass, temporary deferral or acceptance of disclosed limitations where contractually allowed;
5. the **stop/escalation condition**: if evidence does not establish material value from more precision work, leave an honest open debt/unavailable status and return effort to higher-value work.

An inability to prove material impact is not proof of safety for a precision-sensitive consumer. It is a reason **not to devote unlimited resources to a global perfection requirement without a justified beneficiary**. Repeated identical requests against a stable failed source are not progress.

### 6. Measure actual business service, not abstract perfect-data coverage alone

Track core data product availability and latency, per-product admitted coverage and explicit degraded/unavailable gaps, impact on protected decisions, number of consumers blocked, recovery/test effort, provider/network costs and the number of repeated attempts. Keep denominator and exceptions visible. Do not present raw HTTP success, source-level correctness, and consumer acceptance as the same state.

## Consequences and change control

- P10 is the sole source-qualification/warehouse engineering owner; business owners define admission sensitivity and accept products only under their own contracts (ADR 0015).
- #495 owns quality grades/manifest/consumer matrix and impact-cost policy; #496 owns independently buildable daily-core readiness and safe DWD publication, after real dependency inventory. Neither is implemented by documentation alone.
- Source research #492/#493 becomes **impact-led and bounded**: preserve open forensic evidence and only resume expensive precision recovery when demanded by a specific consumer or new independent source capability. Do not close facts simply because a lower-grade product may be usable.
- No changes to accepted P1 evidence, Base V2.1, previous ODS/DWD/OCI releases, exact 1m real-240 qualification, 2,672 existing conflicts, disabled timers, alerts, P3/P5 scientific truth or P6 trading controls arise automatically.
- An implementation PR changing warehouse publication or semantic contracts must include independent review, versioned consumer impact, adversarial missing-required-input cases, Linux shadow replay, rollback and explicit production approval.

## References

- [ADR 0015 — permanent P10 ownership](adr-0015-permanent-p10-data-line.md)
- [LLD 12 — P10 responsibilities](../design/lld/12-permanent-data-line.md)
- [LLD 01 — PIT data foundation and accepted P1 scope](../design/lld/01-data-foundation.md)
- [ODS / 1m reliability parent #456](https://github.com/rv198-star/Quanthus/issues/456)
- [P10 quality grades #495](https://github.com/rv198-star/Quanthus/issues/495)
- [P10 daily-core decoupling #496](https://github.com/rv198-star/Quanthus/issues/496)
