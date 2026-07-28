# T-Close Plus Five-Hour Runbook

## Runtime layout

```text
/opt/quanthus/current/                       deployed Git revision
/home/ubuntu/data/astock/releases/           read-only Hermes ODS mirror
/home/ubuntu/data/quanthus/revisions/        OCI DWD/DWS/PIT revisions and dataset CURRENT pointers
/home/ubuntu/data/quanthus/runs/             pipeline ledgers and logs
/home/ubuntu/data/quanthus/locks/            process locks
/etc/quanthus/t-close-plus-5h.env             secrets and deployment paths, never Git
```

## Hermes recurring handoff

Hermes first completes its source-specific daily job into a private landing directory. It then
generates a concrete spec from
`config/pipelines/hermes-daily-release.spec.example.json` and publishes it:

```bash
python -m quanthus publish-ods-release \
  --spec /root/work/astock/runs/2026-07-27/release-spec.json \
  --output /root/work/astock/data/releases/daily/2026-07-27
```

Do not publish the example file itself. All placeholders must be replaced, `source_revision` must
identify the actual acquisition attempt, and `knowledge_cutoff` must reflect observed availability.
Source files must be atomically replaced, not mutated in place, because release publication may
use hard links on the same filesystem. Historical bulk completion uses `schedule=bootstrap` and a
separate path; it is not substituted for the daily release.

Hermes and CN01 timers run Monday through Friday at 15:40 Asia/Shanghai, after the 15:05-15:30
fixed-price phase and final daily totals. Before session preflight, the acquisition wrapper must fetch a fresh
calendar window that includes the target date and build an attempt-local candidate. It then checks
the target date against both SSE and SZSE rows before contacting market-data providers. Refresh
failure, a missing row, or an SSE/SZSE disagreement is a hard failure and alert with no provider
calls or READY. Two open rows start the market job. Two closed rows produce only the calendar
release. The wrapper must not infer a holiday from an empty bar response. Weekend market runs are
not scheduled; disclosure-watermark collectors may use a separate calendar-day schedule.

An open-session release contains the T bar delta, raw T `tradestatus/isST` observations for the
complete in-scope universe, the complete small security master, and the calendar. A tradable status
without a bar or any security without a status fails the OCI gate. The current stock list scopes T
coverage so inactive historical master codes are not treated as live. A known closed weekday may
publish only the calendar artifact. OCI then records a clean skip without producing or advancing a
DWD partition. No release or incomplete T calendar evidence is a pipeline failure.

OCI's 15:40 timer starts a readiness gate, not DWD/DWS cleaning. It probes both same-date sources
every 30 seconds, chooses the first observed READY immutable attempt, and uses CN01 on a same-poll
tie. The selected source and attempt are persisted in the run directory before transfer. The latest
ODS-ready deadline is 17:25, based on 52m18.4s observed acquisition plus 100% headroom. CN01's
3.649s measured transfer has an 8s SLA; the measured local stages have 10 seconds of independently
rounded budget, giving a CN01-qualified completion deadline of 17:26. Hermes remains a usable
fallback, but its first successful transfer is explicitly recorded as the baseline rather than
being evaluated against the CN01 transport SLA.

For an open session, OCI builds the immutable daily DWD partition and then a one-row DWS market
summary bound to the DWD revision and manifest checksum. Both revisions pass their gates before
publication begins. Publication advances `daily_market_CURRENT.json` and
`daily_market_summary_CURRENT.json` separately; consumers of the DWS pointer must validate its
`dwd_revision` rather than assuming the two pointers form one atomic pair.

Weekly source jobs use the weekly example and preserve correction lookbacks. Their OCI transforms
are added as separate pipeline configurations rather than inserted into the daily critical path.

## OCI deployment

1. Install the repository and P1 environment, then point `/opt/quanthus/current` at the release.
2. Install `ops/systemd/t-close-plus-5h.env.example` as
   `/etc/quanthus/t-close-plus-5h.env`, mode `0600`, and adjust paths only. Do not add secrets to
   the repository copy. Set `QUANTHUS_CODE_REVISION` to the full commit deployed behind
   `/opt/quanthus/current`; the doctor rejects a symbolic branch name or abbreviated hash. Set
   `QUANTHUS_RESEND_API_KEY` in the protected environment for configured outcome email; it must
   never appear in Git, command arguments, or the run ledger.
