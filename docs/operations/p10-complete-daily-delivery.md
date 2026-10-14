# P10 complete Daily delivery and rollback

Parent: [LLD 12](../design/lld/12-permanent-data-line.md).
Decision: [ADR 0016](../architecture/adr-0016-value-proportional-warehouse-quality.md).
Work item: P10-DATA-COMPLETE-DAILY-001. Owner authorized repair on 2026-10-13.
Policy: `config/pipelines/p10-complete-daily.v1.json`.

## Runtime order

| Product/stage | Target and start | Checkpoint | Failure action |
| --- | --- | --- | --- |
| Daily ODS and independent DWD | T, existing daily-core cycle | Daily ODS 23:45 | Service failure hook plus sealed-ODS watchdog |
| Native 1m and 5m ODS/DWD | T, next authoritative trading day 10:30, sealed daily ODS first | 13:00 | Mail; R3 stays quarantined; joint publication held |
| Complete DWD | Immediately after both required minutes qualify and daily DWD exists | 13:20 | Mail; previous complete pointer retained |
| OCI exact complete mirror | Poll every two minutes, advance as soon as source publishes | 14:00 independent watchdog | Mail; previous admitted input retained |

R2 is sufficient; neither missing bars nor unknown turnover becomes fabricated zero.
Daily publication continues independently. Native 5m is a source observation, not a
resample from 1m. Base is unchanged. New complete Daily begins 2026-10-12; this is
not evidence of complete 10/8 or 10/9 minutes. No historical Daily is retagged.
The common reference is TMP, per Job. Published members are immutable ADM; failed
raw attempts stay quarantine. Each interval charges all shard/canary/build attempts
to one persisted 60-minute budget, max 600s per admitted operation. No new source
operations after 13:00. Retry only when a bounded target-date canary really changes.

## Exact paths and permissions

CN01 new complete root: `/srv/quanthus/dwd-dws-v2/daily/products/complete-daily`.
Native minute root: `/srv/quanthus/dwd-dws-v2/intraday/products/native-minute-delivery-v1`.
Job/capture roots: `intraday/operations/complete-daily-v1` and
`intraday/quarantine/complete-daily-v1` below `/srv/quanthus/dwd-dws-v2`.
Existing independent daily root remains `daily/products/daily-core`.

The existing read-only SSH jail remains `/srv/quanthus/dwd-dws-v2/daily`.
Only the new complete export uses root ownership, group `quanthus-dwd-ro`, setgid
0750 directories and 0640 files. No credential, jail, or old product change.
OCI root: `/home/ubuntu/data/quanthus/dwd-complete-daily`; export group `ubuntu`.
Containers mount it read-only at `/data/dwd-complete-daily` and set
`QUANTHUS_COMPLETE_DAILY_ROOT`. API health reports actual admission; the explicit
`CompleteDailySource` SDK reads exact accepted bar files. Frozen event/replay defaults
retain their profiles; this input does not authorize an October historical job.

New mirror environment `/etc/quanthus/oci-complete-daily-sync.env`, mode 0600,
is loaded after existing SSH/mail env files. It contains the existing allowlisted SSH
fields, exact `QUANTHUS_CN01_DWD_REMOTE_ROOT=products/complete-daily`, and
`QUANTHUS_COMPLETE_DAILY_EXPORT_GROUP=ubuntu`. Never print or distribute credentials.

## Deployment sequence and stops

1. Read current SHA, symlinks, service activity, queue state, mounts, disk and dependency
   versions. Keep the original workspaces intact. Stop if in-flight minute or consumer
   jobs cannot finish safely, a required credential/dependency is absent, or reserve <4GiB.
2. After focused tests, independent review and Linux isolated synthetic replay, fetch
   the exact task SHA from GitHub into `/opt/quanthus/data-delivery-releases/<SHA>`.
   Validate clean tracked files. No temporary source tar or whole producer replacement.
