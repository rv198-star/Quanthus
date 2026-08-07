# ADR 0009: GPU-P100 DWD Base V2.1 Execution Topology

- Status: Accepted for implementation and bounded pilots; full build remains pilot-gated
- Date: 2026-08-07
- Decision id: `META-DATA-PIT-001`
- Design status: approved
- Delivery gate: implementation and bounded pilots open; full build blocked on pilot acceptance
- Affects: ADR 0008 section 5, the HLD data-platform topology, and the V2.1 rebuild runbook

## Context

ADR 0008 assumed that CN01 would both build and install DWD Base V2.1. The completed machine audit
showed that CN01 has only two CPUs, about 3.9 GB RAM, and insufficient local installation capacity.
GPU-P100 is an intermittently available WSL2 node with 12 vCPUs, 31 GiB usable RAM, and a dedicated
180 GiB ext4 workspace on host drive D. The current builders are CPU DuckDB, Polars, and PyArrow;
the absent Tesla P100 device is not part of the execution path.

The project owner authorized GPU-P100 as the V2.1 execution node on 2026-08-07 and subsequently
approved the exact replacement contract digest. The approval receipt is
`docs/acceptance/issue-78-dwd-base-v2-1-gpu-p100-execution-approval-20260807.json`. The frozen audit
packet remains immutable historical evidence.

## Decision

- GPU-P100 owns preflight, planning, pilots, domain builds, assembly, verification, and sealing.
- CN01 pulls the sealed candidate through a restricted read-only GPU-P100 export account.
- CN01 retains transfer acceptance, deep verification, local installation, pointer compare-and-swap,
  and rollback authority.
- GPU-P100 uses `/srv/quanthus/gpu-p100-dwd-workspace`; DrvFS/NTFS and the WSL root filesystem are
  forbidden workspace fallbacks.
- GPU-P100 and CN01 use independent local locks. No NFS or cross-host lock is authoritative.
- The build is a versioned, checkpointed script workflow. A node outage never triggers automatic
  failover or implicit resume.
- A Codex App task may inspect receipts hourly and perform only recovery already authorized by the
  active phase. It is not the scheduler and cannot open gates, create a run, install, or cut over.

The approved machine contract is
`config/audits/dwd-base-v2-1-gpu-p100-execution.v1.json`, canonical digest
`sha256:bab98bb0a11dbd2c8395881931cdaed50f915ce2ce39065c25ac103e5ea84d8a`.

## Phase Authority

Until pilot acceptance, authority is limited to implementation, preflight, planning, deterministic
replay pilots, and representative partition pilots. Full build remains blocked until those
receipts are accepted. Candidate installation additionally requires CN01 local capacity; pointer
movement always requires separate approval of the exact installed candidate.

## Consequences

The faster node can perform the expensive CPU work without becoming a production dependency.
Transfers cross a manifest boundary instead of a shared mutable workspace. The additional host
boundary requires a restricted export account, two deep verifications, immutable receipts, and
separate capacity and cutover gates.

The HLD current state is updated by the approval commit. Full build, installation, and cutover
remain controlled by their later gates.
