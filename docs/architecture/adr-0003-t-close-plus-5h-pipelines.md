# ADR 0003: T-Close Plus Five-Hour Data Pipelines

- Status: Accepted
- Date: 2026-07-25
- Last reconciled: 2026-07-28
- Scope: Recurring Hermes acquisition and OCI daily warehouse processing

## Context

P1 established a trusted historical base, but most of its transformations were invoked manually.
The project now needs a repeatable post-close path that survives machine migration without moving
market data through Git. Intraday 1-minute and 5-minute intervention is deliberately deferred until
the live-trading phase, but source acquisition continues because short vendor retention windows
may make later recovery impossible. The approved decision contract remains a complete T-close
signal whose first execution is T+1.

## Decision

### 1. Processing window

From 2026-07-06, all A-shares and ETFs may trade in the after-hours fixed-price phase from
15:05 through 15:30. Those matches are included in final daily volume and amount only after
15:30. Hermes and CN01 therefore start final daily acquisition at 15:40 Asia/Shanghai. OCI starts
only a readiness gate at that time: it does not pull or clean until an upstream has published a
same-date immutable handoff. The slowest accepted acquisition took 52m18.4s; adding 100% and
rounding up yields `ceil(3138.4 * 2) = 6277s`, so ODS must be ready by 17:25.

```text
15:00       closing call auction completes
15:05-15:30 after-hours fixed-price matching
15:30       final daily trading boundary; final volume/amount may now settle
15:40       Hermes and CN01 acquisition; OCI starts readiness polling only
15:40-17:25 OCI polls both immutable handoffs every 30 seconds
on READY     first observed source wins; same-poll tie resolves to CN01
17:25       latest acceptable upstream ODS handoff (52m18.4s observed + 100%)
17:26       CN01-primary DWD/DWS completion SLA
T+1         strategies may consume the T-close snapshot for execution
```

No pre-15:30 partial daily bar is admitted. Minute data, 10:00 intervention, and 14:15
intervention are outside `t-close-plus-5h-v1` and require a separate policy version.

The versioned `config/trading-session.v1.json` also records the other material 2026 rule effects
without prematurely adding them to the current strategy path: exchange-specific order-acceptance
starts for the after-hours phase, exclusion when the security is suspended at 15:00, the 500,000
share risk-warning daily buy cap across auction/block/after-hours channels, SSE fund closing call
auction, and the deferred SSE 16:00-17:00 block-trade extension. Execution code must consume these
rules before it is allowed to place after-hours or risk-warning orders.

The market timer fires Monday through Friday, including exchange holidays. Before any market or
intraday request, Hermes fetches a bounded fresh calendar window, requires the target date in that
response, builds an attempt-local candidate, and resolves the target date from its complete SSE
and SZSE calendar evidence:

- both exchanges open: run the market acquisition;
- both exchanges closed: skip market and intraday requests and publish a calendar-only daily
  release so OCI records `skipped_closed_session`;
- either exchange missing or the two states disagree: fail and alert; absence of bars is never
  interpreted as a holiday.

Refresh failure has the same fail-closed outcome. The historical Base calendar is an input to the
attempt-local merge, not proof that the target date was freshly observed, and is never overwritten
by the recurring attempt.

Weekends have no market timer. Calendar-day announcement collectors may run independently because
disclosures can arrive on non-trading days; they do not make a closed day into a market session.

### 2. Hermes has three acquisition lanes

| Lane | Purpose | Invocation | Output contract |
| --- | --- | --- | --- |
| Historical bootstrap/backfill | Establish or repair long history using the third-party a-stock-data project and targeted sources | Manual and issue-bound | Immutable `bootstrap` revision; never silently merged into a recurring release |
| Recurring acquisition | Collect new observations and bounded correction lookbacks | Daily and weekly timers on Hermes | Immutable `daily` or `weekly` ODS release with `release.json`, checksums, and `READY` |
| Intraday archive acquisition | Preserve direct 1-minute and 5-minute bars before their source windows expire | Post-close timers outside the T+5h critical path | Immutable raw daily partitions; not a T+5h feature or decision input |

Bootstrap code remains owned by the upstream acquisition project. Quanthus owns the release
contract, so either lane can hand immutable evidence to OCI without moving acquisition or source
credentials to OCI.

