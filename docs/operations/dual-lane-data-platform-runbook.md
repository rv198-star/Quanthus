# Dual-Lane Data Platform Runbook

This runbook operates the shadow and future cutover path accepted by
`docs/architecture/adr-0005-dual-lane-canonical-and-ads-publication.md` for
`META-DATA-PIPE-003`. The delivery gate remains blocked for production cutover. Until an explicit
`GO_FOR_CUTOVER` record exists, the legacy OCI full-ODS pipeline remains authoritative, all new
units stay on separate roots, and no consumer is pointed at the target release set.

## Fixed deployment shape

| Node | Service identity | Data authority | Scheduled action |
| --- | --- | --- | --- |
| OCI | `ubuntu` | epoch, arbitration, target publication | epoch 16:30; arbitration 20:00 Asia/Shanghai |
| CN01 | `root:quanthus-candidate-ro` | fixed-primary candidate only | candidate 19:50 Asia/Shanghai |
| Hermes | `root:quanthus-candidate-ro` | standby candidate only | candidate 19:50 Asia/Shanghai |

GitHub is the only code-distribution path. Each node fast-forwards its own checkout to the same
reviewed commit. Do not rsync repository files, virtual environments, configuration, or credentials
between nodes. Data transfer uses separate forced-command identities for epoch ingress and
candidate export; neither identity has canonical publication authority.

The candidate service remains root because its existing ODS source is below `/root/work/astock`.
The two rrsync jails do not live there: an unprivileged forced-command account cannot traverse the
node's mode-0700 `/root`. Both candidate nodes therefore use these fixed exchange roots:

```text
/srv/quanthus/dual-lane/epoch-incoming  quanthus-epoch-in:quanthus-epoch-in 0750
/srv/quanthus/dual-lane/epoch-staging   root:root                             0700
/srv/quanthus/dual-lane/epochs          root:root                             0750
/srv/quanthus/dual-lane/candidates      root:quanthus-candidate-ro            0750
```

`quanthus-epoch-in` may overwrite its untrusted ingress staging but cannot write the sealed epoch
root. Before candidate construction, the root service verifies the ingress manifest and files,
copies them into a new staging directory, fixes ownership/modes, atomically promotes the immutable
child, and only then advances the sealed date-local `CURRENT`. `quanthus-candidate-ro` may read but
cannot write the candidate jail. OCI uses a different key for each direction and node. Client-side
remote roots are `.` because each forced command is jailed directly at its dataset root.

## Prepare CN01 and Hermes

Run the same account and directory setup locally on each candidate node. These are dedicated
locked-password service accounts; `/bin/bash` is present only so sshd can execute the forced
command. No authorized key may omit the restrictions below.

```bash
sudo groupadd --system quanthus-epoch-in
sudo useradd --system --gid quanthus-epoch-in --create-home \
  --home-dir /var/lib/quanthus-epoch-in --shell /bin/bash quanthus-epoch-in
sudo passwd --lock quanthus-epoch-in
sudo install -d -o quanthus-epoch-in -g quanthus-epoch-in -m 0750 \
  /var/lib/quanthus-epoch-in

sudo groupadd --system quanthus-candidate-ro
sudo useradd --system --gid quanthus-candidate-ro --create-home \
  --home-dir /var/lib/quanthus-candidate-ro --shell /bin/bash quanthus-candidate-ro
sudo passwd --lock quanthus-candidate-ro
sudo install -d -o quanthus-candidate-ro -g quanthus-candidate-ro -m 0750 \
  /var/lib/quanthus-candidate-ro

sudo install -d -o root -g root -m 0755 /srv/quanthus /srv/quanthus/dual-lane
sudo install -d -o quanthus-epoch-in -g quanthus-epoch-in -m 0750 \
  /srv/quanthus/dual-lane/epoch-incoming
sudo install -d -o root -g root -m 0700 /srv/quanthus/dual-lane/epoch-staging
sudo install -d -o root -g root -m 0750 /srv/quanthus/dual-lane/epochs
sudo install -d -o root -g quanthus-candidate-ro -m 0750 \
  /srv/quanthus/dual-lane/candidates
```

If an account already exists, inspect its uid, primary group, home, shell, password-lock state, and
authorized keys instead of recreating it. Account or path drift is a failed preflight.

Install one or more rotation-safe keys in each account's mode-0600 `authorized_keys`; every active
line must retain the exact command and all restrictions:

```text
no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,no-X11-forwarding,command="/usr/bin/rrsync -wo -no-del /srv/quanthus/dual-lane/epoch-incoming" ssh-ed25519 EPOCH_PUBLIC_KEY
no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,no-X11-forwarding,command="/usr/bin/rrsync -ro /srv/quanthus/dual-lane/candidates" ssh-ed25519 CANDIDATE_PUBLIC_KEY
```

The epoch line belongs only to `quanthus-epoch-in`; the candidate line belongs only to
`quanthus-candidate-ro`. Do not add a shell key, reuse a Git deploy key, put both commands on one
account, or grant the candidate account group write permission.

