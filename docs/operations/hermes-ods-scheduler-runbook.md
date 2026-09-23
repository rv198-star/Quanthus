# Hermes ODS Scheduler V1 Runbook

This runbook deploys `config/schedules/hermes-ods-schedule.late.v1.json` on Hermes and
`config/schedules/hermes-ods-schedule.v1.json` on CN01. Times are Asia/Shanghai. Issue #41 is the
change record; Hermes must not alter live cron until OCI posts `GO_FOR_CUTOVER`.

## Validate the contract

On a fresh ODS node, install the repository-owned providers, then validate the
contract from the repository checkout:

```bash
make bootstrap-ods
make schedule-check
```

The result must name the selected versioned schedule, five lanes, and `Asia/Shanghai`. This
validates platform invariants, not the live node scheduler.

## Node-local Git deployment

GitHub is the only normal code-distribution path. Every ODS node has its own read-only deploy key;
private keys are never copied between Hermes, CN01, and OCI. The fixed deployment path is a Git
checkout at `/root/work/Quanthus`, tracking `origin/main`. OCI may publish commits and audit nodes,
but it must not distribute repository archives, rsync source trees, or maintain an untracked copy
as the normal deployment state.

For each reviewed release:

1. Confirm all lane services are inactive and preserve the current HEAD, unit checksums, and timer
   state in the node-local cutover evidence directory.
2. Stop timers only, then run `git fetch origin main` and `git pull --ff-only origin main` in the
   node checkout. Merge, rebase, hard reset, and a dirty tracked worktree are deployment failures.
3. Verify the full expected commit, run `make schedule-check` plus the focused contract tests, and
   render node units from that same checkout:

```bash
ops/bin/render-ods-node-units --node-id hermes --output /tmp/quanthus-ods-units
# CN01 uses: --node-id cn01
sudo install -m 0644 /tmp/quanthus-ods-units/* /etc/systemd/system/
sudo systemctl daemon-reload
```

The rendered directory includes the generic `quanthus-pipeline-failure@.service` handler. Node
renaming changes unit names, the protected environment-file path, and the market-window metadata,
but the repository-owned launcher remains `ops/bin/run-hermes-ods-lane` for each individual lane.
CN01 renders its daily orchestrator at 18:30; Hermes renders it at 19:30. Both use a 540-minute
systemd bound. The controller reuses verified same-date READY releases, starts only
missing lanes, seals the daily catalog, and builds the node-local DWD increment after all four
lanes pass. The former four daily lane timers are rendered with
`ConditionPathExists=/etc/quanthus/<node>-ods-legacy-timers-enabled` as rollback units and must not
be enabled alongside the controller. Weekly corrections retain their independent Saturday timer.
Do not hand-edit installed timers.

4. While timers are still stopped, clear any retained failed state for their services. Only then
   start timers, without manually starting services. Do not run `systemctl reset-failed` on a
   timer-triggered service after its timer is active: clearing that state can release a pending
   trigger immediately and create an out-of-window attempt. The market service first runs
   `check-ods-collection-window`; an elapsed same-day event presented after a timer restart is
   condition-skipped outside the node's contract-defined five-minute grace and creates no attempt.
   Do not bypass that gate with `systemctl start` on the service. Record the deployed commit,
   transformed unit checksums, next trigger times, and rollback path. OCI independently verifies
   the exported evidence through its read-only channel.

Node-local `.venv/`, `.deps/`, protected env files, Base/ODS data, attempts, releases, and
watermarks remain outside Git. A bootstrap archive may seed data, but it is never a substitute for
the Git checkout that owns executable code and configuration.

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

The repository-owned default adapter is `quanthus.providers.ods_snapshot`. It windows a
node-local canonical ODS/Base parquet into the attempt and records source lineage; it is not an
upstream vendor collector. A replacement ODS node therefore needs both the immutable Base seed
and explicit upstream refresh jobs for daily/event/weekly sources. A successful snapshot lane
proves scheduling and release semantics, but it does not prove those mutable sources are current.
Upstream credentials and raw responses remain node-local and never enter Git.

Market-dependent provider attempts use `quanthus.providers.trading_calendar` before session
preflight. It requests `baostock.query_trade_dates` for the target date and a seven-day lookback,
requires the target date in the fresh response, and writes the merged candidate under the current
attempt's `landing/preflight/`. It does not overwrite the Base calendar. Market and minute
collectors must not run if this refresh fails.

