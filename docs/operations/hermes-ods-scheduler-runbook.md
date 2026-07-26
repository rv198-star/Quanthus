# Hermes ODS Scheduler V1 Runbook

This runbook deploys the schedule in
`config/schedules/hermes-ods-schedule.v1.json`. Times are Asia/Shanghai. Issue #41 is the change
record; Hermes must not alter live cron until OCI posts `GO_FOR_CUTOVER`.

## Validate the contract

On OCI, from the repository checkout:

```bash
make schedule-check
```

The result must name `hermes-ods-schedule-v1`, five lanes, and `Asia/Shanghai`. This validates
platform invariants, not the live Hermes scheduler.

## Hermes implementation contract

Each recurring unit must provide:

- one launcher with `set -euo pipefail`, a lane-specific lock, and a bounded timeout;
- one config describing datasets, provider requests, retry limits, and watermarks;
- one append-only attempt directory and structured log;
- per-dataset status rather than a wrapper-only exit status;
- source request, source revision, acquisition time, watermark, `knowledge_cutoff`, row count,
  file size, and SHA-256 in the manifest;
- a `READY` marker written only after all mandatory files and the manifest are atomically sealed;
- a nonzero exit and alert for `failed` or `partial` output.

Provider-mode event and weekly collectors are mapped by
`HERMES_ODS_PROVIDER_SPEC`; see
`config/pipelines/hermes-provider-collectors.spec.example.json`. Commands are argv arrays (shell
strings are rejected) and must write parquet to the supplied `{output}` path. The path is inside
the current attempt. A missing command, nonzero exit, missing output, or provider request error
fails the mandatory dataset and cannot advance a watermark or create `READY`.

Minute provider collection is always written under the current attempt. Shared legacy minute
partitions and ad hoc `.nodata` files are not accepted as shadow evidence. A no-bar security must
be justified by same-day tradability, PIT suspension, or lifecycle evidence. Setting
`HERMES_ODS_SYMBOL_LIMIT` creates smoke-only evidence and can never publish `READY`.

Hermes may write only raw/ODS artifacts. Remove all direct curated PG sync from replacement
launchers. OCI publication is a separate job and credential.

## Shadow deployment

Implement the five lanes without modifying old crontab entries. `bootstrap` is a manual command
and receives no timer. Install new timers disabled, then enable only shadow output paths.

### Phase B launchers (repository)

| Lane | Launcher | Unit (disabled by default) |
| --- | --- | --- |
| market-daily-core | `ops/bin/run-hermes-ods-lane --lane market-daily-core` | `ops/systemd/hermes-ods-market-daily-core.{service,timer}` |
| calendar-daily-events | `ops/bin/run-hermes-ods-lane --lane calendar-daily-events` | `ops/systemd/hermes-ods-calendar-daily-events.{service,timer}` |
| weekly-corrections | `ops/bin/run-hermes-ods-lane --lane weekly-corrections` | `ops/systemd/hermes-ods-weekly-corrections.{service,timer}` |
| intraday 1m | `ops/bin/run-hermes-ods-lane --lane intraday-archive --stream 1m` | `ops/systemd/hermes-ods-intraday-1m.{service,timer}` |
| intraday 5m | `ops/bin/run-hermes-ods-lane --lane intraday-archive --stream 5m` | `ops/systemd/hermes-ods-intraday-5m.{service,timer}` |
| bootstrap | manual only; runner refuses automatic execution | no timer |
| matrix | `ops/bin/run-hermes-ods-shadow-matrix` | `ops/systemd/hermes-ods-shadow-matrix.service` |

Shadow root defaults to `$ASTOCK_HOME/shadow/hermes-ods-v1`. Canonical
`$ASTOCK_HOME/releases/{daily,weekly}` and live cron remain untouched until
`GO_FOR_CUTOVER`.

Python entrypoints:

```bash
PYTHONPATH=src python -m quanthus.cli lane-run \
  --config config/schedules/hermes-ods-schedule.v1.json \
  --lane market-daily-core --market-date 2026-07-24 \
  --home /root/work/astock/data \
  --shadow-root /root/work/astock/data/shadow/hermes-ods-v1

PYTHONPATH=src python -m quanthus.cli lane-shadow-matrix \
  --config config/schedules/hermes-ods-schedule.v1.json \
  --open-date 2026-07-24 --closed-date 2026-07-25 \
  --weekly-date 2026-07-24 --event-date 2026-07-24
```

