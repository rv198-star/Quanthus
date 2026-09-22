# ODS/DWD Operations Incident Log

This is the append-only incident ledger for production ODS acquisition, daily catalog construction,
DWD build, and CN01-to-OCI synchronization. It is effective from `2026-09-11`.

Every failed scheduled or recovery run must receive an entry on the same calendar day in
`Asia/Shanghai`. Do not wait for a confirmed root cause: open the entry with
`status=pending_attribution`, preserve exact attempt/run paths and error codes, and append the
confirmed attribution when evidence is available. Never rewrite failed attempts or replace their
evidence with a successful retry.

Each entry must record:

- incident id, market date, node, unit, start/end time, and status;
- failed stage and user/data impact;
- direct cause, root cause, contributing factors, and explicitly excluded causes;
- immutable attempt, orchestrator, manifest, notification, and synchronization evidence;
- recovery action and result;
- permanent corrective action, code revision/PR, verification, and remaining follow-up.

## 2026-09-11 CN01 daily DWD missed build

| Field | Value |
| --- | --- |
| Incident id | `ODS-DWD-20260911-CN01-001` |
| Market date | `2026-09-11` |
| Node / unit | `CN01` / `cn01-ods-daily-orchestrator.service` |
| Window | `2026-09-11 18:30:01` to `20:46:18 Asia/Shanghai` |
| Status | `resolved` |
| Failed stage | `market-daily-core`; catalog and DWD remained fail-closed as `blocked_by_lane_failure` |
| Impact | The scheduled CN01 DWD package and OCI synchronization did not advance beyond `2026-09-10` until recovery completed. No partial DWD package was published. |

### Attribution

The first market acquisition attempt reached the fixed `19:46:00 Asia/Shanghai` completion deadline
after returning 1,804 of the required 4,992 securities. Baostock request latency averaged `2.512s`,
compared with `0.259s` on the prior session. The attempt reported
`provider_error_count=0`; the immediate failure was insufficient throughput within the governed
collection window, not an API error or a malformed response.

The retry wrapper then waited 600 seconds but relaunched the orchestrator without a new recovery
window. `market-daily-core` therefore reused the already-expired fixed deadline. That retry made
only the three readiness-index requests and no stock requests, so it could not recover regardless
of provider health. This stale-deadline retry behavior was the deterministic software root cause of
the terminal daily build failure. Elevated provider latency was the trigger and contributing
condition.

Explicitly excluded causes:

- calendar preflight failure: both exchanges were recorded open;
- provider request errors: the failed acquisition recorded zero provider errors;
- incomplete-lane publication: READY gating correctly prevented catalog and DWD construction;
- DWD transformation failure: DWD was never invoked by either scheduled attempt;
- OCI synchronization failure: OCI correctly waited for a same-date CN01 READY pointer.

### Evidence

- Failed orchestrator runs:
  `/root/work/astock/data/shadow/cn01-ods-v1/orchestrator/daily/2026-09-11/20260911T103001424601Z`
  and `20260911T124421481781Z` on CN01.
- Recovery attempt:
  `/root/work/astock/data/shadow/cn01-ods-v1/attempts/market-daily-core/2026-09-11/20260911-20260911T142116677208Z`.
- Recovery acquisition metrics: 4,995 requests, 4,992-security universe, `0.154s` average request
  duration, `7.859s` maximum, `774s` batch duration, zero empty responses, zero provider errors,
  and one bounded reconnect.
- Recovered DWD package: `dwd-daily-20260911-f536d90fb04d-f97f90c`, content digest
  `sha256:8bacada4c82a2a47dc057ef565ba480405d8247c7c353a5786cf423ba6282d60`.
- OCI receipt:
  `/home/ubuntu/data/quanthus/dwd-daily/receipts/2026-09-11/dwd-daily-20260911-f536d90fb04d-f97f90c.json`.

### Recovery and prevention

An immutable manual recovery gave `market-daily-core` an explicit future deadline. It completed all
4,992 securities at `22:34:14 Asia/Shanghai`. The orchestrator then reused all four verified READY
lanes, built the catalog and DWD package by `22:36:12`, and OCI atomically advanced its verified
`CURRENT.json` at `22:38:10`.