For an open session, inspect `landing/provider_readiness.json`. The market collector polls
`sh.000001`, `sh.000300`, and `sz.399001` until all three return the target date or the configured
readiness deadline expires. An empty successful response is not a connection error and must not
cause an immediate logout/login cycle. Provider/network errors have bounded reconnects; each
successful login applies a 30-second socket timeout. After readiness, the first stock pass is
followed by one retry of only the missing set. A residual gap or the configured completion
deadline produces no READY. The CN01 profile uses 19:00/19:46. Following the
2026-09-23 incident, the Hermes late profile retains 20:00 readiness but uses a
23:36 completion deadline: 4,680 successful requests took 75 minutes, leaving
312 in-scope securities unqueried at the former 20:46 cutoff despite zero provider
errors. This restores the profile's already-declared 7,380-second observed duration
plus 100% safety margin from its 19:30 trigger; it does not weaken row coverage or
turn a partial attempt into READY. The profile revision is
`hermes-late-window-throughput-budget-20260923`.
The rendered Hermes daily controller now gets a six-hour ODS budget and one
attempt; its separate one-hour DWD budget still fits under the nine-hour unit
bound. This replaces the 90-minute first attempt plus four-hour retry, whose
first attempt could never honor the extended market deadline. CN01 retains its
existing two-attempt profile. Do not deploy only the schedule JSON without the
matching rendered Hermes controller unit.

Minute provider collection is always written under the current attempt. Shared legacy minute
partitions and ad hoc `.nodata` files are not accepted as shadow evidence. A no-bar security must
be justified by same-day tradability, PIT suspension, or lifecycle evidence. Setting
`HERMES_ODS_SYMBOL_LIMIT` creates smoke-only evidence and can never publish `READY`.
`HERMES_ODS_MINUTE_TIMEOUT_SEC` defaults to 1800 seconds for the serial full-universe collector;
it must be at least 60 seconds and no more than the four-hour systemd service limit. Multi-page
matrix backfills set their own explicit, still-bounded timeout.
The default minute collector is the repository module `quanthus.providers.minute_tdx`; a reviewed
external fork may be selected with `HERMES_ODS_MINUTE_COLLECTOR`. Both forms receive an
attempt-local `--home` and have no curated-PG path.
When the explicitly enabled Tencent 1m path is used, the collector first probes
traded SZ, SH, and STAR symbols from the sealed same-date daily/status evidence,
preferring non-round daily share volumes for the lot-based markets. A failed
probe writes `source_qualification.json`, retains the raw response in the
attempt, and stops before scanning the universe. A passed probe is only an
early source check: every security still has to pass exact date, session-grid,
daily-volume, and close validation. The absent minute amount stays nullable;
neither probe success nor nullable amount removes the #436 quality limitation.

Hermes may write only raw/ODS artifacts. Remove all direct curated PG sync from replacement
launchers. OCI publication is a separate job and credential.

## Shadow deployment

Implement the five lanes without modifying old crontab entries. `bootstrap` is a manual command
and receives no timer. Install new timers disabled, then enable only shadow output paths. On both
CN01 and Hermes, enable the daily controller and leave the four rendered rollback timers
condition-skipped.

### Phase B launchers (repository)

| Lane | Launcher | Unit (disabled by default) |
| --- | --- | --- |
| market-daily-core | `ops/bin/run-hermes-ods-lane --lane market-daily-core` | `ops/systemd/hermes-ods-market-daily-core.{service,timer}` |
| calendar-daily-events | `ops/bin/run-hermes-ods-lane --lane calendar-daily-events` | `ops/systemd/hermes-ods-calendar-daily-events.{service,timer}` |
| weekly-corrections | `ops/bin/run-hermes-ods-lane --lane weekly-corrections` | `ops/systemd/hermes-ods-weekly-corrections.{service,timer}` |
| intraday 1m | `ops/bin/run-hermes-ods-lane --lane intraday-archive --stream 1m` | `ops/systemd/hermes-ods-intraday-1m.{service,timer}` |
| intraday 5m | `ops/bin/run-hermes-ods-lane --lane intraday-archive --stream 5m` | `ops/systemd/hermes-ods-intraday-5m.{service,timer}` |
| CN01/Hermes daily control plane | `ops/bin/run-cn01-ods-daily` | `ops/systemd/hermes-ods-daily-orchestrator.{service,timer}` (node render) |
| bootstrap | manual only; runner refuses automatic execution | no timer |
| matrix | `ops/bin/run-hermes-ods-shadow-matrix` | `ops/systemd/hermes-ods-shadow-matrix.service` |

