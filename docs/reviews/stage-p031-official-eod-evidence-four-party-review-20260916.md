# P3-STAGE-031 official-exchange EOD evidence feasibility — four-party review

| Field | Value |
| --- | --- |
| Date | 2026-09-16 |
| Work item | `P3-STAGE-031` |
| Frozen result | `NO_PASS_OFFICIAL_EXCHANGE_EVIDENCE_INSUFFICIENT` |
| Machine evidence | `../../config/stage-learning/stage-official-exchange-eod-evidence-feasibility-result.v1.json` |
| Review mode | four independent GPT-6 Astra read-only role sessions; no tools/files inside sessions |
| Decision | 4/4 accept P031 no-pass; open one separately governed bounded Level-1 intraday/realtime evidence feasibility route |

## Frozen evidence reviewed

P031 evaluated exactly one source family: the primary listing-exchange official historical EOD archive
family, with SSE/CIIS for `600519` and SZSE/SSIC for `000001` and `300750`. The frozen dependency
envelope was `2019-10-08 .. 2020-03-31`; the historical decision cutoff stayed T=15:30
Asia/Shanghai. No Stage predicate, economic outcome, source integration, commercial source, protected
2022-2024 arbitration or 2025 blind data was accessed.

The EOD source family passed source-authority checks and showed substantial field/history coverage,
but it did not satisfy the frozen historical-version/public-availability contract:

- SSE/CIIS describes the historical products as official SSE products and the EOD product contains
  code/name/open/close/high/low/volume/amount with history back to 1990. The historical-data product
  family launched in September 2020; the launch notice says historical data is cleaned and published
  by SSE. Current EOD archive delivery is monthly and the cloud historical-data service updates on
  trading days before 21:00. Public documentation did not prove that today's retained value is the
  exact original 2020 dissemination version or expose a complete initial/correction/revision family.
- SZSE documents SSIC as the exclusive market-data manager/distributor; Level-1 contains the required
  price/volume/value family and EOD is provided at the end of the trading day. Official historical
  quote lookup exists. The bounded public evidence did not bind an exact historical value version,
  complete correction family, or publication semantics precise enough to prove that exact version was
  public by 15:30.

The adjacent discovery — official SSE Level-1/Level-2 historical intraday snapshot/tick products and
SZSE contemporaneous Level-1 real-time dissemination — was explicitly held out of P031 because it is a
different product and temporal lineage model.

## Short-term / event-driven review

The reviewer accepted NO_PASS. Official ownership, `historical`, `cleaned`, and `end-of-day` labels do
not prove strict point-in-time availability. The useful adjacent route is a small official Level-1
intraday/realtime evidence check, not a reinterpretation of P031.

The recommended first scope excludes Level-2/tick and asks only whether exact historical records can
be bound to an official dissemination event, version/correction lineage and auditable retention before
the frozen cutoff.

## Fund / portfolio-risk review

The reviewer accepted NO_PASS because using a later cleaned/corrected history could understate the
uncertainty present in the real historical risk meeting. The reviewer selected the same bounded
Level-1 intraday/realtime evidence route and rejected any use of EOD/session-close as a substitute for
version-specific publication evidence.

Progress on the adjacent route must not change P031, portfolio limits or any Stage authority.

## Quant research review

The reviewer accepted NO_PASS as a revision-type look-ahead-control failure, not as evidence that the
market data or Stage concept is invalid. The adjacent experiment was approved only with a frozen small
probe:

- product count: **2** — one SSE Level-1 intraday snapshot historical product/evidence chain and one
  SZSE Level-1 real-time dissemination + official historical-retention evidence chain;
- dates: **3** — `2019-10-08`, `2020-01-02`, `2020-03-31`;
- securities: the frozen venue-specific members (`600519` for SSE; `000001`, `300750` for SZSE);
- no result-aware replacement of products, dates or securities.

The minimum evidence unit is product × security × trading date × exact message/snapshot version. It
must bind source/schema identity, original payload or immutable batch identity, sequence/version,
contemporaneous official dissemination evidence, correction/replacement completeness or equivalent
immutability, and auditable custody. A modern hash of a reconstructed file is not historical identity.

## Data-governance / warehouse review

The reviewer accepted NO_PASS and emphasized four distinct clocks: market event time, generation time,
dissemination time and receipt/archive time. The adjacent route may proceed only if these are not
conflated.

The minimum source contract must bind historical product/schema applicability, raw field semantics,
message/session identity, correction/retransmission lineage, official publication/dissemination
semantics and retainable raw audit evidence. One product family per venue is the maximum scope.

## Consensus next route

All four reviews selected the same successor:

> a new, separately governed **official Level-1 intraday/realtime historical evidence feasibility**
> phase, with no Stage execution or source integration.

The narrow common scope is:

1. SSE: official Level-1 intraday snapshot historical product and the contemporaneous Level-1
   dissemination semantics relevant to the target period;
2. SZSE: official Level-1 real-time dissemination and any official historical-retention evidence for
   the same product/schema family;
3. fixed probe dates: `2019-10-08`, `2020-01-02`, `2020-03-31`;
4. fixed venue securities: `600519`; `000001`, `300750`;
5. one primary evidence pass plus at most one targeted gap pass; then stop.

Level-2, tick-by-tick, commercial feeds, provider contact/purchase, bulk download, Stage execution and
source integration are outside the successor scope.

## Success / failure boundary for the successor

A feasibility PASS requires both venue products to establish an auditable path from exact historical
message/snapshot identity to official dissemination at or before the cutoff, plus correction/revision
completeness or an equivalent immutable-original property. Fixed-date probes demonstrate the evidence
mechanism only; a product-level policy/archive guarantee must also cover the full frozen historical
window before the source can become a candidate.

The successor closes no-pass if either venue can prove only that real-time feeds existed, but cannot
bind today's historical retention to the exact originally disseminated records; if timestamps are only
market-event/generation times; if later cleaning/reconstruction obscures original versions; or if
closing the gap would require purchase, provider contact, a broader product family or actual data
integration.

## Mandatory re-review triggers

Stop and reconvene all four roles before:

- adding Level-2/tick or a commercial/second source family;
- changing product, date, security, field, T=15:30 or the public-availability standard;
- deriving official EOD by a new intraday aggregation semantics rather than checking source evidence;
- treating licensed/private receipt as equivalent to PUBLIC availability without an explicit decision;
- accepting current cleaned/rebuilt history without original-version lineage;
- purchasing/subscribing, contacting providers, bulk downloading, integrating a source, or executing
  Stage/PIT predicates.

## Claim ceiling

P031 establishes only that the preregistered official historical EOD archive family did not satisfy
the strict source-version/public-availability contract using bounded public official evidence. The
four-party decision justifies one final, narrower official Level-1 intraday/realtime evidence
feasibility experiment. It does not establish that this adjacent route will pass or that Stage10/11
can be reconstructed point-in-time.
