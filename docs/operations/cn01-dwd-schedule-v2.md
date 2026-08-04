# Base V2 ODS/DWD recurring schedule

The Base V2 daily path uses the same control plane on CN01 and Hermes. One timer starts at
`18:30 Asia/Shanghai`, runs the four ODS lanes together, and waits up to one hour. The market,
1m, and 5m lanes retain their provider resource locks; a lock-busy result is retried within the
same deadline. The calendar/event lane runs independently.

Before starting a lane, the controller searches the node-local release tree for the newest
same-date release that passes the full ODS manifest, READY, size, and content verification. A
verified existing release is reused and recorded in the controller manifest with
`source=existing_ready_release`; it is never recollected merely because the controller was
restarted. Missing lanes alone are acquired.

After all four releases are READY, the controller seals a four-lane catalog and immediately runs
the Base-seeded daily DWD builder. The builder reads the ODS releases and immutable DWD Base,
writes one audited daily increment under `QUANTHUS_DWD_DAILY_ROOT`, and advances only that
node-local daily `CURRENT.json` after verification. ODS Base, DWD Base, and ODS releases remain
read-only. DWD receives its own one-hour execution budget after the ODS gate; the controller's
systemd bound is 130 minutes.

The fixed `19:30` DWD timer is not part of the normal path. Its unit remains an operator fallback
and requires `/etc/quanthus/cn01-dwd-fixed-timer-enabled`; production keeps the timer disabled.
The wall clock starts work, while four verified READY releases are the DWD execution gate.

CN01 and Hermes each send their own idempotent completion or failure notification after the
controller reaches a terminal state. Subjects identify both node and stage:

- `[Quanthus][CN01][DWD构建]` for the CN01 build;
- `[Quanthus][Hermes][DWD构建]` for the Hermes build.

The node-local protected `/etc/quanthus/dwd-notification.env` owns Resend credentials. Notification
failure is recorded and alerted but does not mutate or roll back an accepted DWD package.

Weekly ODS corrections remain on the independent Saturday `08:00` timer on both nodes. The
existing weekly DWD input-preparation unit remains disabled until the weekly domain transforms are
implemented and audited; this daily cutover does not claim weekly DWD completion.

The four former daily ODS lane timers are rendered as explicit rollback units. They require
`/etc/quanthus/<node>-ods-legacy-timers-enabled` and must not be enabled beside the controller.
The former `cn01-dwd-daily-input.timer` and `cn01-dwd-weekly-input.timer` remain disabled control
plane evidence and are not normal execution paths. DWS remains disabled by current operating
policy.

Every successful controller run writes `run.json` and READY under the node shadow root. Any lane,
catalog, or DWD failure returns nonzero, writes no controller READY, triggers the node failure
notification, and leaves all Base and consumer pointers unchanged.
