# P3-STAGE-031 — Official-exchange historical EOD evidence feasibility

| Field | Value |
| --- | --- |
| Design status | approved bounded source-evidence experiment |
| Delivery gate | closed no-pass |
| Parent | `P3-STAGE-030` blocked closeout |
| Machine contract | `../../config/stage-learning/stage-official-exchange-eod-evidence-feasibility.v1.json` |
| Stage / strategy / trading authority | none |

## Objective

Evaluate one and only one historical source family: official market-data archives of the securities'
primary listing exchanges. SSE handles `600519`; SZSE handles `000001` and `300750`. Determine whether
that family can provide the source-version and public-availability evidence required to become a
candidate authority for a future strict `PUBLIC_RECONSTRUCTION` review.

This is not a PIT replay and not a source integration task.

## Frozen evidence target

Use the fixed F9 daily dependency envelope `2019-10-08 .. 2020-03-31` for the three securities. This
contains the daily-bar causal input scope for the exact P029 ten-observation population without
requiring Stage result access. Required daily fields are unadjusted/raw `open`, `high`, `low`, `close`,
`volume` and `amount`, plus security/date identity.

The relevant historical cutoff remains T=15:30 Asia/Shanghai.

## Evidence gates

A source candidate must establish all of the following:

1. authoritative exchange / officially authorized market-data product identity;
2. frozen security/date/field coverage;
3. exact historical value version identity rather than only today's historical value;
4. complete correction/revision lineage, or an independently justified immutability property that is
   strong enough to prove the archived value equals the originally disseminated value;
5. publication/dissemination evidence precise enough to decide the 15:30 cutoff;
6. stable audit retention / provenance binding and deterministic field mapping.

`Daily`, `end of trading day`, session close, current download time and today's corrected archive are
not sufficient by themselves.

## Bounded route

### P031-A — Freeze source family and evidence contract

One family only: primary listing-exchange official historical EOD archive. SSE and SZSE are
venue-specific authorities inside that family. Commercial aggregators are outside scope.

### P031-B — Official authority and publication audit

Use public exchange/official market-data documentation to establish product authority, field scope,
historical coverage and stated dissemination cadence. Search explicitly for correction/revision and
publication-time semantics rather than assuming them from exchange ownership.

### P031-C — Fixed coverage probe

Probe only the frozen securities/date/field envelope enough to determine whether official historical
records are queryable / orderable and whether returned records expose source-version or correction
identity. No bulk purchase, no Stage calculation and no outcome-based source selection.

### P031-D — Evidence matrix

For SSE and SZSE, classify each evidence dimension as `PASS`, `FAIL`, or `UNPROVEN`, recording the
official evidence and the precise claim ceiling.

### P031-E — Four-party closeout

One sealed matrix goes to short-term/event-driven, fund/risk, quant and data-governance reviewers.
Close either:

- `PASS_OFFICIAL_EXCHANGE_SOURCE_CANDIDATE_FEASIBILITY`, or
- `NO_PASS_OFFICIAL_EXCHANGE_EVIDENCE_INSUFFICIENT`.

P031 closed `NO_PASS_OFFICIAL_EXCHANGE_EVIDENCE_INSUFFICIENT`. The four-role closeout accepted the
no-pass 4/4 and authorized only a separately governed bounded official Level-1 intraday/realtime
historical evidence feasibility successor. No source integration or PIT replay is automatically
opened.

## Stop conditions

Close no-pass as soon as the single official-exchange family is fully classified and a required gate
remains unproven. Do not continue searching commercial feeds or historical web mirrors. If meeting the
evidence standard would require subscription purchase, private contract evidence, inferred publication
time, a new source family, altered cutoff/population, or actual source integration, stop and convene a
new four-party review before doing so.

## Non-goals

- no Stage10/11 predicate execution;
- no retrospective label comparison;
- no return/prediction analysis;
- no source ingestion or DWD rebuild;
- no 2022-2024 arbitration or 2025 blind access;
- no second source family;
- no automatic successor.
