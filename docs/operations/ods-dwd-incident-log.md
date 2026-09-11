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
