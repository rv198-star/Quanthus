# P10 complete Daily delivery and rollback

Parent: [LLD 12](../design/lld/12-permanent-data-line.md).
Decision: [ADR 0016](../architecture/adr-0016-value-proportional-warehouse-quality.md).
Work item: P10-DATA-COMPLETE-DAILY-001. Owner authorized repair on 2026-10-13.
Policy: `config/pipelines/p10-complete-daily.v1.json`.

## Runtime order

| Product/stage | Target and start | Checkpoint | Failure action |
| --- | --- | --- | --- |
| Daily ODS and independent DWD | T, existing daily-core cycle | Daily ODS 23:45 | Existing attempt alerts plus sealed-ODS watchdog |
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
   install/enable complete cycle and independent ODS/minute/DWD checkpoints. Existing
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

## Rollback

Disable the new complete cycle, checkpoint and OCI timers, then let bounded active
operations drain; do not kill an immutable publication midway. Preserve quarantine,
ledger and new packages. Restore the previous data-delivery link (remove only our link
if none existed), exact saved unit states and Compose override/image tags. Recreate
only the three consumer containers once their queues are idle. Existing daily-core
production continues during rollback. Do not repoint old Daily or change sealed Base.
Any new complete pointer/visibility stays for audit but is inaccessible to restored
consumers; a rollback is not a reason to backdate or relabel it. Check independent
daily timers/current and container health again, and record the rollback receipt.
