# CN01 to OCI DWD Daily Handoff

CN01 publishes one immutable package under
`/srv/quanthus/dwd-dws-v2/daily/packages/<market-date>/`. OCI pulls only the package named by
CN01's `daily/CURRENT.json`; it never reads ODS, rewrites a package, or copies the historical
daily tree. The OCI timer runs at `20:30 Asia/Shanghai`, after the CN01 DWD timer at `19:30`.

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

The units reuse the protected Resend key in `/etc/quanthus/t-close-plus-5h.env`; an optional
`/etc/quanthus/oci-dwd-sync.env` may override non-sensitive settings. Install and enable:

```text
oci-dwd-daily-sync.timer
oci-dwd-daily-sync.service
oci-dwd-cn01-notification.service
oci-dwd-sync-notification.service
oci-dwd-sync-failure-notification.service
```

The sync stages the remote package, downloads `READY` last, runs the same deep DWD manifest and
content checks as CN01, and atomically installs the package and OCI `CURRENT.json`. Existing
packages are immutable: a same-date digest mismatch fails closed. A receipt is written under
`$QUANTHUS_OCI_DWD_LOCAL_ROOT/receipts/<market-date>/`.

When no `--market-date` is supplied, the sync requires CN01 `CURRENT.json` to be today's
`Asia/Shanghai` date. A stale pointer is a failure rather than silently reusing yesterday's
package. Source-date failures are marked with `cn01_source_failure=true`; transport, local disk,
and local immutability failures are attributed to OCI only.

After a successful sync, the two `OnSuccess` units send distinct idempotent Resend emails:

- `cn01_dwd_completed`: CN01's package was verified and handed off.
- `oci_dwd_sync_completed`: OCI installed and advanced its local DWD pointer.

When the sync fails, it first writes
`$QUANTHUS_OCI_DWD_LOCAL_ROOT/failures/<market-date>/latest.json` and the atomic pointer
`failures/LATEST.json`. `oci-dwd-sync-failure-notification.service` then sends:

- `oci_dwd_sync_failed`: the OCI synchronization attempt failed;
- `cn01_dwd_failed`: additionally, when the receipt proves the already-read CN01 current/package
  is stale or invalid. An SSH/rsync transport error is kept as an OCI failure and is not
  misattributed to CN01.

Notification state is stored under `QUANTHUS_NOTIFICATION_STATE_ROOT`; retries for the same
event/date/manifest or bounded-failure digest do not create duplicate accepted emails. Resend
HTTP, network, and timeout failures are retried up to `QUANTHUS_RESEND_RETRIES`; after the final
attempt the notifier writes a receipt under `QUANTHUS_NOTIFICATION_STATE_ROOT/failures/` and
returns nonzero. A provider outage cannot generate a second email about itself, so the local
failure receipt and systemd journal remain the authoritative evidence for that case.