PR `#293` adds a retry-only `QUANTHUS_ODS_DAILY_RETRY_RECOVERY_SECONDS=14760` window measured from
retry start, passes it only to `market-daily-core`, records `market_recovery_deadline_at`, and raises
the service limit to 540 minutes. The first attempt's fixed schedule deadline and all full-universe
and READY fail-closed checks remain unchanged. PR `#294` aligns the runbook timeout. CN01 deployed
Git revision `c317fdb2ac81e34b5571b9e1abde524da1c5b05b`; schedule validation and 32 focused tests passed
on the node.

Remaining follow-up: Hermes must obtain the shared change through its node-local GitHub
fast-forward deployment. OCI has read-only Hermes authority and must not perform that deployment.

## 2026-09-18 CN01 and Hermes daily DWD failures

| Field | Value |
| --- | --- |
| Incident id | `ODS-DWD-20260918-DUAL-001` |
| Market date | `2026-09-18` |
| Nodes / units | `CN01` and `Hermes` / daily ODS-to-DWD orchestrators |
| Detection | Failure email from both nodes on `2026-09-18` |
| Status | `resolved` on both CN01 and Hermes |
| Failed stage | CN01: `calendar-daily-events`; Hermes: `market-daily-core` |
| Impact | CN01 did not publish the same-date DWD package, so OCI remained on its preceding verified package while waiting for a same-date READY source. Hermes also reported a DWD failure. No partial DWD package was accepted. |

### CN01 attribution and evidence

CN01's market, 1m, and 5m lanes all published verified READY releases. The first
`calendar-daily-events` attempt queried CNInfo for all 4,992 in-scope securities, but `000703` and
`000712` each exhausted three attempts with a 30-second read timeout. The orchestrator retry again
queried the full universe; this time `002284` and `002297` each exhausted three attempts with HTTP
502 responses. Both attempts therefore recorded
`cninfo acquisition incomplete: 2/4992 requests failed`, withheld the event watermark and READY,
and correctly blocked catalog and DWD publication.

- First orchestrator run:
  `/root/work/astock/data/shadow/cn01-ods-v1/orchestrator/daily/2026-09-18/20260918T103001443866Z`.
- Retry orchestrator run:
  `/root/work/astock/data/shadow/cn01-ods-v1/orchestrator/daily/2026-09-18/20260918T120459664223Z`.
- First event attempt:
  `/root/work/astock/data/shadow/cn01-ods-v1/attempts/calendar-daily-events/2026-09-18/20260918-20260918T103002449638Z`.
- Retry event attempt:
  `/root/work/astock/data/shadow/cn01-ods-v1/attempts/calendar-daily-events/2026-09-18/20260918-20260918T120500547014Z`.

The changing securities and error modes exclude a fixed unsupported security as the cause. The
direct cause was transient CNInfo transport failure. The software cause was that each security had
three immediate attempts, but the collector did not perform a delayed missing-set pass after the
full-universe scan. Two residual transient failures therefore invalidated an otherwise complete
event acquisition on both orchestrator attempts.

### Corrective action and CN01 recovery

Issue `#312` tracks the repair. The collector now performs one delayed, bounded pass over only the
residual failed security set. Its final acquisition log keeps exactly one row per security and
records the scan round, cumulative attempts, and prior-round error. Any residual failure still
withholds the event release, watermark, catalog, and DWD.

PR `#313` delivered the collector repair. Its first CN01 recovery event release was READY, but that
PR also changed the schedule contract revision and config digest. The daily catalog correctly
rejected combining that release with the three same-day releases sealed under the preceding
contract. No catalog or DWD was published. PR `#314` restored the exact preceding schedule bytes
and revision while retaining the bounded collector implementation; both schedule config SHA-256
values matched their pre-incident values before deployment.

CN01 deployed merge revision `36c06d69c8f33a0b40bb41b72aad483cecfbdbf1` through its node-local
GitHub fast-forward path. The compatible event recovery queried all 4,992 securities with zero
residual failures and published release `hermes-calendar-daily-events-20260918-8179844Z-v2`,
manifest `sha256:ebcc55936076cd1bc34ecee71b991a1960ea20548ff0f65849de2136efe35a25`.
The final orchestrator reused all four verified READY lanes, sealed catalog
`20260918T153258546555Z`, and completed DWD at `2026-09-18 23:33:06 Asia/Shanghai`.

