# DWD Base V2.1 GPU-P100 Rebuild Runbook

| Field | Value |
| --- | --- |
| Governing work | `META-DATA-PIT-001` / Issue #78 |
| Governing decision | `docs/architecture/adr-0009-gpu-p100-dwd-v2-1-execution.md` |
| Machine contract | `config/audits/dwd-base-v2-1-gpu-p100-execution.v1.json` |
| Build node | GPU-P100, CPU execution |
| Install and cutover node | CN01 |
| Delivery gate | contract and pilot receipts approved; full build exact-revision-gated |

The exact execution contract and pilot receipts are approved. This runbook does not itself
authorize a full builder revision, installation, or pointer movement. Full build opens only when
the project owner approves the exact implementation revision and that same revision is placed in
the fail-closed node environment.

## Script Workflow

Repository wrappers call production Python modules and emit structured receipts:

1. `run-dwd-v2-1-rebuild` verifies node identity, exact contract and inputs, ext4 mount, capacity,
   filesystem semantics, environment, and the GPU-local lock; it then freezes one pilot run id,
   source dispositions, domain order, package identity, tool versions, and capacity receipt.
2. `pilot-dwd-v2-1-rebuild` runs bound deterministic replays and representative heavy partitions,
   producing measured memory, thread, and spill limits. It executes two isolated, sequential,
   read-only PyArrow decodes against the sealed financial-reporting and market-observation inputs,
   then pauses at `awaiting-pilot-acceptance`.
3. The exact pilot receipt approval is frozen in
   `docs/acceptance/issue-78-dwd-base-v2-1-gpu-p100-pilot-approval-20260808.json`.
4. `build-dwd-v2-1-rebuild` creates a distinct revision-bound full-build run. It verifies the
   accepted pilot approval and source receipts, repeats the deep preflight under `full_build`
   authority, freezes a new plan, executes the nine-domain DAG sequentially, and deeply verifies
   every immutable checkpoint before continuing.
5. The same full-build runtime hard-links the nine verified domain candidates into a 33-table
   export, writes its manifest and `READY`, and deeply verifies it. It then runs five checkpointed
   successor-candidate audits: physical schema and partitions, semantic contracts, domain
   invariants, coverage and exclusions, and lineage authority. Only five sealed `verified` reports
   advance the run to `awaiting-candidate-approval`; a finding or report drift fails closed. The
   runtime calls the pointerless seal API and cannot create or change a `CURRENT` descriptor.
6. `import-dwd-v2-1-candidate` runs on CN01 after separate implementation and authorization. It
   pulls only manifest-declared bytes into `/personal`,
   deep-verifies them, rechecks local capacity, and prepares hidden local ingress.
7. Existing install and pointer compare-and-swap tools remain separate; cutover is never part of
   the rebuild phase chain.

Each phase writes temporary output, fsyncs files and parent directories, verifies the result, then
atomically publishes a receipt. Resume revalidates the run id, code revision, input manifests,
audit/remediation/contract digests, phase cursor, and output hashes. Any mismatch fails closed.

## Pilot Limits And Invocation

The GPU-P100 pilot defaults are frozen at one worker, 12 GiB address-space limit, four threads,
256 decoded rows per batch, and 8 GiB maximum spill per execution. `TMPDIR` and all supported engine
temporary-directory variables are pinned below the run's ext4 `scratch/pilot` directory. No local
scratch fallback, Base mutation, builder output, network input, or concurrent worker is allowed.

Run the pilot only after `run-dwd-v2-1-rebuild` has produced a sealed
`awaiting-pilot-receipts` phase for the same revision and run id:

```bash
ops/bin/pilot-dwd-v2-1-rebuild \
  --repository /home/rv192/work/Quanthus-issue78-audit-closure \
  --workspace /srv/quanthus/gpu-p100-dwd-workspace \
  --ods-base /srv/quanthus/ods/base/ods-base-202607-v2 \
  --dwd-base /srv/quanthus/dwd-dws-v2/base/dwd-base-202607-v2 \
  --environment /etc/quanthus/gpu-p100-dwd-rebuild.env \
  --run-id "$RUN_ID" \
  --expected-revision "$REVISION" \
  --expected-contract-digest "$CONTRACT_DIGEST" \
  --python /home/rv192/work/Quanthus-issue78-audit-closure/.venv-dwd-rebuild/bin/python
```

