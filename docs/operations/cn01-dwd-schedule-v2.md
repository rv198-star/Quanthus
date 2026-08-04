# CN01 ODS/DWD v2 schedule control

The v2 CN01 daily path has one control-plane timer at 18:30 Asia/Shanghai. It starts all four
lanes together. The market, 1m, and 5m lanes share the existing `pve_market_api` resource lock;
a lock-busy result is retried until the same one-hour orchestration deadline. The calendar lane
uses its independent event resource group. This preserves the market-daily critical path while
using the available parallelism without provider contention.

The controller waits for all four immutable ODS releases to be `READY`, seals a four-lane catalog,
and immediately invokes the daily DWD input preparation. A successful run writes a controller
manifest and `READY` under the CN01 shadow root. A lane, catalog, or DWD failure produces no
success marker and does not advance an ODS or Base pointer. The catalog and DWD input run are
both outside the ODS Base and DWD Base packages.

The v2 CN01 schedule also has one independent weekly input-preparation lane:

- `cn01-dwd-weekly-input.timer` starts a bounded wait and seals one weekly ODS correction release
  only after the weekly ODS `READY` exists; 09:00 is not treated as a completion guarantee.

The daily and weekly preparation commands invoke `ops/bin/run-cn01-dwd`. The command verifies the ODS Base and DWD Base
manifest bindings from `config/releases/v2-build202607.json`, writes a run-local catalog, and
emits `run.json` plus `READY` under `QUANTHUS_DWD_RUN_ROOT`. It never writes either Base, ODS
release, `CURRENT`, or a DWS package.

The run manifest exposes eight logical DWD owners (`#57` through `#64`). The trading-calendar
backbone is an internal step of `#59`, so it is not a ninth scheduled business script. DWS remains
disabled by policy.

The existing `cn01-daily-dwd.service` is the obsolete seven-table path. Both that service and its
timer now require `/etc/quanthus/legacy-dwd-enabled`; without that explicit sentinel they are
skipped. The four former CN01 daily ODS lane timers are rendered as rollback units and require
`/etc/quanthus/cn01-ods-legacy-timers-enabled`; they are therefore inactive after the orchestrator
cutover. The former `cn01-dwd-daily-input.timer` similarly requires
`/etc/quanthus/cn01-dwd-daily-timer-enabled` and is not part of the normal path.

The calendar event lane starts with the 18:30 snapshot. Its seven-day lookback means announcements
published later in the evening are picked up by the next natural-day run; the controller does not
claim an 18:30 knowledge cutoff is a 19:30 final sweep.

Input preparation is intentionally separate from domain execution. The current domain builders
are still Base-only (`mode=base`). A daily or weekly run must not invoke them until their declared
incremental modes are implemented and audited. The sealed run is the handoff contract for that
next step.
