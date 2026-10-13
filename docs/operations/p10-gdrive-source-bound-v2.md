# GDrive source-bound archive v2

The first complete package containing reused financial sources is 1,513,117,466
bytes; its actual deterministic tar.zst is 1,026,944,259 bytes. The original
600-second command timeout cannot upload this artifact over observed CN01 egress.

Archive policy v2 uses the separate `source-bound-v2` remote namespace under the
existing complete-Daily archive folder. Old cloud objects and v1 metadata remain
immutable. Preserve an interrupted v1 run before activating v2; recreate the v2
archive metadata from the unchanged qualified package, with exact source hashes.
Local runs, delivered receipts and failure queues use the separate
`/var/lib/quanthus/gdrive-complete-v2` state root. The service failure hook uses
this root; mail replay retains both v1 and v2 queues plus complete-Daily and
acquisition queues. Existing OAuth/proxy credentials stay at their existing
`/etc/quanthus/gdrive-complete-v1` configuration path.

Resource budgets are explicit: one package per run, 7,200 seconds per command,
7,500 seconds per route, 15,000 seconds per job, and a 255-minute systemd timeout.
US.Stlaure remains first and US.WePC remains the fallback. The node retains the
4 GiB reserve and 4 GiB package/archive limit. The existing per-date attempt
limit and durable failure-mail queue remain active.

This is an engineering transfer budget. Joint source admission, R2 minimum,
R3=0, immutable SHA256, remote size/MD5 and CLOUD_READY readback remain required.
GDrive archival is independent of daily construction and OCI's 14:00 visibility
deadline. Successful local archive staging does not imply successful upload.
