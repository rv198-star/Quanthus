# ADR 0004: Hermes ODS Scheduler Version 1

- Status: Accepted for shadow deployment
- Date: 2026-07-25
- Scope: Recurring acquisition and reconciliation on Hermes
- Contract: `config/schedules/hermes-ods-schedule.v1.json`
- Migration tracking: GitHub issue #41

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

| Lane | Trigger (Asia/Shanghai) | Responsibility | Publication |
| --- | --- | --- | --- |
| `bootstrap` | Manual, issue-bound | Initialization and historical backfill | Immutable raw revision; never recurring |
| `market-daily-core` | Mon-Fri 15:10 | Daily bars, binary status observation, market references and index/adjustment deltas | Daily ODS; T+5H deadline 20:00 |
| `calendar-daily-events` | Every day 19:30 | Announcements and disclosure-driven events | Natural-day ODS; never creates a market session |
| `intraday-archive` | 1m Mon-Fri 20:30; 5m Mon-Fri 23:00 | Preserve direct minute archives while sources retain them | Independent streams outside the T+5H path |
| `weekly-corrections` | Saturday 08:00 | Low-frequency primary acquisition and bounded reconciliation | Independent correction revision |

### Market session preflight

The market lane is timer-driven on weekdays, then independently checks SSE and SZSE for the
target date before calling market-data providers.

- Both open: acquire and publish the complete market release.
- Both closed: publish only the calendar evidence with terminal state `closed_session`.
- Missing or disagreeing records: fail, alert, make no provider calls, and publish no `READY`.

Weekend market timers do not exist. Event acquisition still runs every natural day because an
announcement can be disclosed on a weekend or holiday; it cannot convert that date into a
tradable session.

### Minute archives

Both 1m and 5m are acquired directly. Formal 5m evidence is never synthesized from 1m. The two
streams have separate timers, locks, checkpoints, logs, attempts, manifests, and readiness
markers. 1m runs first because its upstream history is retained for only about three to four
months. Minute consumers and intraday intervention remain deferred, but acquisition does not.

Each minute timer applies the same SSE/SZSE three-state preflight as the market daily lane. Both
open permits direct acquisition; both closed publishes calendar-only evidence; missing or
disagreeing evidence fails before any provider call or READY marker.

Minute work starts after the T+5H market deadline and does not block daily DWD publication. The
legacy combined 16:00 minute job remains active during shadowing; it is disabled only after the
replacement proves continuous coverage through a controlled cutover.

### Weekly corrections

Weekly primary acquisition covers shareholder counts, quarterly financials, corporate actions,
dividends, adjustment evidence, detailed ST announcement history, security lifecycle, and
industry membership. Daily-data reconciliation is bounded: 20 sessions for none bars and index
bars, 90 days for status, and a full diff for the calendar and security master.

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

Hermes publishes immutable ODS only. It does not advance OCI `CURRENT`, build DWD/DWS/ADS, or
write curated PostgreSQL schemas. A soft failure in one dataset cannot be hidden by another
dataset's success or by a wrapper exiting zero.

## Resource policy

The market daily core receives priority until its 20:00 deadline. Event calls use a separate API
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
