# CN01 to OCI DWD Daily Handoff

CN01 publishes one immutable package under
`/srv/quanthus/dwd-dws-v2/daily/packages/<market-date>/`. OCI pulls only the package named by
CN01's `daily/CURRENT.json`; it never reads ODS, rewrites a package, or copies the historical
daily tree. The OCI timer starts at `20:30 Asia/Shanghai` and waits until `20:00` on T+1
(23.5 hours) for CN01's same-date READY-gated DWD pointer. This includes the `17:40` T+1
source-maturity deadline and a bounded ODS/DWD build margin, and releases the unit before the
next `20:30` timer. The target market date is persisted under the OCI DWD root, so
cross-midnight recovery continues the original trading date instead of following the wall clock.
The wall clock is not proof that DWD is complete.

## CN01 read-only export

Create a locked `quanthus-dwd-ro` account and install one dedicated public key with an exact
forced command. The account must not have a shell key or access to `/root/work`:

```text
no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,no-X11-forwarding,command="/usr/bin/rrsync -ro /srv/quanthus/dwd-dws-v2/daily" ssh-ed25519 OCI_DWD_READ_KEY
```

The DWD daily root is owned by `root:quanthus-dwd-ro`, directories are `g+rx`, and files are
`g+r`. Install `cn01-dwd-daily.service` with `Group=quanthus-dwd-ro` so future immutable files
inherit the export group. The existing historical daily packages receive the same group/read
normalization once during deployment.

## OCI sync

The warehouse units run from the dedicated immutable runtime symlink
`/opt/quanthus/warehouse-current`, independent of the shared `/opt/quanthus/current` runtime used
by other OCI jobs. This allows warehouse recovery fixes to deploy without advancing unrelated
production services. The units reuse the protected Resend key in
`/etc/quanthus/t-close-plus-5h.env`; an optional `/etc/quanthus/oci-dwd-sync.env` may override
non-sensitive settings. Install and enable:

```text
oci-dwd-daily-sync.timer
oci-dwd-daily-sync.service
oci-dwd-sync-notification.service
oci-dwd-sync-failure-notification.service
```

The sync stages the remote package, downloads `READY` last, runs the same deep DWD manifest and
content checks as CN01, and atomically installs the package and OCI `CURRENT.json`. Existing
packages are immutable: a same-date digest mismatch fails closed. A receipt is written under
`$QUANTHUS_OCI_DWD_LOCAL_ROOT/receipts/<market-date>/`.

When no `--market-date` is supplied, the sync creates or resumes the persisted pending market-date
target. The first invocation binds that target to the current `Asia/Shanghai` date; subsequent
recovery invocations reuse it across midnight until the exact package is synchronized successfully.
A stale prior-day pointer is retried within the bounded wait and fails only after that wait rather
than silently reusing yesterday's package. The pending target remains for the next timer or a
date-bounded operator recovery if CN01 publishes after the wait. Source-date failures are marked
with `cn01_source_failure=true`; transport, local disk, and local immutability failures remain
separately classified.

After a successful sync, OCI sends only `oci_dwd_sync_completed`. Its subject begins with
`[Quanthus][OCI][DWD同步]`. CN01 sends its own build notification on CN01; OCI does not infer or
relabel a CN01 build result from transfer evidence.

When the sync fails, it first writes
`$QUANTHUS_OCI_DWD_LOCAL_ROOT/failures/<market-date>/latest.json` and the atomic pointer
`failures/LATEST.json`. `oci-dwd-sync-failure-notification.service` sends only
`oci_dwd_sync_failed`, with subject `[Quanthus][OCI][DWD同步]`. The receipt still distinguishes a
stale or invalid source from transport and local-disk failures, but OCI never sends a CN01 build
email.

Notification state is stored under `QUANTHUS_NOTIFICATION_STATE_ROOT`; retries for the same
event/date/manifest or bounded-failure digest do not create duplicate accepted emails. Resend
HTTP, network, and timeout failures are retried up to `QUANTHUS_RESEND_RETRIES`; after the final
attempt the notifier writes a receipt under `QUANTHUS_NOTIFICATION_STATE_ROOT/failures/` and
returns nonzero. A provider outage cannot generate a second email about itself, so the local
failure receipt and systemd journal remain the authoritative evidence for that case.
