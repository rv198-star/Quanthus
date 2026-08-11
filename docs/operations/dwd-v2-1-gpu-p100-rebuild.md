# DWD Base V2.1 Rebuild Runbook

This runbook records the R18 GPU-P100 execution and the reusable production workflow. GPU-P100 is
run provenance, not an execution requirement.

## Released Build

| Field | Value |
| --- | --- |
| Release | DWD Base V2.1 Build202607 Release |
| Run | `dwd-v2-1-gpu-p100-20260811T063015Z-5b90cc47-r18` |
| Package | `dwd-base-202607-v2.1` |
| Manifest SHA-256 | `sha256:541c2684b4eb53199e5f09c596c43d1fb37ed5bd8395c686404a95796dece08b` |
| Content digest | `sha256:b689a21b2052ca722af35faeb72cb68850b54d00ddb062b3694e443ba29c32b9` |
| Build revision | `5b90cc47414a3542ab65e04e703554d487cd448d` |
| Scale | 33 tables; 455,817,962 rows; 14,447,400,032 payload bytes |

## Workflow

1. `preflight-dwd-v2-1-rebuild` verifies the clean code revision, canonical build configuration,
   immutable ODS/DWD inputs, ext4 filesystem semantics, capacity, and the local single-writer lock.
2. `run-dwd-v2-1-rebuild` creates the input plan and durable phase receipts.
3. `pilot-dwd-v2-1-rebuild` performs deterministic replay and bounded representative decoding.
4. `build-dwd-v2-1-rebuild` resumes from verified checkpoints, builds nine domains, seals the
   pointerless 33-table candidate, and runs the five independent candidate audits.
5. Success is `candidate-verified` with all five audit digests. It does not modify a current pointer.
6. `promote-dwd-domain-base` deeply verifies and atomically installs the candidate, retains the old
   package, and compare-and-swaps `BASE_CURRENT.json`.

No step checks for GPU-P100 or CN01. The historical contract digest is a reproducibility input for
R18, not a human authorization token. Host-specific systemd files under `ops/systemd/` are deployment
examples; the Python and `ops/bin/` entry points are portable.

## Safety Properties

- Base inputs and completed candidates are immutable and hash-bound.
- A run id is bound to its code revision and input plan.
- Every checkpoint is revalidated before reuse.
- Thread, batch, memory, spill, and free-space bounds fail closed.
- Temporary output is fsynced and atomically renamed.
- Promotion uses a local lock and a SHA-preconditioned pointer compare-and-swap.
- `dwd-base-202607-v2` remains intact as the immediate rollback package.

## R18 Promotion

On the host that contains both the candidate and Base root:

```bash
ops/bin/promote-dwd-domain-base \
  --source /srv/quanthus/gpu-p100-dwd-workspace/runs/dwd-v2-1-gpu-p100-20260811T063015Z-5b90cc47-r18/export/dwd-base-202607-v2.1 \
  --base-root /srv/quanthus/dwd-dws-v2/base \
  --current /srv/quanthus/dwd-dws-v2/base/BASE_CURRENT.json \
  --run-id dwd-v2-1-gpu-p100-20260811T063015Z-5b90cc47-r18 \
  --expected-current-sha256 sha256:<observed-current-file-sha256>
```

The command may be rerun idempotently after success. It rejects pointer drift, changed source
bytes, a conflicting installed package, non-local package paths, and failed deep verification.