Shadow root defaults to `$ASTOCK_HOME/shadow/hermes-ods-v1`. Canonical
`$ASTOCK_HOME/releases/{daily,weekly}` and live cron remain untouched until
`GO_FOR_CUTOVER`.

Within the shadow daily release tree, only `market-daily-core` advances the date-local
`CURRENT.json`. After `calendar-daily-events` completes, verify that its immutable release and
watermark exist while the market-data `CURRENT.json` bytes remain unchanged. A changed pointer is
a candidate-blocking incident; retain both release manifests, restore only the last verified
market-data descriptor atomically, and do not delete either release.

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

1. Run `market-daily-core` on both Hermes and CN01 for at least five open sessions, plus one known
   closed weekday and one injected missing/disagreeing calendar case. Keep Hermes at 17:30 and
   CN01 at 18:30 for the full v3 sample. Compare the contract metrics in each acquisition log; do not
   select a preferred window from one day.
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
- provider-mode market and minute attempts prove a fresh target-date calendar refresh before
  their three-state preflight;
- every required shadow scenario passes;
- market daily core publishes READY by 19:46 or raises a tested SLA alert;
- the early/late collection experiment contains at least five open sessions per node before any
  timer preference is accepted;
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

These commands must be interpreted in the correct network namespace. The OCI reverse tunnel
binds host-loopback `127.0.0.1:2222`; a Codex command running in the default filesystem/network
sandbox may not see that listener and can falsely report `tunnel not listening`. Before declaring
an outage, repeat the approved probe in the OCI host network context and require both:

```bash
ss -ltn | grep '127.0.0.1:2222'
~/bin/hermes-ods-schedule-check.sh status
```

The first proves the host listener and the second proves end-to-end SSH forced-command access.
Service state on Hermes is not inferred from either one alone. The channel remains read-only:
`hermes-ods-ro` exposes the sanitized schedule commands and an rrsync jail, while `astock-ro`
exposes only the data jail. Channel availability never authorizes `git pull`, unit installation,
timer restart, an interactive shell, or a root-key probe from OCI. Hermes performs its own GitHub
fast-forward deployment; OCI consumes the exported evidence and independently audits it.

An operator verifies the expected target date, `preflight_evidence.status=ready`, the fresh
calendar source/window/checksum, preflight result, each mandatory dataset status, watermark
movement, READY checksum, duration, retry count, and alert state. File existence alone does not
establish success.

For the collection-window experiment, also compare `metrics.readiness_polls`,
`empty_response_count`, `provider_error_count`, `reconnect_count`, average/maximum request duration,
and batch duration from `acquisition_log.json`. Keep node, market date, universe size, and contract
revision in the comparison. Provider readiness time and network congestion are different possible
causes; do not infer either from a single aggregate duration.

On a closed weekday, only a checksummed calendar-only release is valid. On refresh failure or
missing/disagreeing calendar evidence, there must be no market/minute provider calls and no READY.
On weekends, only event-lane output is expected.

## Failure and rollback

The current remote execution channel is NexusAgent as required by root `AGENTS.md`.
Older OCI-specific access examples in this historical runbook are not permission
to restore that channel. A missing node connection blocks deployment verification,
not local engineering and not by itself proof of a production outage.

Under `META-OPS-PROCESS-001`, daily lane/catalog/DWD children use owned process
groups. The controller gives TERM 10 seconds, then KILL up to 10 seconds, and only
releases ownership after the group disappears. Exit 75 / `process_cleanup_unconfirmed`
means automatic retry is prohibited: inspect the retained run/log/notification
context and confirm quiescence through the approved node channel before any manual
recovery. Do not convert that exit to success or start a replacement while writers
may remain. A missing launcher also leaves an explicit failed run rather than
continuing to catalog/DWD.

Before deploying this slice, validate Linux shell pipelines and service-stop
behaviour, including the installed systemd cgroup kill settings. An externally
daemonized collector and a controller killed without cleanup are outside the
process-group proof. Retain failed evidence and rollout/rollback receipts under
Issue #425; merging the code alone does not close it.