The intraday archive prioritizes direct 1-minute acquisition because it has the shortest recovery
window, then collects the direct 5-minute track. The formal 5-minute series is never synthesized
from the 1-minute archive. Intraday jobs use independent locks, logs, checkpoints, and status;
their failure cannot block or falsely pass the daily T+5h release. Existing intraday data and
scripts remain outside Git, and no intraday feature enters a snapshot until a later versioned
policy and acceptance gate authorize consumption. Current TDX/mootdx 1m and 5m artifacts cover
only the continuous-trading and closing-call-auction grid ending at 15:00. Their completeness flag
does not claim after-hours coverage. Sparse 15:05-15:30 matches require the separate versioned
`after_hours_fixed_price_trades.v1` dataset contract; absent trades must never be expanded into a
dense 1m/5m grid. Intraday jobs consume the same proven open-day decision as the daily market
release and do not independently guess from an empty provider result.

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

Hermes writes `READY` last and then atomically writes same-date `CURRENT.json` outside the immutable
attempt directory. CN01's direct source layout is attempt-addressable, so OCI discovers its latest
same-date `READY` attempt through rsync. OCI polls both sources every 30 seconds and records the
first observed candidate in `ods-source-selection.json`; a tie in the same poll resolves to CN01.
The selected source and attempt cannot change during the run. OCI nevertheless verifies the
READY-to-manifest digest, every relative path, size, SHA-256, source revision, and timezone-aware
knowledge cutoff. A transferred directory without a valid READY marker is not an input dataset.

### 3. OCI owns recurring transformation

The versioned pipeline has six ordered phases:

1. poll the CN01-primary and Hermes-fallback ODS handoffs and atomically record one winner;
2. pull only the exact immutable attempt named by that selection;
3. verify release integrity, the requested market date, and open/closed session evidence;
4. rebuild code validity, normalize T bars and raw status observations into an immutable DWD
   partition, and derive one lineage-bound DWS market summary from that exact DWD revision;
5. rerun DWD checksums, row counts, duplicate keys, identity coverage, complete status coverage,
   tradable-bar coverage, universe scope, and OHLC gates, then validate DWS checksums, lineage, and
   aggregate metric invariants;
6. advance the individually atomic DWD and DWS pointers at
   `/home/ubuntu/data/quanthus/revisions/daily_market_CURRENT.json` and
   `/home/ubuntu/data/quanthus/revisions/daily_market_summary_CURRENT.json`.

The accepted P1 DWD revision is the historical base. Daily partitions form an immutable chain on
top of that base and contain only T rows, avoiding a new 10+ GB full-history copy each day. A rerun
of T creates a new child revision and may replace the T pointer only after the same gates pass.
A pointer cannot move to an earlier market date. DWS is a rebuildable projection whose manifest
contains the exact DWD revision and DWD-manifest checksum; it is not an alternate normalized source.

The two dataset pointers are not a cross-file transaction. Both revisions are gated before either
publication stage begins, but a DWS-pointer failure after DWD publication can leave DWS behind. The
run then fails, does not advance `LAST_SUCCESS.json`, and must be repaired by replaying the same
immutable inputs. Consumers of DWS must verify its declared DWD lineage rather than assume pointer
dates match. Future PIT jobs use the same runner and add stages before publication. PostgreSQL
publication remains an optional final projection after issue #37; it is never needed to establish
canonical file state.

### 4. Failure and audit behavior

- A single exclusive lock prevents overlapping market-date runs.
- Each attempt writes a durable `run.json` and per-stage logs below
  `/home/ubuntu/data/quanthus/runs/`.
- Measured stages record observed maximum duration, 100%-headroom SLA, actual elapsed seconds, and
  pass/fail state in the ledger. CN01's selected 3,935,273-byte transfer completed in 3.649s, so
  its transfer SLA is 8 seconds. The independently rounded verify/build/gate/publish budgets sum
  to 10 seconds. Hermes transfer is an availability fallback without a completed transfer baseline;
  its first successful fallback run records the measurement but is not falsely labeled as a
  CN01-qualified SLA.
- A failed stage stops all later stages. In particular, a failed gate cannot invoke publication.
- `LAST_SUCCESS.json` changes only after every configured stage succeeds.
- After the terminal `run.json` is durable, the runner sends configured completion/failure email
  through Resend and writes only delivery outcome metadata back to the ledger. API credentials stay
  in the protected environment. Notification failure is alertable but does not change a successful
  data result or roll back an accepted pointer.
- A CN01-qualified run completing after 17:26, or after any measured stage exceeds its SLA, is
  retained as `succeeded_late` and must alert; it is not relabeled as on time.
- A successful Hermes-fallback run without a recorded Hermes transfer baseline is retained as
  `succeeded_unqualified`, advances the verified data pointer, and alerts for SLA measurement.
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

The automated version closes the most urgent daily path without prematurely designing the
intraday system. It also makes the collector/warehouse boundary executable rather than a verbal
convention and publishes a small, lineage-bound daily market summary without making DWS canonical.
Weekly OCI PIT transforms and PostgreSQL projection remain separate increments; adding them does
not change the daily release or runner contracts.
