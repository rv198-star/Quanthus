# ADR 0004: Hermes ODS Scheduler Version 1

- Status: Accepted for the current ODS schedule; candidate-build boundary amended by ADR 0005
- Date: 2026-07-25
- Last reconciled: 2026-07-31
- Scope: Recurring acquisition and reconciliation on Hermes and CN01 ODS nodes
- Contract: `config/schedules/hermes-ods-schedule.v1.json`
- Migration tracking: GitHub issue #41

ADR 0005 retains this acquisition schedule and immutable ODS contract but permits CN01 and Hermes
to build complete DWD/DWS candidate families after acquisition. Lane candidates have no canonical,
ADS, PostgreSQL, or consumer-publication authority. The ODS-only statement below remains the live
boundary until ADR 0005's explicit cutover and is then superseded only for candidate production.

## Context

Hermes currently runs a third-party weekly bootstrap-oriented script, a combined minute job,
and several completion watchdogs. The weekly job mixes fresh acquisition, historical repair,
reference builds, cleansing, and PostgreSQL synchronization. It takes many hours and contains
soft-failing stages, so a top-level exit code cannot establish dataset completeness. New daily
jobs also overlap parts of the weekly script without an explicit reconciliation contract.

Hermes is hosted on the resource-constrained PVE node with PostgreSQL. ADR 0002 assigns raw
acquisition and immutable ODS evidence to Hermes, while OCI owns normalization, PIT joins,
quality gates, DWD/DWS/ADS publication, and curated PostgreSQL projections. The recurring
schedule must enforce that boundary while preserving short-retention 1m data.

## Decision

Hermes has five lanes. Their definitions, timers, datasets, locks, and publication invariants
are versioned in the machine-validated schedule contract.

`schedule_id=hermes-ods-schedule-v1` identifies the stable five-lane topology. Material contract
edits within that topology carry a separate `contract_revision`; every attempt and READY release
records that revision and the exact schedule-file SHA-256. This preserves replay lineage without
misrepresenting the 2026 final-trading-time update as a new lane topology.

| Lane | Trigger (Asia/Shanghai) | Responsibility | Publication |
| --- | --- | --- | --- |
| `bootstrap` | Manual, issue-bound | Initialization and historical backfill | Immutable raw revision; never recurring |
| `market-daily-core` | Hermes Mon-Fri 17:30 first-ready; CN01 Mon-Fri 18:30 throughput-budgeted window | Final daily bars after the 15:30 trading boundary, binary status observation, market references and index/adjustment deltas | Daily ODS; READY by 19:46, then OCI completion by 20:30 |
| `calendar-daily-events` | Hermes every day 19:30; CN01 starts from the 18:30 daily orchestrator | Announcements and disclosure-driven events | Natural-day ODS; never creates a market session |
| `intraday-archive` | 1m Mon-Fri 20:30; 5m Mon-Fri 23:00 | Preserve direct minute archives while sources retain them | Independent streams outside the T+5H path |
| `weekly-corrections` | Saturday 08:00 | Low-frequency primary acquisition and bounded reconciliation | Independent correction revision |

### Calendar refresh and market session preflight

The market lane is timer-driven on weekdays. In provider mode its first operation is a bounded
upstream calendar refresh through the repository-owned `quanthus.providers.trading_calendar`
adapter. The target date must be present in that invocation's response; a row found only in the
old Base calendar is not fresh evidence. The adapter merges the response with the historical Base
into an attempt-local candidate, records its source, request window, checksum, and target state,
then checks the candidate's SSE and SZSE rows before calling market-data providers.

The v1 locked upstream, `baostock.query_trade_dates`, exposes one shared A-share calendar. The
adapter mirrors that evidence to the exchange keys expected by the existing contract and labels
the evidence model honestly; it does not claim that the two rows are independent upstream
observations. A future source upgrade requires a new contract revision.

- Both open: acquire and publish the complete market release.
- Both closed: publish only the calendar evidence with terminal state `closed_session`.
- Missing or disagreeing records: fail, alert, make no provider calls, and publish no `READY`.

Refresh failure is also terminal and occurs before all market-provider calls. The candidate never
overwrites the Base calendar or a prior attempt. Reruns create a new attempt and fetch the target
date again. Explicit forced-preflight paths remain test-only and are recorded as a bypass.

### Provider readiness and collection-window experiment

Open-session preflight does not prove that BaoStock has published final daily rows. The market lane
therefore polls the target date for `sh.000001`, `sh.000300`, and `sz.399001` before scanning the
stock universe. All three must be present in one poll. Empty successful responses never trigger
connection churn; only provider/network errors receive bounded reconnect retries. BaoStock's
process-global socket receives a 30-second timeout after every login. The stock scan performs one
initial pass and one bounded retry of only the missing set, then fails closed on any residual gap.

Contract revision `hermes-full-ods-scope-cn01-throughput-20260731` starts the v3 five-open-session
A/B and retains mandatory daily valuation, complete weekly fundamentals, and sparse after-hours
acquisition. Hermes starts at 17:30 and scans at first provider readiness; CN01 starts at 18:30.
The earlier CN01 window follows the 2026-07-31 v2 attempt: 4,342 requests completed in 2,748 seconds
at 0.633 seconds per request, leaving 653 securities at the 19:46 deadline. Its 80-minute systemd
timeout now exceeds the observed roughly 53-minute full-scan duration while the acquisition's
authoritative 19:46 deadline remains unchanged. Earlier samples remain evidence and are not mixed
into the v3 sample. Both attempts retain the same source, universe, quality gates, and immutable
release semantics. Their acquisition logs record the comparison metrics named by the contract.
The experiment may change a future timer only through another contract revision after the minimum
sample is present.

