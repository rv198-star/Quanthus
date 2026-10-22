# P10 container jobs through ODS/DWD completion

Work item: [P10-DATA-OPS-016 / #537](https://github.com/rv198-star/Quanthus/issues/537).
Governing design: [LLD 12](../design/lld/12-permanent-data-line.md).
Design and production switch authorized by Owner 2026-10-22. The active image,
timer switch and natural-run outcome are recorded in the deployment receipt and
work item; image tests alone do not establish natural-run acceptance.

## Boundary

One pipeline image, three explicit one-shot data roles: Daily, recovery and Weekly,
plus separately scoped backup and housekeeping roles using that same image.
Acquisition, ODS/DWD build, API mail, GDrive transport and TMP cleanup all ship
in this image. Roles share a release, not writable source directories.
Daily invokes the existing `daily_base24_cycle` through source acquisition, ODS
catalog, one immutable daily core, native 5m, complete DWD verification and sealing.
It does not stop at provider success. A held 5m source reports partial delivery;
an authoritative closed date reports its event disposition. Neither is full Daily
success. Existing date locks, READY ordering and consumer/PIT gates remain active.

The lightweight external API call runs **inside each data job**, immediately after
its small durable terminal event. There is no standalone mail container/image,
message broker or mail daemon. Mail failure leaves sealed data intact; retry uses
the same image's backup role with a mail-only command and never invokes collection.
The host triggers/observes Docker only and does not read API credentials or send
mail. If Docker cannot start, the host retains an unsent failure event; an
in-container sender cannot run during a daemon outage. Replay that event after
Docker recovers. Strategy/forward-email transport remains outside this work.

Daily/recovery retain the existing host timer clock; no second in-container data
cron is enabled. Only housekeeping has the small internal clock described below.

The initial Weekly entry binds and verifies the already implemented financial
statement domain with its original envelopes/receipts. It reports other domains
held, never complete Weekly acceptance. Industry currently has a separate qualified
snapshot/verifier but no sealed general collection/build CLI in this change; wiring
all Weekly domains and their current acquisition is an outstanding delivery step.
Unsupported nonempty domains are rejected rather than ignored.

## Prepare and build an immutable image

Use a fresh task-owned context outside both source repositories. No untracked
source, host `.git`, credentials, venv, payload or historical run is copied. The
context generator exports only exact tracked `src`, `config` and `pyproject.toml`, plus the versioned
Dockerfile and hash-locked requirements. The separate collector is pinned to
`f839af0033ab4aa8cbe6d9fb933f29db88792e91`. Both code bundles verify their exact file
set, sizes and full hashes. The old host Git-checkout verification remains valid.

```bash
PYTHONPATH=src python3 -m quanthus.pipelines.securities_warehouse.container_code \
  --repo "$QUANTHUS_REPO" --revision "$QUANTHUS_FULL_SHA" \
  --collector-repo "$QUANTHUS_COLLECTOR_REPO" \
  --python-image "$PINNED_PYTHON_IMAGE" --rclone-image "$PINNED_RCLONE_IMAGE" \
  --output "$NEW_BUILD_CONTEXT"
docker build --target pipeline \
  --build-arg PYTHON_IMAGE="$PINNED_PYTHON_IMAGE" \
  --build-arg RCLONE_IMAGE="$PINNED_RCLONE_IMAGE" \
  --build-arg PIPELINE_REVISION="$QUANTHUS_FULL_SHA" \
  --file "$NEW_BUILD_CONTEXT/Dockerfile" "$NEW_BUILD_CONTEXT"
```

`PINNED_PYTHON_IMAGE` must be an actually verified official Python 3.10 slim
reference including its complete registry digest. No mutable default/tag fallback
is provided. `PINNED_RCLONE_IMAGE` likewise requires an actually verified
`rclone/rclone:<tag>@sha256:<full digest>`. Its official static binary is copied
into the pipeline image; there is no separate rclone runtime image/service.
The Owner authorized existing noninteractive sudo for CN02 Docker control.
`sudo -n docker version` has verified daemon access; no user-group or socket-ACL
change is required. Direct registry access timed out, while the existing CN02
loopback proxy can reach the official registry with TLS verification. Freeze and
verify the actual registry manifest and every software blob before building; do
not change the running Docker service or accept an unverified mirror. Registry
access alone does not qualify the pipeline image.

The 2026-10-22 switch audit reproduced a systemd-specific failure: inherited
`NoNewPrivileges=true` prevents `sudo -n` from elevating. The host service template
therefore sets `NoNewPrivileges=false` for the explicitly approved Docker control;
the host Python/TMP owner remains `agentdock`. Every container still has
`no-new-privileges:true`, non-root UID/GID and no capabilities. The launcher checks
its actual `/proc/self/status` before creating state and rejects a conflicting
systemd sandbox. Interactive `sudo` success is insufficient service acceptance.

`ops/data/runtime.lock` pins the 33 runtime packages from the actual CN02 Python
3.10 environment and published wheel hashes, excluding test/development tools. This
locks package artifacts; successful installation/ABI compatibility still requires
the image build and smoke tests. No claim that the unbuilt image is qualified.

## Deployment configuration and paths

Copy and complete `ops/data/deployment.example.json` privately; placeholders fail
closed. Pin the actual image ID (`sha256:...`) or repository digest, full code SHA
and Compose file SHA256. `.env` auto-loading and ambient Docker/Compose selection
variables are excluded. The Compose project is `quanthus-data`; data jobs have a
manual profile and help-only default. Backup has a `delivery` profile, and the cleaner has a `maintenance`
profile; a default `compose up` starts none of these roles.

`docker_access` defaults to `direct` for existing deployments. CN02's example uses
the Owner-approved `sudo-noninteractive` mode: the unprivileged host wrapper invokes
only `/usr/bin/docker` through `sudo -n`, preserving its explicit Compose variables
and selecting `unix:///var/run/docker.sock`. Startup and named-container stop use
the same mode. A sudo denial remains a failed job with retained evidence; there is
no password prompt or automatic privilege fallback. Run the Python wrapper as
`agentdock`, not under sudo: its scratch markers, leases and state must retain the
same UID as the non-root container. ODS/DWD, TMP, state and credentials stay in host
bind mounts; images contain code and runtime dependencies only.

Container configuration is separate from the currently running host configuration.
Its Daily `pipeline_revision` must equal the image's bundled revision and collector
must be `/opt/collector`. Daily preserves the absolute host addresses of Base,
bootstrap history and runtime: existing DATES and bootstrap records contain these
paths. Renaming their mounts to `/data/*` does not rewrite those records. The
separate Weekly plan continues to use its explicit `/data/*` locations.

| Container location | Purpose | Access |
| --- | --- | --- |
| Exact `QUANTHUS_BASE_ROOT` (Daily), `/data/base` (Weekly) | Exact accepted Base DWD | Read only |
| Exact `QUANTHUS_HISTORY_ROOT` | Explicit bootstrap cores at their preserved addresses | Read only |
| Exact `QUANTHUS_RUNTIME_ROOT` (Daily), `/data/runtime` (backup) | Existing Daily root, captures, locks, DATES, outputs | Daily read/write; backup read only |
| `/data/weekly-input` | Exact qualified Weekly source/receipts/config | Read only |
| `/data/weekly-jobs` | New bounded Weekly run parents | Read/write |
| `/run/config/*.json` | Separate pinned role configurations | Read only |
| `/run/events` | Small safe terminal events | Jobs write; delivery reads |
| `/run/mail` | API idempotency receipts | Read/write; persistent |
| Container runtime environment | Existing operational mail API settings via private `env_file` | Daily/Weekly/delivery only; never image layers |
| `/run/backup` | Frozen archives and verified-copy receipts | Backup role only; persistent |
| `/run/secrets/gdrive` | Existing private rclone settings directory | Backup only; token refresh may write |
| `QUANTHUS_PRIOR_ANCHOR` | Empty common-parent namespace and prior descriptors, 16 MiB tmpfs | Temporary; no parent host bind |
| `/data/tmp` | Unique leased run under host `QUANTHUS_TMP_ROOT` | Disposable TMP only |

Set Daily `prior_anchor` and `QUANTHUS_PRIOR_ANCHOR` to the exact common parent of
the three Daily roots. That parent is an empty container tmpfs, with only the
three explicit child bind mounts overlaid. The actual host parent is never bound.
Preflight rejects a broader ancestor, nonphysical/missing mounts, and every
read/write root overlapping a read-only source in either direction. A read-only
alias alone does not protect a source also exposed through another writable bind.
No historical whole-Base copy or mutation of existing DATES is required. Archived
entries outside these exact roots need explicit additional read-only bindings
before their replay; they are not implicitly exposed by mounting the whole parent.
Do not point an unfinished container test at writable live runtime.

The audit rebuilt the same frozen 2026-10-21 ODS/native inputs on the host and in
a network-disabled container. All 16 output Parquet files matched in schema,
row counts and typed row digests (including Decimal and timestamps), and selected
source bytes/production DATES were unchanged. This qualifies that isolated Daily
replay, not natural timer execution or new provider availability.

The initial financial Weekly plan has kind `p10-container-weekly-financial-v1`,
`domains=["financial_statements"]`, `execution_authorized=false`, exact
`code_revision`, `through`, `config`/`config_sha256`, `base`, `source`/`source_sha256`,
`receipts`/`receipt_sha256`, `source_control`/`source_control_sha256`,
`expected_envelopes`, a new unique `job` under `/data/weekly-jobs`, and explicit
`Limits` fields. These limits include retained output, spill, verification scratch,
free-floor, RSS and elapsed budgets. An existing sealed/failed job is preserved;
reuse requires an explicit new plan/disposition, not output overwrite.

`QUANTHUS_MAIL_ENV` names a physical host env file with mode 0600. Compose injects
`QUANTHUS_RESEND_API_KEY`, `QUANTHUS_RESEND_FROM`, `QUANTHUS_RESEND_TO`,
`QUANTHUS_RESEND_TIMEOUT_SECONDS` (1..30), and `QUANTHUS_RESEND_RETRIES` (0..1)
at container creation. The Key is a runtime environment variable, never Dockerfile
`ENV`, a build argument or image content. Use `format: raw` so literal credential
characters are not interpolated. The cleaner explicitly overrides `env_file=[]`
and receives no mail settings. Retain no secret values in resolved-Compose dumps,
inspect output, logs, plans, source bundles or handoffs. Earlier private JSON
sender input remains supported for old independent receipts, not the production
configuration. All five terminal statuses notify; no success-only switch exists.
`QUANTHUS_GDRIVE_CONFIG_ROOT` names an existing private directory containing
`rclone.conf` or the existing `oauth-bridge.json`; only backup receives it. That
directory may be written for token refresh, not mounted as an immutable single file.
The bridge uses the already authorized Duplicati HTTPS refresh endpoint and writes
a short-lived, mode-0600 rclone token file before each bounded transport command.
It never invents an OAuth refresh token or client identity. Transport alone uses
host networking to reach the existing loopback proxy on port 10800, with verified
TLS, no listening ports, no capabilities and no Docker socket. Data roles keep
their ordinary network namespace. Failed direct job-local mail is retried through
this transport role; neither failure recollects data. The existing Hermes notification API settings may be provisioned privately under
the Owner-approved migration. Keep original settings unchanged and exclude every
secret value from images, plans, logs and handoffs.

## Fixed scratch and conservative cleanup

The deployment sets `compose_env.QUANTHUS_TMP_ROOT`, proposed as
`/srv/quanthus-base22-data/tmp/p10`. It must already be a physical directory owned
by the job user, outside every mounted source, output and state/config path in
both ancestor directions. Deployment preflight rejects overlap and symlinks before
creating a run. Each invocation creates a new private `<role>-<date>-<uuid>` child,
writes `.p10-tmp.json`, and holds `.lease` until the job finishes. Compose binds
only that child at `/data/tmp`, with `TMPDIR` and `QUANTHUS_TMP_ROOT` set there.
Large generic temporary files therefore stay on the data disk rather than the
container writable layer or small memory-backed `/tmp`. Existing explicitly
budgeted builder scratch/evidence paths retain their own contracts; they are not
silently relocated or made eligible for automatic deletion by this wrapper.

Versioned policy `p10-success-tmp-72h-v1` retains successful scratch for **72 hours
after completion**. Only terminal `complete` or authoritative `closed` qualifies;
partial Weekly/held minutes, failures, interrupted/running jobs and unknown
directories remain. An active lease always prevents reclamation. The cleaner
rejects links, shared hardlinked files, different-device trees, foreign ownership,
invalid markers/times and scan errors. No path is selected just because it is old.
Host failure leaving an orphan container also leaves its marker running, not
eligible. Failed scratch requires a separate explicit retention disposition.

Preview is the default:

```bash
ops/bin/run-p10-container-job clean-tmp --deployment /etc/quanthus/p10-container-deployment.json
```

The host command now only starts a one-shot **cleanup container**; default is
preview, and `--apply` explicitly enables removal there. The host wrapper itself
does no deletion. Review the preview before activation.

Under the Owner's revised instruction, `cleanup-start --deployment FILE` starts
the same-image `cleanup` service; its container-local clock sweeps at **03:30
Asia/Shanghai**, including one catch-up sweep after restart. `.p10-cleaner.json`
persists the completed slot, and `.p10-cleaner.lock` prevents a duplicate scheduler.
Clock rollback does not repeat a completed slot; an unsuccessful sweep does not
advance its receipt and retries after five minutes. SIGTERM/INT interrupts idle
waiting. This is a TMP-specific clock, not a general workflow scheduler.

The cleaner has exactly one bind, the entire owned TMP root at `/work/tmp`, and
`network_mode: none`. It has no Base, ODS/DWD/Daily/Weekly, secret, notification or
Docker socket mount. It uses the job UID/GID, a read-only root, no capabilities,
128 MiB memory limit and bounded Docker log rotation. Per-job leases remain visible
through the shared TMP inode and still block reclamation. These are configuration
and synthetic guarantees; actual kernel/container mount and stop acceptance is
still required. `cleanup-stop --deployment FILE` stops only this role.

The prior uninstalled host cleanup service/timer examples were removed. The new
service is not started by this task; existing host data timers remain unchanged.
Docker access is needed to launch the cleaner, not for its in-container sweeps.

Original provider captures, `collector-home`, ODS/DWD/Daily/Weekly products, READY,
manifests, failure receipts, job evidence and mail outbox remain outside this TMP
root. Do not point the cleaner at the current Daily root, bulk-import old folders
as disposable, age-delete `/tmp` globally or run blanket Docker prune. Container
`/tmp` and ephemeral `/data` are automatically released with `--rm`; retained logs,
images and governed caches have separate retention scopes.

## Whole data lifecycle: directories and GDrive

The Owner clarified that final ODS/DWD need fixed directories and backup, with
**no automatic deletion of formal products**. These responsibilities share one
versioned deployment, while each role receives only the paths it needs.

| Scope | Fixed root/selection | Retention and access |
| --- | --- | --- |
| Accepted Base DWD | `QUANTHUS_BASE_ROOT` → `/data/base` | Read only; keep |
| Original Daily ODS and acquisition receipts | Existing captures below `QUANTHUS_RUNTIME_ROOT`; select exact source release paths/digests from the cycle, not a latest-directory guess | Persist; never TMP |
| Daily DWD core and final 1d/5m | `QUANTHUS_RUNTIME_ROOT` → `/data/runtime`; exact `core` / `product` / manifest identities in `DATES.json` and immutable run receipts | Persist; keep partial scope explicit |
| Weekly source and products | `QUANTHUS_WEEKLY_INPUT_ROOT` / `QUANTHUS_WEEKLY_JOB_ROOT` | Persist with their domain qualification; no cleanup mount |
| Temporary processing files | `QUANTHUS_TMP_ROOT` | Only scoped successful scratch eligible for reclamation |
| GDrive archive receipts and retry state | Separate backup state, outside TMP and source roots; exact path frozen with backup adapter | Persist independently of data jobs |

Automatic backup policy is now **Base 2.4 Daily and Weekly only**, with `1d/5m`;
Base 2.1/2.2/2.3/2.3.1 are retired from automatic selection. Existing cloud archives
are historical records and are not deleted. Policy:
`config/pipelines/base24-backup.v1.json`. It binds the accepted DWD Base manifest,
not just the version label, and excludes a whole-Base upload from this increment
transport. The old `9c9abe7e...` publisher hard-requires Base 2.3 plus 1m and must
not be connected as a fallback.

`p10_gdrive_backup` consumes an **explicit frozen upstream selection**, never a
latest-directory scan. Required fields are `kind=p10-base24-backup-selection-v1`,
policy SHA256 (bare hex), exact image `code_revision`, `base_version`/`base_manifest_sha256`,
`intervals=["1d","5m"]`, `execution_authorized=false`,
`formal_product_deletion="forbidden"`, the configured `remote_name`, distinct
approved `folder_ids.ODS` and `.DWD`, and at most 32 `packages`. Each package has a
safe unique `id`, `layer=ODS|DWD`, `cadence=daily|weekly`, `business_date`, physical
mounted `root`, complete `files` mapping (relative path → `bytes` and bare
`sha256`), a `qualification_receipt` member, `qualification=qualified_scope|partial_scope`
and retained `limitations` (mandatory for partial scope). `limits` explicitly bind
`max_source_bytes`, `max_archive_bytes`, `free_floor_bytes` and `timeout_seconds`.

The upstream data stage owns semantic qualification and must freeze this selection
from its exact verified receipts. This transport does not establish admission from
a self-declared version or receipt filename. Automatic creation/queueing of that
selection for the current live Daily/Weekly paths, and the real folder IDs, remain
activation prerequisites; no production selection was fabricated here.

Preflight rejects wrong versions/intervals, missing proof, file-set changes,
unsafe paths/links, unreadable trees, state/source overlap and insufficient disk
before creating job state. The backup role sees Daily/Weekly inputs **read-only**,
no Base writable root. It streams each selected source once into deterministic
uncompressed tar while checking its frozen SHA256 and stat identity; it then hashes
the archive for restoration and cloud MD5. Scratch/retained archive space is bounded
before writing; interrupted packs remain evidence and are never overwritten.
Retrying an upload reuses a hash-verified packed archive, not a data rebuild.

ODS and DWD go to their **distinct exact folder IDs**. Below each ID, the proposed
new increment namespace is `<daily|weekly>/base-2.4/<date>/<selection-sha>/<package-id>`;
verify it against the existing layout before activation rather than matching folder
names. Each immutable directory contains `data.tar` and a safe `backup.json` with
the full source inventory, original qualification/limitations, Base identity and
archive SHA256/MD5. It omits host source paths and credentials. Use
[rclone copy](https://rclone.org/commands/rclone_copy/) with `--immutable --checksum`,
then independently list and compare the **exact file set, bytes and MD5** (including
metadata). Missing hashes, extras, duplicates or mismatches prevent the local
`VERIFIED.json`. TLS verification is kept; retries/timeouts are finite and ambient
rclone credentials/options are excluded. No deleting `sync` operation is used.

A data-build success, upload exit zero and cloud-byte verification remain distinct.
`cloud_bytes_verified` never changes consumer admission, upgrades partial Weekly,
or authorizes deleting formal local products. Backup state/failed evidence is not
eligible for the TMP cleaner. Archive retention/compaction is a later explicit
policy, not automatic in this release.

No active CN02 GDrive timer or installed host `rclone` was found during read-only
inspection. The image's rclone and fake-cloud transport tests pass, but the live
adapter is still **prepared**. Automatic selection/queueing of new Daily/Weekly
products, exact folder mapping, supported credential injection and real egress/
upload verification remain open in #537. A retained Duplicati OAuth bridge is not
an rclone config and must not be passed to that adapter as if it were one.

## Operation, checks and rollback

`ops/bin/run-p10-container-job ROLE --deployment FILE [--day YYYY-MM-DD]` starts a
single job. `ROLE=backup` sends only the frozen selection; `ROLE=mail --event FILE`
retries only the retained event using the same-image delivery role. On ordinary
failure/timeout/termination the wrapper attempts to stop only its uniquely named
container and records a data-job failure event. Backup/mail retry termination also
stops its own named container, preserving archives/events, and restores the host's
signal handlers. Daemon/host hard failure can prevent cleanup; natural timer behavior
still needs runtime acceptance. No success is inferred from Docker exit zero
without a matching event.

Keep current 17:30 / 21:15 Shanghai host scheduling. The template
`ops/systemd/cn02-p10-container@.service.example` is not installed or enabled. A
switch must quiesce the original writer, retain its exact unit/drop-ins/config/
environment, and verify the role and image. Use the dedicated container unit;
replacing only the old ExecStart would inherit its sudo-blocking sandbox drop-in.
Do not enable both schedulers. Rollback stops the named new job, retains state/failure evidence, and
restores the prior pinned host entry after verifying no live writer remains.

Before activation: complete image build, import/CLI smoke in a non-root read-only
container, identical-input Daily semantics, supported Weekly semantics, two role
locks, stop/timeout/child cleanup, source/path isolation, mail retry/dedup with a fake
endpoint, immutable backup with fake Drive including interruption/missing hashes,
and actual resource/space measurements. All tests must use disposable
synthetic data or separately approved isolated inputs. Containerizing the current
contract grants no new provider calls, research access, full Weekly admission or
production consumer authority. Record unavailable checks explicitly in the handoff.

## Production triggers and retained rollback

Install the immutable release's `cn02-p10-container@.service.example` as
`cn02-p10-container@.service`, plus the versioned Daily/recovery timers, delivery
service/timer and cleaner service. Daily starts at 17:30 Shanghai and recovery at
21:15. Quiesce and disable only the superseded host timers after verifying no
active writer. Preserve their files, drop-ins, release, environment and config;
record enabled/active states and full hashes before the switch. Do not enable both
sets of data timers. Rollback stops the new timers and any active new writer,
verifies date locks are free, stops the cleaner, then restores the recorded old
timer state. Do not delete products or rewrite DATES during rollback.

Both `OnSuccess` and `OnFailure` trigger the same-image delivery service. Its
15-minute retry timer only drains existing small events and exact qualified date
entries; it cannot collect or change data. Success, partial, closed, busy and
failure all receive API mail, deduplicated against the durable event. A daemon
outage leaves an unsent event and is retried when Docker returns. Preserve an
already written terminal event even when the data container exits nonzero, so
its idempotency identity is not replaced by a second host timestamp.

The deployment's backup config is `kind=p10-base24-delivery-routing-v1`, with
exact `code_revision`, accepted `base_manifest_sha256`, distinct approved ODS/DWD
root folder IDs, `remote_name`, all five `notify_statuses` and explicit transport
limits. `p10_delivery` freezes the matching DATES entry and its market/events,
sealed native acquisition package and DWD product (or independently qualified
core). Native acquisition backup retains the original raw observations together
with adapter/grade evidence; this is not separate consumer admission. Frozen
selections and transport failures are retained, and already verified byte copies
are reused. No latest-directory guess, whole-Base upload, retired-2.3 selection
or automatic formal-product deletion is permitted. A stale date/event manifest
identity is rejected instead of silently selecting a newer revision.

Weekly remains explicitly domain-qualified: the financial entry must have an
exact qualified source/config/receipt plan; it cannot be scheduled with placeholder
inputs or presented as refreshed complete Weekly. Unavailable Weekly sources
block that scope and must not block an independently qualified Daily switch.

## Runtime configuration surface

| Setting | Configuration and bounds |
| --- | --- |
| Operational email API key, sender and recipients | Private runtime `QUANTHUS_MAIL_ENV`; see `operational-mail.env.example` |
| Email timeout and retry count | Runtime env above, bounded independently of data collection |
| Source/history/runtime, Weekly input/output, TMP, backup state and private credential roots | Existing explicit `QUANTHUS_*` Compose variables; physical/disjoint paths validated before launch |
| Non-root data UID/GID | `QUANTHUS_DATA_UID` / `QUANTHUS_DATA_GID`, aligned with mounted host ownership |
| Existing transport proxy | Optional `QUANTHUS_DELIVERY_PROXY`, loopback HTTP only; defaults to CN02's existing port 10800; transport role alone |
| Image and code identity | Exact immutable image ID/full SHA in deployment; no mutable `latest` fallback |
| Cloud destinations, Base identity and write limits | Exact versioned routing/backup contract, not ambient remote/token overrides |
| Business timezone, Daily clock, data-quality and consumer gates | Asia/Shanghai and versioned policies/units; no arbitrary env override |

Docker environment values are inspectable by authorized daemon administrators.
Do not export unredacted `compose config`/`docker inspect` after provisioning.
The checked-in examples contain placeholders only. An empty key is rejected and
never silently falls back to another identity. Success/failure notification tests
must name themselves as channel acceptance tests rather than today's data result.

Existing ODS releases may retain read-only hardlink aliases to the original
collector attempt. A frozen ODS selection can explicitly set
`read_only_source_links=true` only for a `release.json`/READY-qualified package.
The archiver rehashes every byte and compares inode, size, clocks and link count
before/after reading; changing an alias fails closed. It writes only independent
regular tar members, never new hardlinks or symlinks. Other source packages and
all state/config files retain the strict single-link requirement. This handles
existing source representation without making another full copy or weakening
image/runtime independence.

## One bounded native-capture recovery batch

Policy `base24-native-recovery.v1.json` permits one additional native batch per
market date only under the explicit recovery role, after the original one-hour
batch stopped with `DAILY_CYCLE_NATIVE_CAPTURE_INCOMPLETE`. It does not authorize
a third full Daily acquisition. The original date lock, immutable core, exact
market/events manifests, reference digest and complete member hashes are checked
before reuse; failed/mismatched/unknown source members remain rejected.

Copy verified capture bytes and original acquisition receipts into a fresh run as
independent regular files. Completed shards make no provider requests. A paired
partial shard retains its original receipts and requests only missing securities.
A second recovery batch is rejected. Each batch still has two workers, a 3600s
limit and the original resource/source-quality gates. Registration freezes the
policy hash, seed identity and actual remaining scope before new requests. No old
source, receipt, core or failure is overwritten.

This is late recovery, not on-time success: retain the 20:45/21:00 deadline miss,
actual acquisition clocks and `LATE_RECOVERY_NOT_ON_TIME_SUCCESS` in the resulting
receipt. Terminal mail explicitly prefixes the reason with `LATE_RECOVERY_`. A
quality-held source stays partial. Research, PIT and next-open admission are
unchanged. The same existing 21:15 recovery timer can consume this bounded path;
normal Daily never silently extends its intake window.

A failed recovery caused only by up to four empty, pre-query SDK login failures
can perform one login-only repair under `login_only_repair` in the same policy.
It independently copies the completed capture and retains original errors. Its
900-second ceiling is also bounded by the original recovery capture deadline;
there is no additional hour or retry of failed market rows. Initial SDK login
keeps its original responses and performs one paced retry. Worker exceptions
cancel queued work rather than letting a failed executor drain an unbounded queue.
