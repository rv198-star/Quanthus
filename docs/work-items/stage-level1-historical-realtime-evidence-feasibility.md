# P3-STAGE-032 — Official Level-1 historical realtime evidence feasibility

| Field | Value |
| --- | --- |
| Design status | approved bounded source-evidence experiment |
| Delivery gate | closed no-pass; historical PIT source search stopped |
| Parent | `P3-STAGE-031` |
| Machine contract | `../../config/stage-learning/stage-level1-historical-realtime-evidence-feasibility.v1.json` |
| Stage / source-integration / strategy authority | none |

## Objective

Test one final official-market-data evidence hypothesis before stopping the current PIT source search:
can exact historical Level-1 message/snapshot versions be tied to official contemporaneous dissemination
at or before T=15:30 and to an auditable correction/replacement lineage?

This phase evaluates evidence, not market semantics. It does not construct a new daily bar and does not
run Stage10/11.

## Frozen products

Exactly two products/evidence chains are allowed:

1. **SSE Level-1 intraday snapshot historical chain** — official Level-1 historical snapshot product
   plus the contemporaneous Level-1 dissemination semantics applicable to the frozen period;
2. **SZSE Level-1 realtime historical-retention chain** — official Level-1 realtime dissemination plus
   official evidence of how the same product/schema family is historically retained.

No Level-2, tick-by-tick, commercial feed or product substitution is allowed in this phase.

## Fixed probes

Dates are frozen before evidence collection:

- `2019-10-08`
- `2020-01-02`
- `2020-03-31`

SSE security: `600519`. SZSE securities: `000001`, `300750`. Dates or securities cannot be changed
because evidence is difficult to find.

The historical decision cutoff remains T=15:30 Asia/Shanghai.

## Evidence unit

A passing unit is:

> official product/schema × security × trade date × exact message/snapshot version

and must bind:

- historically applicable official schema/product identity;
- original raw message/snapshot or immutable batch identity;
- sequence/version identity and retransmission/replacement/correction semantics;
- official dissemination/publication evidence sufficient to decide `<= T 15:30`;
- complete correction/replacement lineage or an equivalent original-message immutability property;
- retainable raw/audit provenance and deterministic field interpretation.

Market event time, generation time, a modern file hash or the word `realtime` does not satisfy the
publication/version gate by itself.

## Route

### P032-A — Freeze contract

Close once the two products, three dates, fixed securities, evidence unit, pass/fail logic and
change-control triggers are frozen.

### P032-B — SSE evidence audit

Inspect only official SSE/CIIS public documentation for Level-1 historical snapshot scope, historical
schema applicability, original message/batch identity, delivery/dissemination clocks, corrections,
retransmissions and audit retention.

### P032-C — SZSE evidence audit

Inspect only official SZSE/SSIC public documentation for Level-1 realtime semantics, historical schema
applicability, retransmission/correction identity and any official historical-retention chain.

### P032-D — Fixed probes

Classify the fixed dates/securities. Do not download broad datasets. If product-level documentation
already proves a hard evidence gap, stop the probe rather than collecting current historical values.

### P032-E — Evidence matrix

For each venue classify product/schema applicability, exact version identity, dissemination-time proof,
correction completeness and audit retention as `PASS`, `FAIL` or `UNPROVEN`.

### P032-F — Four-party closeout

Closed. Four independent role reviews accepted the no-pass 4/4 and selected the same phase decision:
stop the current Stage historical PIT source-evidence hunt. P029 remains retrospective
development-shadow only; no commercial archive search or weaker PIT proxy is authorized. See
`../reviews/stage-p032-level1-evidence-four-party-review-20260916.md` and
`../acceptance/stage-p032-level1-historical-realtime-evidence-feasibility-20260916.md`.

## Resource and stop boundary

One primary evidence pass plus at most one targeted gap pass. Stop no-pass if either venue cannot prove
exact originally disseminated version identity, <=15:30 dissemination or correction/replacement
completeness. Do not expand products, dates or source families after failure.

Any request to aggregate intraday data into an EOD bar, change the cutoff, treat private receipt as
PUBLIC availability, purchase/contact a source, add Level-2/tick/commercial feeds, integrate a source
or execute Stage/PIT is a material target change and requires a fresh four-party review first.
