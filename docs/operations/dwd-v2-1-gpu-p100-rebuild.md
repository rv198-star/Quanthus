# DWD Base V2.1 GPU-P100 Rebuild Runbook

| Field | Value |
| --- | --- |
| Governing work | `META-DATA-PIT-001` / Issue #78 |
| Governing decision | `docs/architecture/adr-0009-gpu-p100-dwd-v2-1-execution.md` |
| Machine contract | `config/audits/dwd-base-v2-1-gpu-p100-execution.v1.json` |
| Build node | GPU-P100, CPU execution |
| Install and cutover node | CN01 |
| Delivery gate | exact contract approved; implementation and pilots open; full build pilot-gated |

This runbook does not authorize a full builder, installation, or pointer movement. The exact
execution contract is approved, but full build remains blocked until the replay and partition-pilot
receipts are accepted.

## Script Workflow

Repository wrappers call production Python modules and emit structured receipts:

1. `preflight-dwd-v2-1-rebuild` verifies node identity, exact contract and inputs, ext4 mount,
   capacity, filesystem semantics, environment, and the GPU-local lock.
2. `plan-dwd-v2-1-rebuild` freezes one run id, source dispositions, finding coverage, domain order,
   package identity, tool versions, and capacity receipt.
3. `pilot-dwd-v2-1-rebuild` runs bound deterministic replays and representative heavy partitions,
   producing measured memory, thread, and spill limits. It executes two isolated, sequential,
   read-only PyArrow decodes against the sealed financial-reporting and market-observation inputs,
   then pauses at `awaiting-pilot-acceptance`.
4. `run-dwd-v2-1-rebuild` currently executes the authorized preflight and exact-input planning
   phases, publishes durable phase receipts, and pauses at `awaiting-pilot-receipts`. `--resume`
   revalidates immutable inputs before continuing. Nine-domain execution remains unavailable until
   the sealed replay and partition-pilot receipts are explicitly approved.
5. `assemble-dwd-v2-1-candidate` requires nine verified domain receipts and assembles all 33 tables
   on the same ext4 filesystem.
6. `verify-dwd-v2-1-candidate` checks physical integrity, PIT semantics, identities, invariants,
   finding closure, replay, and V2 business-row differences.
7. `seal-dwd-v2-1-export` writes the immutable manifest and publishes `READY` last.
8. `import-dwd-v2-1-candidate` runs on CN01, pulls only manifest-declared bytes into `/personal`,
   deep-verifies them, rechecks local capacity, and prepares hidden local ingress.
9. Existing install and pointer compare-and-swap tools remain separate; cutover is never part of
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

The unit has no restart policy. A node outage or failed pilot requires evidence inspection and an
explicit checkpoint decision; it never resumes itself or opens full-builder authority.

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
