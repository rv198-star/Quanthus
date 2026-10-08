# P10 1m source maturity — next trading day (2026-10-08)

| Control | Status |
| --- | --- |
| Authority | Owner 2026-10-08, P10; supersedes natural-calendar-day T+1 cutoff |
| Dependencies | #424 / #440 / #456 / #466, upstream A Stock Data #57 |
| Implementation | Isolated engineering candidate, not production |
| Daily and recovery timers | Remain disabled; this change does not activate them |

## Approved target

T represents the original exchange market date; PIT source identity, daily/5m/event evidence, and publication date do not shift. The T close run may seal independent market, 5m, and event lanes. With the explicit next-trading-day policy it must **not request 1m on T**. The fourth lane and catalog/DWD stay deferred even if the other three lanes are READY.

The earliest T+1 1m test is 10:10 Asia/Shanghai on the next verified exchange trading day. This is only a probe time, not a claim that the upstream minute figures are mature. The existing exact share totals, real 240-minute source grid, close check, raw SHA, sealed-reference and whole-security arbitration remain mandatory.

The 17:40 late deadline is on the next verified trading session, never one natural day after T. The backfill queue remains active after late notification; no missing minutes, residual shares, amount or OHLC fields may be fabricated.

The next trading date is sourced from bounded BaoStock query_trade_dates evidence (45 calendar days), requiring a continuous start-to-next-open sequence and source SHA. A missing or invalid calendar fails closed before any new 1m request or late alert. 2026-09-24 T+1 is 2026-09-28 (Mid-Autumn closure); 2026-09-30 T+1 is 2026-10-08 (National Day closure). Exchange reference: https://www.sse.com.cn/disclosure/announcement/general/c/c_20260915_10832273.shtml

## Controlled activation

Both run-cn01-ods-daily and recover-matured-ods-daily accept an explicit CLI option:

    --minute-maturity-policy next-trading-day

or equivalent environment variable:

    QUANTHUS_ODS_MINUTE_MATURITY_POLICY=next-trading-day

Until an authorized reviewed deployment and node/unit hash receipts, the legacy default remains same-day-probe. Do not restore CN01/Hermes timers automatically; do not overwrite existing artifacts or send success notifications on deferral.

## 2026-09-24 targeted recovery results

The 2026-10-08 paid relay issued 24 bounded historical requests; all returned HTTP 200 and immutable raw receipts. Against the same sealed daily/status/native 5m inputs, four now pass the full exact whole-security qualification: 000404, 000755, 300501, 301502. Twenty fail volume or first-bucket/source interpretation. This is quarantine-only qualification and not data publication.

Inputs SHA-256:
- daily: 8de54259119bf8a7590f935271d6d2c28cabe72d23979bb5f3c8c7d975ae0136
- status: 096c9b346bf77705587b28cd416dd3ba201a334005703010dc528327eb437a9d
- native 5m: eb295b8891f7e727461860e14d50e60fc569e636caa58da981096b28fbacde35

Source-local receipt: /root/work/astock/data/quarantine/paid-1m/2026-09-24-tplus1-20261008-strict-revalidation.json

Previous 4,992-universe exact ledger: 2,296 unconflicted candidates (including seven suspended); 2,672 double-qualified source semantic conflicts; 24 hard gaps. These new four remove four potential hard gaps but do not resolve the 2,672 conflicts and do not authorize catalog, DWD, notification, or OCI sync. A formal whole-population requalification remains required before any publication.

## Release gates

1. Review and test market-calendar outages, weekends/holidays, timestamp windows, the no-T0 1m promise, and durable deferral receipts.
2. Qualify each of the 24 new immutable raw responses under the same exact policy and physical identity; no inferred row, amount, share top-up or unreviewed source precedence.
3. Prove coverage of the remaining 20 hard gaps and separately resolve the 2,672 source conflicts, or formally classify the market-date product unavailable. A small price tolerance is not a truth-ownership policy.
4. Only after all four ODS lanes satisfy the same date/contract, independently verify CN01 and Hermes published DWD and the OCI manifest/content hash. Owner approval to restore collection timers is a separate decision.

No new subscriptions, credential disclosure, rate-limit workarounds or change to P6 and PIT policy.
