# DWD Base V2.1 GPU-P100 Rebuild Proposal

| Field | Value |
| --- | --- |
| Governing work | `META-DATA-PIT-001` / Issue #78 |
| Proposed decision | `docs/architecture/adr-0009-gpu-p100-dwd-v2-1-execution.md` |
| Machine contract | `config/audits/dwd-base-v2-1-gpu-p100-execution.v1.json` |
| Build node | GPU-P100, CPU execution |
| Install and cutover node | CN01 |
| Delivery gate | exact contract digest and pilot receipts pending |

This proposed runbook does not authorize a full builder, installation, or pointer movement. It
defines the script and monitoring surfaces that may be implemented before the exact digest gate.

## Script Workflow

Repository wrappers call production Python modules and emit structured receipts:

1. `preflight-dwd-v2-1-rebuild` verifies node identity, exact contract and inputs, ext4 mount,
   capacity, filesystem semantics, environment, and the GPU-local lock.
2. `plan-dwd-v2-1-rebuild` freezes one run id, source dispositions, finding coverage, domain order,
   package identity, tool versions, and capacity receipt.
3. `pilot-dwd-v2-1-rebuild` runs bound deterministic replays and representative heavy partitions,
   producing measured memory, thread, and spill limits.
4. `run-dwd-v2-1-rebuild --run-id ID --resume` executes nine domain builders sequentially and
   skips work only after complete receipt and byte verification.
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

## Hourly Codex App Inspection

The recurring task performs one bounded inspection pass and exits. It treats an offline GPU-P100
as expected availability, not data corruption.

It checks reachability, approved contract digest, active run identity, phase receipts, process and
lock owner, ext4 mount identity, free bytes, memory pressure, spill growth, and recent structured
events. Healthy work is left untouched. On failure it preserves evidence and may apply only an
idempotent recovery already authorized for the active phase. Digest drift, conflicting run ids,
checkpoint corruption, repeated failure, capacity breach, and every import/install/cutover action
require escalation.

The monitor never starts a new run, opens a gate, infers success from free-form log text, resumes
after a node outage, installs a partial candidate, or changes `BASE_CURRENT.json`.
