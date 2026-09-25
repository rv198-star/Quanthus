# ODS/DWD T+1 source-maturity recovery (2026-09-24)

## Decision and ownership

The owner permits date-bounded backfill and no longer requires T+0 publication.
The nightly CN01/Hermes orchestration still attempts T after the close, but an
otherwise READY three-lane run may defer the Tencent 1m lane when the source is
not yet exact. The recovery timer owns retries; the source's raw response and
the sealed daily reference, not the timer, own data qualification.

## Allowed deferral

The 1m lane may enter `deferred_source_maturity` only when the other three ODS
lanes are READY and either its bounded qualification fails or a **complete**
per-security scan has no failures except Tencent-versus-sealed-daily volume or
close mismatches. A partial scan, HTTP/transport failure, malformed receipt,
process cleanup failure, or any other lane failure remains a real failure.
No date, 240-minute grid, exact share-volume, close, catalog, or DWD gate is
relaxed; no synthetic minute amount is introduced.

## Retry and alert behavior

The maturity timer runs at 06:10, 08:10, 10:10, 12:10, 14:10, 16:10 and
17:40 Asia/Shanghai each day. A missed firing catches up after node restart,
but the worker selects only prior market dates, so a T-evening enablement
cannot prematurely retry the T source. Each attempt selects an existing
deferred market date from node-local run receipts; it never changes the data's
trading date. An expected pending
source stays quiet. If still pending at the following calendar day's 17:40
CST cutoff, one date-specific failure notification records the T+1 miss, but
the backfill queue remains active. The cutoff is configurable with
`--tplus1-cutoff HH:MM`; a later successful date-correct package sends the
separate success notification.

If the new code is deployed after an evening run, the worker can reclassify
that run's failed receipt into the same deferred queue only when its complete
node/date-local checkpoint satisfies the exact source-mismatch rule. This
avoids rewriting or relabeling the immutable prior run.
If ODS reaches READY but DWD fails, the same date remains eligible for a later
DWD retry. A latest `ready` DWD run is treated as complete even if the worker
never wrote its private completion marker (for example, an authorized manual
recovery); it must not be rebuilt just to synthesize that marker.

Publication still requires four READY ODS lanes and the existing catalog and
DWD validations. OCI only mirrors the qualified CN01 package and must match
its manifest and content hashes. The paid Tushare-style relay remains a
separate draft/quarantined source evaluation, not an implicit fallback.

## Rollout evidence

Before activating the new timer, verify both nightly services are inactive,
render and syntax-check CN01/Hermes units, then deploy the same reviewed Git
revision on both nodes. Verify each node's next timer elapse, one pending run
without false success/failure notification, the complete-scan mismatch path,
and eventual date-correct ODS/DWD/OCI receipts. Preserve the 9/23 raw evidence
and issue #436's NULL minute amount quality debt.