3. Install the service and timer from `ops/systemd/`.
4. Run the doctor and validate the timer before enabling it.

```bash
sudo install -d -m 0755 /etc/quanthus
sudo install -m 0600 ops/systemd/t-close-plus-5h.env.example \
  /etc/quanthus/t-close-plus-5h.env
sudo install -m 0644 ops/systemd/quanthus-t-close-plus-5h.service /etc/systemd/system/
sudo install -m 0644 ops/systemd/quanthus-t-close-plus-5h.timer /etc/systemd/system/
sudo install -m 0644 ops/systemd/quanthus-pipeline-failure@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo --preserve-env=QUANTHUS_HERMES_RSYNC_KEY,QUANTHUS_HERMES_KNOWN_HOSTS \
  ops/bin/t-close-plus-5h-doctor
sudo systemctl enable --now quanthus-t-close-plus-5h.timer
```

Set both source connection groups in `/etc/quanthus/t-close-plus-5h.env`: the existing Hermes
read-only rrsync variables and the `QUANTHUS_CN01_ODS_*` variables in the example environment.
`QUANTHUS_HERMES_ODS_RELEASE_ROOT=shadow/hermes-ods-v1/releases` stays relative to the rrsync jail;
CN01's source root is absolute because its dedicated key is restricted to that node.

Each daily release calendar must cover the date being scheduled. Missing T evidence is an error,
not a holiday assumption. The timer is not enabled until the first real open-session release and
one calendar-only closed-session release pass manual rehearsal.

## Manual rehearsal

```bash
ops/bin/wait-ods-release-ready daily 2026-07-27 /tmp/ods-source-selection.json 6277 30
ops/bin/pull-ods-release daily 2026-07-27 /home/ubuntu/data/astock /tmp/ods-source-selection.json
.venv/bin/python -m quanthus verify-ods-release \
  --release-dir /home/ubuntu/data/astock/releases/daily/2026-07-27
ops/bin/run-t-close-plus-5h 2026-07-27
```

Inspect the exact run ledger rather than inferring success from a Parquet file appearing:

```bash
find /home/ubuntu/data/quanthus/runs/t-close-plus-5h-v1 -name run.json -type f
```

Successful publication creates or advances
`/home/ubuntu/data/quanthus/revisions/daily_market_CURRENT.json` and, after it,
`daily_market_summary_CURRENT.json`. Failed builds can leave an immutable unpromoted revision for
diagnosis. A failure before publication changes neither pointer; a failure during the second
pointer publication may leave the canonical DWD ahead of its rebuildable DWS projection and does
not advance `LAST_SUCCESS`. Replay the exact market date and immutable handoff, then confirm DWS
lineage before treating the run as recovered. An SLA-late run exits nonzero after retaining its
successful publication, so systemd records a failed unit for monitoring even though canonical data
is not rolled back. The failure handler puts the full unit status in the journal; production
monitoring must alert on either failed unit.

## Outcome notifications

The versioned pipeline config sends one terminal completion or failure email through Resend after
the durable `run.json` exists. The runner records provider, event, status, attempts, and any bounded
error text under `notifications.email`; it never records the API key. Notification delivery is a
secondary monitoring path: failure is visible in the ledger but does not turn a successful data
pipeline into a failed publication or roll back canonical data. Systemd failure monitoring remains
required independently of email delivery.

## Migration

On a replacement OCI node, restore the Git deployment, recreate the virtual environment, install
the protected environment and read-only Hermes key, and restore the accepted Quanthus revisions.
The ODS mirror may either be copied or repulled. Run `t-close-plus-5h-doctor`, verify the current
pointer and one historical release, execute one manual market-date rehearsal, then enable the
timer. PostgreSQL is not part of this recovery path because it remains rebuildable projection
state.