Do not edit a release, reuse its id, update PG directly, or fill missing observations with zeros.
Keep failed/partial attempts for diagnosis and retry into a new attempt.

Every failed scheduled or recovery run must be entered on the same Shanghai calendar day in
`docs/operations/ods-dwd-incident-log.md`. Open unresolved cases as `pending_attribution` rather
than guessing a cause. Preserve the failed attempt and orchestrator paths, distinguish trigger from
software root cause, state which downstream stages were blocked, and append recovery and permanent
corrective evidence when confirmed. A systemd journal entry or notification alone is not the
operations incident record.

The daily service's first attempt always obeys the schedule contract's fixed completion deadline.
If that attempt fails, `run-ods-daily-with-retry` gives only the second attempt the configured
`QUANTHUS_ODS_DAILY_RETRY_RECOVERY_SECONDS` window. The orchestrator records the resulting
`market_recovery_deadline_at`, passes it only to `market-daily-core`, and continues to require all
four READY releases before catalog or DWD construction. This makes a terminal timeout retry useful
without weakening coverage checks or silently moving the recurring schedule deadline.

If a replacement lane misses data or violates its manifest contract, disable only that new timer,
re-enable its archived legacy entry, and post `ROLLED_BACK` with exact evidence to #41. Do not
roll back lanes that are operating correctly. OCI does not advance DWD `CURRENT` from the bad
release and can rebuild from the last accepted ODS revision.

During the five-session A/B, a failed node remains fail-closed and may be rerun as a new attempt,
but its timer is not moved into the other node's window mid-sample. A safety or repeated-availability
failure ends the experiment explicitly and records the shortened sample before rollback.

A missed daily release is recovered with the launcher, never by moving or manually firing the
timer. Pass the original `--market-date` and a reviewed, timezone-aware
`--recovery-deadline-at`; the deadline must be in the future. The resulting attempt and acquisition
log are marked `run_mode=manual_recovery` and
`excluded_from_collection_window_experiment=true`. It may restore ODS continuity and feed OCI, but
it is never counted as an early/late A/B observation.

## C2 opt-in minute recovery (not deployed)

For programme #424, the Owner's 2026-09-22 task-scoped exception in root AGENTS.md
authorizes existing passwordless SSH first, without MCP node management. Preserve
known-host checks and GitHub distribution. Connectivity is not deployment approval;
do not replace a running daily job's checkout or restart its service during preflight.

Issue #429 / `META-OPS-MINUTE-002` is governed by the data-foundation LLD's
“Opt-in same-attempt 1m residual retry” section. No service env or timer is changed
by installing this code. After Linux and node release gates pass, an approved rollout
may set `HERMES_ODS_MINUTE_1M_RECOVERY_POLICY` to the absolute path of the deployed
`config/pipelines/minute-attempt-recovery.v1.json`. Record its id and SHA-256 from
both lane metadata and collector status with the code revision and node receipt.
The v1 budget is 1740 seconds; the existing default outer timeout is 1800 seconds.
Do not combine this setting with a seed or collector override. The 5m lane ignores
this 1m-only setting; the standalone collector rejects an explicit 5m policy request.

Expect `symbol_attempts`, `scan_round`, `terminal_reason`, and per-symbol `attempts`
history. `budget_exhausted`, storage failure or circuit opening is failure, not
partial success/READY. An uncooperative call may reach the outer timeout; verify
process/cgroup cleanup before retry. Do not infer that a recovered checkpoint alone
proves coverage, date validity, publication, or timely SLA success.

Rollback after a controlled stop by removing only this opt-in env setting, preserving
failed evidence; do not mutate published packages or Base. This disables extra rounds
and the cooperative budget without changing OCI sync. C2 does not repair or authorize
the separate cross-attempt seed path or the formal-5m fallback conformance gap.

## Migration

On a replacement collector, restore repository code/config, source credentials, ODS raw evidence,
watermarks, and disabled/enabled timer state. Validate the schedule, run all preflights, publish a
shadow attempt for each lane, verify it from OCI, and only then enable production timers. Market
data, credentials, raw manifests, and run artifacts never enter Git.

For a Base-seeded validation node, keep all timers disabled until market, events, weekly, 1m, and
5m smoke attempts have reached a natural terminal state. The Base seed, shadow attempts, releases,
watermarks, host env files, and provider credentials are deployment state, not repository content.