Deploy the reviewed commit from GitHub and create the dedicated P1 runtime:

```bash
cd /root/work/Quanthus
git fetch origin main
git pull --ff-only origin main
make bootstrap-p1 VENV=.venv-p1 PYTHON=python3
```

Copy `ops/systemd/dual-lane-node.env.example` to a node-local temporary file, set the full deployed
commit and the correct lane/ODS root, and install it without committing it. Hermes uses its shadow
ODS release root; CN01 uses its own CN01 release root.

```bash
sudo install -o root -g root -m 0640 /tmp/dual-lane-node.env \
  /etc/quanthus/dual-lane-node.env
sudo install -o root -g root -m 0644 \
  ops/systemd/quanthus-dual-lane-candidate.service \
  ops/systemd/quanthus-dual-lane-candidate.timer \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl disable quanthus-dual-lane-candidate.timer
sudo ops/bin/dual-lane-doctor node
```

The doctor must name `dual-lane node deployment preflight OK`. It verifies the exact Git commit,
clean tracked checkout, `.venv-p1`, policy, real non-symlink jail paths, users/groups, ownership,
modes, rrsync capabilities, and every active forced-command key. It changes no state.

## Prepare OCI

Keep all target roots beside, not inside, legacy data and pointer trees:

```bash
sudo install -d -o ubuntu -g ubuntu -m 0750 \
  /home/ubuntu/data/quanthus/dual-lane/epochs \
  /home/ubuntu/data/quanthus/dual-lane/incoming \
  /home/ubuntu/data/quanthus/dual-lane/publication
```

Deploy the same reviewed commit through OCI's own GitHub checkout. Install four distinct private
keys, pinned known-hosts files, and a node-local environment derived from
`ops/systemd/dual-lane-oci.env.example`. Private keys are owned by `ubuntu` with mode 0400 or 0600.
The two remote roots for each node remain `.`; do not replace them with host paths.
`QUANTHUS_DUAL_LANE_SECURITY_MASTER` must name OCI's regular, non-symlink
`ref_security_master.parquet`; epoch publication copies and checksums that exact snapshot so both
lanes build code-validity evidence from one shared reference rather than their lane-local copies.
The publisher rejects the epoch before `READY` when the id derived by the wrapper no longer matches
the sealed pointer, copied parent/base, policy, universe, code-validity, and security-master inputs.
Legacy pre-shared-reference epochs and candidates remain readable for audit and must not be reused
for a new build or arbitration attempt.

```bash
sudo install -o root -g ubuntu -m 0640 /tmp/dual-lane-oci.env \
  /etc/quanthus/dual-lane-oci.env
sudo install -o root -g root -m 0644 \
  ops/systemd/quanthus-dual-lane-epoch.service \
  ops/systemd/quanthus-dual-lane-epoch.timer \
  ops/systemd/quanthus-dual-lane-arbitration.service \
  ops/systemd/quanthus-dual-lane-arbitration.timer \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl disable quanthus-dual-lane-epoch.timer \
  quanthus-dual-lane-arbitration.timer
sudo ops/bin/dual-lane-doctor oci
```

The OCI doctor verifies the commit/policy, target-root ownership, separate users and keys, key
modes, pinned host files, the canonical parent/base selection, the shared security-master input,
and jail-relative remote roots. It does not contact either node and therefore does not prove the
reverse tunnel or end-to-end rrsync path.

## Copy-only migration order

Migration creates new artifacts and pointers only under the target roots. It does not move,
rename, overwrite, or delete legacy ODS, OCI mirrors, DWD/DWS, ADS, logs, or pointers.

1. Record the legacy OCI timer state, active service state, current pointer bytes/checksums, deployed
   commits, and free space. Do not stop the legacy timer.
2. Fast-forward all three Git checkouts to one reviewed commit and build each node's local runtime.
3. Create the two new jails on CN01 and Hermes and the three target roots on OCI.
4. Install environment and unit files disabled. Run the applicable doctor on all three nodes.
5. Rehearse epoch transfer, both candidate builds, OCI pull/arbitration, and target release-set
   publication for an explicit market date.
6. Compare target evidence with the legacy result. Confirm the byte/checksum of every legacy
   consumer pointer is unchanged by the shadow run.
7. Enable target timers for bounded shadow observation only after the manual rehearsal passes.
   Target publication remains under `/home/ubuntu/data/quanthus/dual-lane/publication` and is not a
   consumer authority before cutover.

Never seed a target root by moving a legacy directory. When historical evidence is needed, copy it
to a new staging path, verify the source and destination manifests/checksums, and retain both. The
portable ADS layout is discovered through its manifest; operators must not create or consume a
calendar-year directory convention outside that manifest.

## Manual scheduled-path rehearsal

Use an open market date whose ODS releases are complete on both nodes. Run each command on its
owning node and preserve unit journals plus manifest/checksum evidence.

At or after 16:30 Asia/Shanghai on OCI:

```bash
sudo -u ubuntu /opt/quanthus/current/ops/bin/run-dual-lane-epoch-publish 2026-07-29
```