Success is a pause, not builder authorization. The two approval inputs are the canonical digests in
`evidence/pilot/replay-determinism.json` and `evidence/pilot/partition-pilot.json`.

Install and start the persistent one-shot unit after placing the approved revision and run id in
`/etc/quanthus/gpu-p100-dwd-rebuild.env`:

```bash
sudo install -m 0644 ops/systemd/quanthus-dwd-v2-1-pilot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start quanthus-dwd-v2-1-pilot.service
```

The pilot unit has no restart policy. A node outage or failed pilot requires evidence inspection
and an explicit checkpoint decision; it never resumes itself or opens full-builder authority.

## Full Build Service

After the exact full-build revision is approved, install
`ops/systemd/gpu-p100-dwd-full-build.env.example` as the node environment and replace its revision
and run placeholders. The new run id must contain the first eight characters of the approved
revision. Keep `QUANTHUS_DWD_RESUME_AUTHORIZED=0` for the first start.

Install and start the bounded service:

```bash
sudo install -m 0644 ops/systemd/quanthus-dwd-v2-1-full-build.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start quanthus-dwd-v2-1-full-build.service
```

The service is sequential, network-isolated, limited to four CPUs and 14 GiB, and has
`Restart=no`. All temporary directories are pinned below `runs/<run-id>/scratch/full-build` on the
ext4 workspace. Build and audit checkpoints allow an explicitly authorized later invocation to
continue without repeating verified domains or completed audit tables. Normal success is the
paused `awaiting-candidate-approval` phase with five candidate-audit digests in its receipt, not
installation.

After an interruption, inspect the immutable failure and last-passed receipts. Resume only after
an explicit checkpoint decision by setting `QUANTHUS_DWD_RESUME_AUTHORIZED=1` and starting the
service again. The runtime re-runs deep preflight, rejects drift in any completed checkpoint, and
continues at the first unfinished source audit, domain, or candidate audit. Set the flag back to
`0` after the recovery invocation.

## Fixed Control Paths

| Role | Path |
| --- | --- |
| GPU workspace | `/srv/quanthus/gpu-p100-dwd-workspace` |
| GPU build lock | `/srv/quanthus/gpu-p100-dwd-workspace/control/locks/dwd-base-v2-1-rebuild.lock` |
| CN01 import/install lock | `/srv/quanthus/dwd-dws-v2/v2.1-rebuild/locks/import-install.lock` |
| CN01 transfer landing | `/personal` under a manifest-bound run directory |
| CN01 hidden ingress | `/srv/quanthus/dwd-dws-v2/base/.incoming-dwd-base-202607-v2.1` |
| CN01 installed package | `/srv/quanthus/dwd-dws-v2/base/dwd-base-202607-v2.1` |
| CN01 active pointer | `/srv/quanthus/dwd-dws-v2/base/BASE_CURRENT.json` |

## Hourly Inspection Layers

The GPU-P100 systemd timer performs one bounded local inspection pass and exits. It treats an
offline GPU-P100 as expected availability, not data corruption; no timer runs while the node is
offline.

It checks reachability, approved contract digest, active run identity, phase receipts, process and
lock owner, ext4 mount identity, free bytes, memory pressure, spill growth, and recent structured
events. Healthy work is left untouched. On failure it preserves evidence and may apply only an
idempotent recovery already authorized for the active phase. Digest drift, conflicting run ids,
checkpoint corruption, repeated failure, capacity breach, and every import/install/cutover action
require escalation.

The monitor never starts a new run, opens a gate, infers success from free-form log text, resumes
after a node outage, installs a partial candidate, or changes `BASE_CURRENT.json`.

GPU-P100 runs `quanthus-dwd-v2-1-monitor.timer` once per hour and writes atomic snapshots below
`control/monitor`. The timer is non-persistent: after an offline interval it performs one normal
post-boot inspection instead of replaying missed checks. Codex App may later inspect the same
snapshots by connecting directly from OCI to GPU-P100; that supervisory task does not replace or
control the systemd timer.