OCI independently installed and verified package `dwd-daily-20260918-5d789c21aac8-36c06d6` at
`23:35:12 Asia/Shanghai`. Its manifest SHA-256 is
`sha256:c0599b680e89007f9818a59caf5db7e62a3d606fa9f51ddb980b6b0ee37b410c`, content digest is
`sha256:16c707e2dd643dc8ed9a8307eea9e6a08808f57e67475e357d47ade74dfdb39b`, and
`dwd_security_bar` contains 1,442,688 rows. The receipt is
`/home/ubuntu/data/quanthus/dwd-daily/receipts/2026-09-18/dwd-daily-20260918-5d789c21aac8-36c06d6.json`.
Both CN01 orchestration and OCI synchronization triggered their success notification units.

### Hermes attribution and recovery

Hermes had a different direct failure. Its event lane published READY successfully. The first
`market-daily-core` attempt returned daily bars for 3,912 of 4,992 securities, leaving 1,080
mandatory status observations missing; fail-closed validation withheld the market release. The
outer retry later received zero daily rows, so the market, status, and valuation datasets all
failed. The first 90-minute orchestration window also expired while the 1m and 5m lanes were still
running. A second orchestration completed both minute lanes but remained blocked by the market
failure. No incomplete catalog or DWD package was published.

Hermes was still on revision `9810d3a955232d25327e9a015d551fe801f109fd`, which lacked the
retry recovery-window fix and 540-minute controller bound delivered after the 2026-09-11
incident. Through the approved management channel, operators preserved the pre-deployment HEAD,
unit checksums, and timer state, stopped timers, fetched `origin/main` from GitHub with the
node-specific deploy key, and fast-forwarded the clean checkout to
`3657bb5d50d51d55eecad9291537d8644089fda8`. Both schedule contracts and 24 focused node-local
tests passed. Repository-rendered units replaced the installed units; the daily orchestrator now
has `TimeoutStartSec=540min` and a 14,760-second retry recovery window.

The explicit 2026-09-18 recovery reused the already verified event, 1m, and 5m releases and ran
only the missing market lane. It collected 4,992 daily bars, 4,992 status observations, and 4,992
valuation observations with no residual gap, publishing market release
`20260918-20260918T155233390804Z`; its `release.json` SHA-256 is
`fd3458411ab2dfd2c825bfbae115f05c7b66c050103c5fa450cb48fecc97bff1`. Catalog
`20260918T155231888498Z` contains four lanes and 32 artifacts with digest
`sha256:11ad4ed404017ef77f0ffd08ff06fdb35f332c475226984a11081d50d4ef2b52`.

Hermes published DWD package `dwd-daily-20260918-11ad4ed40401-3657bb5`, manifest
`sha256:9995bec3d04417cac5e661a51f57e00fd4976b29efe0242d32272fce5c131654`, and content digest
`sha256:51200ba732e390616bf6224ea604d6b77f37c50471e39a7d2c9da502fc0e96d4`.
`dwd_security_bar` contains 1,442,688 rows. The node-local DWD `CURRENT.json` points to market date
2026-09-18, and the success notification unit exited zero. Recovery evidence is retained at
`/root/work/astock/data/shadow/hermes-ods-v1/evidence/incident-312-hermes-20260918T2350CST`.

After recovery, operators started the timers before clearing the services' retained failed state.
Clearing that state released pending daily and weekly timer triggers at `2026-09-19 00:12
Asia/Shanghai`, outside both contract windows. The out-of-window services were stopped and exposed
no catalog or DWD package, but the orchestrator's expected `TERM` result invoked `OnFailure` and
sent a `2026-09-19` failure email at `00:15:20`. This was a recovery-operation false alert, not a
regression of the verified 2026-09-18 package. The services are inactive, all three timers are
active with their next contract-defined triggers, and the runbook now requires failed-state reset
while timers remain stopped.

## 2026-09-21 CN01/Hermes ODS failure and OCI sync cascade