Each node also has a five-minute activation grace declared in the contract and enforced by the
systemd service before Python acquisition starts. Restarting a calendar timer after its same-day
window may make systemd present the elapsed event again even with `Persistent=false`; that event is
condition-skipped, creates no attempt, and cannot contaminate the A/B sample. Operators use the
launcher directly for an explicitly reviewed manual rerun rather than bypassing the service gate.

Weekend market timers do not exist. Event acquisition still runs every natural day because an
announcement can be disclosed on a weekend or holiday; it cannot convert that date into a
tradable session.

### Minute archives

Both 1m and 5m are acquired directly. Formal 5m evidence is never synthesized from 1m. The two
streams have separate timers, locks, checkpoints, logs, attempts, manifests, and readiness
markers. 1m runs first because its upstream history is retained for only about three to four
months. These provider bars cover the continuous-trading and closing-call-auction grid through
15:00 only. `auction_session_complete` therefore does not imply coverage of sparse after-hours
fixed-price matches from 15:05 through 15:30. The 1m attempt therefore also acquires the separate
`after_hours_fixed_price_trades.v1` sparse dataset and a receipt for every requested A-share and ETF.
The trades may be globally empty only when every request completed successfully and carries an
explicit justified-empty receipt. The 5m attempt does not repeat this acquisition. Minute consumers
and intraday intervention remain deferred, but acquisition does not.

Each minute timer first performs the same fresh attempt-local calendar refresh and then applies
the same SSE/SZSE three-state preflight as the market daily lane. Both open permits direct
acquisition; both closed publishes calendar-only evidence; refresh failure or missing/disagreeing
evidence fails before any minute-provider call or READY marker.

Minute work starts after the ready-gated daily market deadline and does not block daily DWD publication. The
legacy combined 16:00 minute job remains active during shadowing; it is disabled only after the
replacement proves continuous coverage through a controlled cutover.

CN01 uses `hermes-ods-daily-orchestrator.{service,timer}` as its daily control plane. The
orchestrator starts the market and event lanes together, then starts both minute streams with
retry-on-lock-busy behavior for the shared `pve_market_api` group. It seals the four-lane daily
catalog and invokes the CN01 DWD input handoff only after every lane is `READY`. The controller's
one-hour SLA is an operational deadline; it does not alter the lane's 19:46 provider completion
deadline or the immutable release contract. The CN01 event snapshot begins at 18:30 and relies on
the existing seven-day lookback to capture later disclosures on a subsequent natural-day run.

### Weekly corrections

Weekly primary acquisition covers shareholder counts, raw TDX F10 text, complete balance-sheet,
income-statement and cash-flow provider responses, a quarterly projection derived from the complete
income statements, real BaoStock industry membership, corporate actions, dividends, adjustment
evidence, detailed ST announcement history, and security lifecycle. F10 and statement collectors
checkpoint bounded security batches and stream the completed parts into Parquet so a retry neither
restarts the whole market nor loads the full archive into memory. Every requested security/source
has status `success_nonempty`, `justified_empty`, or `failed`; any failed receipt blocks READY.
Daily-data reconciliation is bounded: 20 sessions for none bars and index bars, 90 days for status,
and a full diff for the calendar and security master.

Every compared key is classified as `same`, `missing_in_daily`, `changed_upstream`, or
`conflict`. Weekly output is a new correction revision and never overwrites the daily release.
OCI decides whether to merge it into a new DWD child after independent checks.

### Publication state machine

Successful releases advance through:

`scheduled -> preflight -> acquiring -> sealed -> ready`

`closed_session`, `failed`, and `partial` are explicit terminal alternatives. `partial` is not
success. Each dataset reports its own status, count, source request/revision, watermark,
knowledge cutoff, and checksum. `READY` is written last after all mandatory artifacts and the
manifest are sealed. A release id cannot be reused with changed lineage; reruns create a new
attempt or revision.

Only a successful `market-daily-core` release may atomically advance the date-local
`releases/daily/<market-date>/CURRENT.json` used for DWD/DWS candidate selection. Other daily
lanes, including `calendar-daily-events`, publish complete immutable releases in the same schedule
tree but never create or replace that market-data selection pointer.

Hermes publishes immutable ODS only. It does not advance OCI `CURRENT`, build DWD/DWS/ADS, or
write curated PostgreSQL schemas. A soft failure in one dataset cannot be hidden by another
dataset's success or by a wrapper exiting zero.

## Resource policy

The market daily core receives priority until its 19:46 ODS-ready deadline. Event calls use a separate API
resource group. Minute jobs use the market API group after the core deadline. Weekly bulk I/O
runs Saturday morning, after Friday minute acquisition. Locks prevent jobs in one constrained
resource group from competing for PVE CPU, disk, or provider quotas.

## Cutover

The new lanes first run in shadow mode beside the old cron. OCI compares release coverage,
watermarks, checksums, state transitions, and failure behavior. No existing job is removed until
OCI posts `GO_FOR_CUTOVER` on #41. Cutover changes one lane at a time and keeps the old command
available, disabled, for rollback. Details are in the scheduler runbook.

## Consequences

Freshness and historical correction become separate, observable responsibilities. Hermes load
is bounded and failures become attributable to a dataset and attempt. OCI can replay every build
from immutable ODS evidence and may later move PostgreSQL without redesigning acquisition.

The cost is more scheduler units, manifests, locks, and alerts. The five-lane contract and local
validator make that operational complexity explicit and migration-safe.