3. Save the exact previous unit enabled/active states, links, effective config identities
   and OCI Compose override in a private node-local rollback directory. Save no secret
   values in public receipts. Validate new unit definitions with systemd-analyze.
4. CN01 disable old minute T/Tplus1 and their old minute alert timers only after confirming
   no old minute service is in flight. Point `data-delivery-current` to the new release;
   install/enable complete cycle and independent ODS/minute/DWD checkpoints plus the
   daily-core instance's OnFailure drop-in. The hook reads exact failed target metadata;
   if unavailable it labels the date as an execution date, not an inferred trade date. Existing
   daily-core timers, its 3d74 producer checkout and weekly corrections remain intact.
5. OCI install complete mirror plus independent 14:00 watchdog. Prepare allowlisted env
   and export root. Build the three narrow consumer overlays from pinned preimages;
   verify them without network against synthetic complete products first. After an idle
   queue check, amend only their image tags, new root env and read-only mount. Existing
   Base/P6/current data roots and protected windows remain intact.
6. Read back Git/image SHAs, timer next times, read-only SSH and mounts, health and logs.
   Before 10:30 only schedule/dependency verification is allowed; do not launch early
   full-market collection or fabricate a production complete pointer.
7. First real T+1 requires actual 1m/5m census and R3=0, Base manifest/content binding,
   complete physical proof, identical OCI manifest/content, actual consumer bar read,
   and real knowledge/visibility times. Synthetic replay cannot establish that result.

## Durable operational mail recovery (2026-10-13)

Each failure writes its immutable reason/context request to the node-private
notification `pending/` directory before calling Resend. A transport retry retains
that request and its original stage/date idempotency key; only a successful provider
receipt permits removing it. This does not prove inbox delivery. Temporary transport
failure remains a pending request after all in-call retries are exhausted.

The independent CN01/OCI `complete-daily-mail-retry` timers run every five minutes,
including after reboot. Each state root permits at most four attempts per run; failed
items rotate behind new pending items. These services only send queued mail and do
not acquire sources, rebuild products, reset budgets or consume archive attempts.
CN01 scans both its complete Job and GDrive notification roots; OCI scans its complete
mirror notification root. The sender and retry service use the same node-local mail
environment. Do not print its credentials.

The GDrive service has an independent OnFailure hook, covering errors outside its
per-package handler and systemd timeouts. It takes a target date only from failure
metadata written during the current service run. Otherwise the message explicitly
labels its date as an execution date with an unresolved target.

Deploy both the producer/mirror and separate GDrive notification implementations from
their exact GitHub checkouts, preserving each release's existing data code. Keep daily
ODS, market/minute gates, scheduling, Base, sealed packages and accepted code bindings
unchanged. Drain active mail/archive/mirror processes before switching links. Preserve
the completed 10/12 Job at its original code identity; start the new 10/13 Job normally
on its next trading day. Verify same-day completed pointers by their sealed receipts,
without rerunning their controller using a different code revision.

For rollback, stop the mail-retry timers and wait for their bounded services to finish,
then restore previous code links and exact unit/drop-in state. Preserve pending requests
and provider receipts. Removing the retry service leaves pending mail requiring recovery;
it never permits deleting data or resetting collection/transfer budgets.

## Failed native grading and exact processing-only resume (2026-10-14)

A quarantined original with invalid row identity must not crash comparison with a
valid target-date revision. Both original/revised raw SHA bindings remain enforced;
the selected revision still passes the complete native assessment. Invalid or empty
new observations do not replace valid old bars or justify another supplier pass.

A failed `repair-grade` is not a completed repair. Subsequent scheduled cycles may
retry **only grading of the existing repair capture**, with distinct
`repair-grade-retry-001/002` operation identities. The original receipt and charged
60-minute ledger remain intact; each retry has its own maximum 600 seconds. Source
capture/canary remains prohibited after 13:00. Successful grading with remaining R3
still blocks complete publication. Deadline checks retain late seal/publish times.