| Field | Value |
| --- | --- |
| Incident id | `ODS-DWD-20260921-DUAL-001` / [#417](https://github.com/rv198-star/Quanthus/issues/417) |
| Market date | `2026-09-21` |
| Nodes / units | CN01 and Hermes daily ODS orchestrators; OCI DWD daily sync |
| Status | `resolved` on 2026-09-22, with independent CN01, Hermes, and OCI receipts in #417 |
| Failed stage | Both minute streams on both ODS nodes; Hermes first market attempt also had transient BaoStock errors |
| Impact | Both node-local DWD builds remained blocked by absent minute READY releases; OCI could not mirror a same-date CN01 DWD package. |

The fixed TDX endpoint was TCP-reachable but returned no usable minute-bar probe. The minute
collector used Eastmoney without adequate historical-date handling and pacing for the full 4,992
security universe. Remote closes accumulated until the fixed 1,800-second lane limit expired. The
after-hours recovery then exposed a separate date-binding defect: an unrelated minute-bar probe
rejected usable historical transaction data and the latest-day fallback could not satisfy the
requested 2026-09-21 date. DWD and OCI failed closed because the upstream same-date READY evidence
was absent; they were not independent transformation or sync defects.

PRs #418, #421, #422, and #423 addressed the provider fallback, transaction-specific probe, and
verified immutable seed recovery. CN01 recovery run `20260922T081348241628Z` and Hermes recovery run
`20260922T081348172026Z` each finished with four READY lanes, catalog READY, and DWD READY. OCI
installed CN01 package `dwd-daily-20260921-faf4478eb321-b02fb3c` at
`2026-09-22T08:25:40Z`; CN01/OCI content digest and manifest SHA-256 matched
`sha256:dfdf5c5781443da3b7c5099dee550141851652aeacf01cca753b7bed5215a9ae`.
The transient CN01 recovery unit did not inherit the standard success-mail hook, so the OCI email
alone was never used as proof of CN01 completion. Exact failed attempt paths, recovery manifests,
and the notification limitation are retained in #417; this entry was appended after recovery.

## 2026-09-22 CN01/Hermes daily ODS failure — recovery in progress

| Field | Value |
| --- | --- |
| Incident id | `ODS-DWD-20260922-DUAL-001` / [#432](https://github.com/rv198-star/Quanthus/issues/432) |
| Market date | `2026-09-22` |
| Nodes / units | CN01 and Hermes daily ODS orchestrators; CN01-to-OCI DWD mirror pending |
| Window | CN01 `18:30:01–19:40:12`; Hermes `19:30:18–21:35:19` Asia/Shanghai |
| Status | `pending_attribution` and `recovery_in_progress` at 2026-09-23 04:05 Asia/Shanghai; delayed entry after session interruption |
| Failed stage | Both minute streams on both nodes, exit 4 in each orchestrator run; Hermes market daily first run also failed but recovered in its second run |
| Impact | Both catalogs and DWD builds reported `blocked_by_lane_failure`. No 2026-09-22 CN01-origin DWD package was available for OCI verification. |

The directly observed minute failure on both nodes was `Eastmoney circuit opened after 20
consecutive symbol failures`. The upstream error class, exact provider availability, and the
relative contribution of the minute collector's probe/fallback behavior still require attribution.
Hermes runtime dependency drift tracked in #431 has not been established as this incident's cause.
The READY gate correctly withheld incomplete catalogs and DWD packages. OCI's own current state
could not be inspected at this checkpoint because direct SSH closed during handshake.

Preserved orchestrator run roots:

- CN01: `/root/work/astock/data/shadow/cn01-ods-v1/orchestrator/daily/2026-09-22/20260922T103001436219Z`
  and `20260922T113203053722Z`.
- Hermes: `/root/work/astock/data/shadow/hermes-ods-v1/orchestrator/daily/2026-09-22/20260922T113019117601Z`
  and `20260922T130558138878Z`.

One same-date CN01 5m recovery release was READY at
`/root/work/astock/data/shadow/cn01-ods-v1/releases/daily/5m/2026-09-22/20260922-20260922T192318338303Z`:
its attempt held 4,992 symbol files and the release manifest listed artifact checksums. CN01 1m
was still missing. That 5m collector's `status.json` says `server=sina-minute-jsonp`, while the
release acquisition log's collector command still names `mootdx/tdx.incr`; the checkout was dirty
and the attempt has no recorded code revision. Its checkpoint contains 4,969 securities from Sina
and 23 from `tencent-minute-query`; the temporary code constructs the latter's 5m rows by grouping
1m samples. ADR 0004 requires the formal 5m archive to be acquired directly, so this READY release
does not qualify for the formal daily catalog/DWD. Its provider and code lineage also need
reconciliation before any recovery promotion. Hermes 5m attempt
`/root/work/astock/data/shadow/hermes-ods-v1/attempts/intraday-archive/5m/2026-09-22/20260922-20260922T195645033988Z`
was still acquiring; Hermes 1m was missing. Other operations work had uncommitted provider changes
on both nodes, so no overlapping collector or checkout mutation was made during this audit.
The #432 gate requires verified four-lane, catalog, DWD, notification, and OCI date/package/hash
receipts before changing this status to `resolved`.
