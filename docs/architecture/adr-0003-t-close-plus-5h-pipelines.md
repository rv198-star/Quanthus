# ADR 0003: T-Close Plus Five-Hour Data Pipelines

- Status: Accepted
- Date: 2026-07-25
- Scope: Recurring Hermes acquisition and OCI daily warehouse processing

## Context

P1 established a trusted historical base, but most of its transformations were invoked manually.
The project now needs a repeatable post-close path that survives machine migration without moving
market data through Git. Intraday 1-minute and 5-minute intervention is deliberately deferred until
the live-trading phase. The approved decision contract remains a complete T-close signal whose
first execution is T+1.

## Decision

### 1. Processing window

The daily service starts at 15:10 Asia/Shanghai on a known open session. Its completion SLA is
20:00, five hours after the 15:00 close. The window is for acquisition, verification, DWD/DWS/PIT
work, gates, and publication; it does not mean that work waits until 20:00 to start.

```text
15:00       T close
15:10       OCI service starts and waits for the immutable Hermes release
15:10-20:00 pull, verify, normalize, gate, publish
20:00       daily DWD CURRENT must identify the accepted T partition
T+1         strategies may consume the T-close snapshot for execution
```

No current-session partial daily bar is admitted. Minute data, 10:00 intervention, and 14:15
intervention are outside `t-close-plus-5h-v1` and require a separate policy version.

### 2. Hermes has two acquisition lanes

| Lane | Purpose | Invocation | Output contract |
| --- | --- | --- | --- |
| Historical bootstrap/backfill | Establish or repair long history using the third-party a-stock-data project and targeted sources | Manual and issue-bound | Immutable `bootstrap` revision; never silently merged into a recurring release |
| Recurring acquisition | Collect new observations and bounded correction lookbacks | Daily and weekly timers on Hermes | Immutable `daily` or `weekly` ODS release with `release.json`, checksums, and `READY` |

Bootstrap code remains owned by the upstream acquisition project. Quanthus owns the release
contract, so either lane can hand immutable evidence to OCI without moving acquisition or source
credentials to OCI.

The daily v1 release requires:

- `daily_ohlcv_none_delta`: only rows for T, not another full-history copy;
- `daily_status_observation_delta`: T-day provider `tradestatus` and binary `isST` observations
  for every in-scope security, retained as raw evidence rather than interpreted on Hermes;
- `security_master`: the small complete current master used to rebuild point-in-time code validity;
- `stock_list`: the current acquisition universe; historical master rows alone must not resurrect
  inactive codes into T coverage;
- `trading_calendar`: complete SSE/SZSE evidence for T.

On a known closed weekday, Hermes publishes a calendar-only daily release. OCI verifies it, records
`skipped_closed_session`, and does not invoke build, gate, or publication. Missing calendar evidence
is an error and is never treated as a holiday.

The recurring acquisition lane may also emit daily index, valuation, calendar, suspension, and
status deltas. They become required only when their OCI transformations and acceptance gates are
versioned. Weekly releases carry bounded revisions for financial statements, corporate actions,
adjustment evidence, shareholder counts, and ST observations. Shareholder-count acquisition keeps
the approved disclosure-date watermark, at least a 90-day lookback, and revision history rather
than overwriting an earlier disclosure.

Hermes writes `READY` last. OCI nevertheless verifies the READY-to-manifest digest, every relative
path, size, SHA-256, source revision, and timezone-aware knowledge cutoff. A transferred directory
without a valid READY marker is not an input dataset.

### 3. OCI owns recurring transformation

The versioned pipeline has five ordered phases:

1. pull one exact Hermes daily release through the read-only rsync channel;
2. verify release integrity, the requested market date, and open/closed session evidence;
3. rebuild code validity and normalize T bars and raw status observations into an immutable DWD
   partition;
4. rerun checksums, row counts, duplicate keys, identity coverage, complete status coverage,
   tradable-bar coverage, universe scope, and OHLC gates;
5. atomically advance `/home/ubuntu/data/quanthus/revisions/daily_market_CURRENT.json`.

The accepted P1 DWD revision is the historical base. Daily partitions form an immutable chain on
top of that base and contain only T rows, avoiding a new 10+ GB full-history copy each day. A rerun
of T creates a new child revision and may replace the T pointer only after the same gates pass.
A pointer cannot move to an earlier market date.

Future DWS/PIT jobs use the same runner and add stages before publication. PostgreSQL publication
remains an optional final projection after issue #37; it is never needed to establish canonical
file state.

### 4. Failure and audit behavior

- A single exclusive lock prevents overlapping market-date runs.
- Each attempt writes a durable `run.json` and per-stage logs below
  `/home/ubuntu/data/quanthus/runs/`.
- A failed stage stops all later stages. In particular, a failed gate cannot invoke publication.
- `LAST_SUCCESS.json` changes only after every configured stage succeeds.
- A completed run after 20:00 is retained as `succeeded_late` and must alert; it is not relabeled as
  on time.
- Commands are executed as argument arrays without a shell. Secrets remain in the service
  environment or protected files and must not be command-line arguments.

## Repository and runtime boundaries

Git contains `src/quanthus/pipelines/`, `config/pipelines/`, `ops/bin/`, systemd unit templates,
tests, and this contract. ODS files, DWD partitions, manifests produced by runs, logs, database
state, SSH keys, and environment files stay outside Git.

The deploy root is the stable symlink `/opt/quanthus/current`. Migration installs the repository
and virtual environment there, restores the external data directories or pulls ODS again, installs
the environment file and units, and runs the doctor before enabling the timer.

## Consequences

The first automated version closes the most urgent daily path without prematurely designing the
intraday system. It also makes the collector/warehouse boundary executable rather than a verbal
convention. Weekly OCI PIT transforms and PostgreSQL projection remain separate increments; adding
them does not change the daily release or runner contracts.