The pinned `run-complete-daily-cycle` entrypoint propagates its resolved `ROOT/src`
to the first position in child `PYTHONPATH`, retaining SDK dependency paths. Record
actual child module paths/digests during rollout; a requested Git SHA alone does
not prove which module the child executed.

For a failed Job already pinned to an older code SHA, preserve `code.json` and use
an explicit private JSON authorization selected by
`QUANTHUS_COMPLETE_DAILY_RESUME_AUTHORIZATION`. Its exact v1 fields are:
`kind=p10-complete-daily-processing-resume-v1`, `market_date`, `interval=5m`,
`from_code_revision`, `to_code_revision`, `policy_id`, `reference_digest`,
`failed_operation_digest`, `original_capture_digest`, `repair_capture_digest`,
and `processing_only=true`. Capture digests bind sorted relative paths and SHA256
of all identity files, receipts and raw compressed bytes. The failed operation
digest binds its complete immutable original receipt. Scope is one exact target
and input closure; additional/mismatched fields fail closed.

After an idle-job/lock check, Linux focused tests, independent review, and an
isolated build using these actual frozen inputs, fetch the exact repair SHA from
GitHub, preserve the previous link/config state, and switch only
`data-delivery-current`. Load the scoped resume authorization in a private final
EnvironmentFile for the complete service. No daily-core switch is required. The
controller writes a separate immutable `code-resumes/<SHA>.json` receipt and may
grade existing inputs but cannot capture new sources for the reconciled Job.
Only a physically verified R3=0 pair permits normal complete DWD, OCI and archive
handoff. Preserve original failures; report later recovery with actual times.

Rollback restores the prior data-delivery link and removes only this rollout's
resume EnvironmentFile/drop-in. Preserve old/new Job receipts, captured inputs,
immutable products and pending mail; do not reset budgets or alter source bytes.

## Controller timeout and crash notification

`quanthus-complete-daily.service` has a dedicated OnFailure handler in addition to
the independent 13:00/13:20 watchdogs. The controller writes `active-run.json`
with its systemd InvocationID, code revision, actual target date and stage. It
starts with an unresolved date, then binds the calendar-resolved target; the
stage changes to complete only after minute admission or when checking an
already accepted complete product. A stage change preserves the invocation start.

The failure handler accepts only that current invocation and its bounded start
time. It reuses the existing date/stage mail key with internal failures and the
watchdog. Missing, stale or invalid markers produce an explicitly unresolved
runtime-date minutes alert, never a guessed prior market date. No new supplier
operation, grading, rebuild or budget reset occurs in the hook. Existing queued
mail replay handles transport errors.

Fetch the exact repair commit from GitHub, verify focused Linux tests and unit
definitions, then switch only `data-delivery-current` while the controller and
mail retry are idle. Install the versioned controller failure unit and its scoped
OnFailure drop-in; restore prior timer activity after daemon-reload. Keep the
prior completed Job, processing-resume authorization, quality policy, producer,
archive and OCI identities. A new target Job pins the new code on its ordinary
start. Do not restart an accepted old Job merely to demonstrate the new hook.

Rollback restores the previous data-delivery code link and removes only this
failure unit/drop-in after bounded jobs drain. Retain activity, Job, raw,
notification and publication evidence. Synthetic failure/deduplication tests
and actual runtime path readback do not pre-accept a future natural failure or
tomorrow's minute collection and joint delivery.

## General rollout rollback

Disable the new complete cycle, checkpoint and OCI timers, then let bounded active
operations drain; do not kill an immutable publication midway. Preserve quarantine,
ledger and new packages. Restore the previous data-delivery link (remove only our link
if none existed), exact saved unit states and Compose override/image tags. Recreate
only the three consumer containers once their queues are idle. Existing daily-core
production continues during rollback. Do not repoint old Daily or change sealed Base.
Any new complete pointer/visibility stays for audit but is inaccessible to restored
consumers; a rollback is not a reason to backdate or relabel it. Check independent
daily timers/current and container health again, and record the rollback receipt.