1. Run `market-daily-core` for at least five open sessions, one known closed weekday, and one
   injected missing/disagreeing calendar case.
2. Run `calendar-daily-events` for at least seven natural days including a weekend.
3. Run direct 1m and 5m streams for at least five open sessions. Prove no key/date coverage gap
   against the legacy combined job, prove that the two checkpoints advance independently, and
   exercise their closed and missing/disagreeing calendar paths.
4. Run `weekly-corrections` twice. Prove all four correction classifications and that no daily
   artifact is overwritten.
5. Re-run one attempt. It must create a distinct attempt/revision and preserve the first output.
6. Interrupt acquisition and transfer separately. Neither interruption may expose `READY`.

Hermes posts checksums, row counts, start/end times, exit codes, release paths, and injected
failure evidence to #41 with `READY_FOR_OCI`. OCI pulls through the read-only channel, verifies
the releases, compares coverage and lineage, and records the acceptance decision.

The matrix summary requires
`$MATRIX_SHADOW_ROOT/evidence/minute_legacy_coverage_diff.json`. It must identify the immutable
legacy source, set `coverage_ok=true`, and contain `streams.1m.unexplained_missing_count=0` and
`streams.5m.unexplained_missing_count=0`. A missing or incomplete diff is a matrix residual, not
an implicit pass.

## Cutover gates

OCI posts `GO_FOR_CUTOVER` only when all of the following are true:

- schedule configuration passes the repository validator;
- every required shadow scenario passes;
- market daily core finishes before 20:00 or raises a tested SLA alert;
- `closed_session`, `failed`, and `partial` cannot advance any canonical pointer;
- direct 1m and direct 5m have no unexplained coverage gap;
- weekly corrections retain both prior evidence and conflicting observations;
- Hermes cannot write OCI DWD/DWS/ADS or curated PG;
- OCI can rebuild and publish a representative DWD child from the shadow release.

## Controlled cutover order

Change one lane at a time and observe it before continuing:

1. Market daily core.
2. Calendar daily events.
3. Weekly corrections; disable the old Saturday monolith only after this lane passes.
4. Direct 1m, then direct 5m; disable the old combined minute job only after both pass.

For each lane, archive the old crontab text and script checksum in #41, disable rather than
delete the old entry, enable the replacement timer, run one manual dry-run/preflight, and capture
the first scheduled result. Obsolete completed watchdogs are disabled only after their target
jobs have been mapped to a replacement or explicitly retired.

## Daily operation

OCI checks the read-only Hermes schedule and release status without shell or write access:

```bash
~/bin/hermes-ods-schedule-check.sh status
~/bin/hermes-ods-schedule-check.sh cron
~/bin/hermes-ods-schedule-check.sh logs
```

An operator verifies the expected target date, preflight result, each mandatory dataset status,
watermark movement, READY checksum, duration, retry count, and alert state. File existence alone
does not establish success.

On a closed weekday, only a checksummed calendar-only release is valid. On missing/disagreeing
calendar evidence, there must be no provider calls and no READY. On weekends, only event-lane
output is expected.

## Failure and rollback

Do not edit a release, reuse its id, update PG directly, or fill missing observations with zeros.
Keep failed/partial attempts for diagnosis and retry into a new attempt.

If a replacement lane misses data or violates its manifest contract, disable only that new timer,
re-enable its archived legacy entry, and post `ROLLED_BACK` with exact evidence to #41. Do not
roll back lanes that are operating correctly. OCI does not advance DWD `CURRENT` from the bad
release and can rebuild from the last accepted ODS revision.

## Migration

On a replacement collector, restore repository code/config, source credentials, ODS raw evidence,
watermarks, and disabled/enabled timer state. Validate the schedule, run all preflights, publish a
shadow attempt for each lane, verify it from OCI, and only then enable production timers. Market
data, credentials, raw manifests, and run artifacts never enter Git.