At this point each node has only an untrusted `epoch-incoming/<date>` copy. Confirm its referenced
artifact and date-local `CURRENT.json` arrived in content, immediate-child `READY`, then `CURRENT`
order. A partial transfer must expose neither ingress `READY` nor ingress `CURRENT`. Do not call the
ingress copy sealed or candidate-visible.

After both nodes have complete ODS and at or after 19:50, run locally on each candidate node:

```bash
sudo -u root -g quanthus-candidate-ro \
  /root/work/Quanthus/ops/bin/run-dual-lane-candidate-build 2026-07-29
```

The candidate launcher first invokes `promote-dual-lane-epoch`: it verifies ingress, promotes a
root-owned immutable epoch child, advances only the sealed date-local `CURRENT`, and then builds
against that sealed descriptor. Confirm the ingress account cannot modify the sealed tree and the
candidate export account can read every file in the completed candidate tree.

At or after 20:00 on OCI:

```bash
sudo -u ubuntu /opt/quanthus/current/ops/bin/run-dual-lane-arbitration 2026-07-29
```

Verify the candidate checks, arbitration reason, accepted generation, operational ADS, release-set
`READY`, target `release-set_CURRENT.json`, and publication event from their manifests. Also verify
that all files referenced by the target release set resolve beneath the target publication root.
Do not infer success from a `CURRENT.json` file alone.

## Qualification and failure drills

Freeze calibration thresholds before qualification and keep the qualification sessions disjoint.
For each drill, retain commands, exact input/candidate/epoch ids, manifests, reason codes, pointer
checksums before and after, timestamps, resource usage, and alert evidence.

- Interrupt epoch content, immediate-child `READY`, and `CURRENT` transfers separately. A receiver
  may see only verified immutable content followed by `READY`, then `CURRENT`; interruption cannot
  delete or replace an existing sealed file. Also overwrite ingress content deliberately and prove
  that the unprivileged account still cannot alter the promoted epoch tree.
- Withhold CN01, then Hermes. Exercise the frozen primary-failure and standby-absence budgets; a
  missing lane is not silently equivalent to an independently failed lane.
- Inject stale epoch, parent/reference mismatch, clock drift, invalid candidate, both-failed, and
  unattributed material divergence cases. None may advance the target pointer except a policy-valid
  Hermes fallback after an independently established CN01 failure.
- Repeat and overlap pull/arbitration attempts. The shared publisher lock and immutable identities
  must yield one acceptance and one successful release-set compare-and-swap.
- Stop ADS build after acceptance and at each release write stage. Consumers must retain the old
  complete release set; retry must be idempotent.
- Rebuild ADS from retained canonical inputs with both candidate nodes unavailable. Restore the
  exact accepted ODS rebuild basis after simulated loss of each producer according to the reviewed
  retention matrix.
- Rehearse target-to-legacy rollback while retaining all target and legacy evidence.

Passing unit tests or one successful day is not `GO_FOR_CUTOVER`. The acceptance record must also
cover the retention matrix, alerts, resource budgets, correction horizon, rollback window,
multi-session qualification, consumer migration, and legacy/target single-writer fencing required
by ADR 0005 and Issue #45.

## Cutover and rollback

Production cutover remains prohibited until the reviewed evidence records the exact phrase
`GO_FOR_CUTOVER` for `META-DATA-PIPE-003`. Immediately before cutover, rerun all three doctors,
confirm no publisher service is active, archive timer/unit/environment checksums without secrets,
and verify the target release set plus rollback candidate.

The future cutover order is fail-closed:

1. Disable and quiesce the legacy timer. The helper requires both explicit tokens and retains the
   service files and all data:

   ```bash
   sudo /opt/quanthus/current/ops/bin/disable-legacy-oci-publisher \
     --confirm GO_FOR_CUTOVER --decision META-DATA-PIPE-003
   ```

2. Verify the legacy service is inactive and its timer disabled. If it is still active, stop the
   cutover and wait or roll back; never enable overlapping publishers.
3. Confirm target arbitration uses the same fenced publisher lock and enable the target publisher.
4. Move consumers atomically to the sole target `release-set_CURRENT.json` only through the
   separately reviewed consumer migration. Dataset-specific pointers cannot be selected
   independently.
5. Preserve the prior complete release set, legacy timer/unit files, and all old/new evidence for
   the full rollback and correction horizons.

For rollback, first disable and quiesce target arbitration, then select the reviewed prior complete
non-revoked release set or restore the legacy consumer path, and only then re-enable the legacy
timer. Record `ROLLED_BACK` with exact pointer and unit evidence. Never run both publishers to
reduce downtime.

## Cleanup prohibition

This runbook grants no retention cleanup authority. Initial migration and cutover are copy-only.
No ODS release, candidate, canonical record, OCI mirror, ADS revision, research bundle, log,
staging directory, unit, or pointer may be deleted or repurposed because the target path passed.
Future cleanup requires a separate reviewed inventory, reference/descendant check, expired
rollback and correction windows, failure-independent copy evidence, and successful restore drills.
