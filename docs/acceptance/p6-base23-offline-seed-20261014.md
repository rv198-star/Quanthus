# P6-BASE-001 Base 2.3 offline Seed engineering — 2026-10-14

Governing design: `../design/lld/08-validation-governance/p6-forward-validation.md`.
This is engineering evidence, not source qualification or P6 production acceptance.

## Decision and recovery point

Owner explicitly instructed continued P6 progress, with Base 2.3 sufficient for the
historical stage; October Daily, minutes and backups are not predecessors. Before edits,
the decision, scope and acceptance were recorded successfully in
[#514 comment 6177175829](https://github.com/rv198-star/Quanthus/issues/514#issuecomment-6177175829).
The runtime boundary was also recorded in
[#511 comment 6177250466](https://github.com/rv198-star/Quanthus/issues/511#issuecomment-6177250466).

Cloud development began clean at main `f5b87dab454a719240d56944cb2243ed8765113f`.
Draft #515's three files at `98b57f4f51ca8a166db34ce6e8cbb2a9aec72ce8` were reused;
#513's path/idempotency and daily-core patch remains separate and unchanged. The native
metadata/path/hash primitives are reused from #523's `native_base23_reader.py` at
`94441f52da65f83df22344f5f956037cb1ad2473`, without importing its P4/P10 consumer grants.

## Source evidence and actual limits

The same #523 revision publishes `config/releases/base23-oci-consumers.v1.json` with:

- manifest `sha256:6991d0c9440d4117c3dfdd3375e2267d0e43174203fc834751c9476cd7598206`;
- content `sha256:f5b1c35e9d60874a5daa8ce36f50d8d4edf62317075e3f223d344a1eb7292608`;
- MATERIALIZATION `sha256:c3c1eed800ea9180498c4bef1c98bf89f1f15d0f400e7dbc74a10a700f401da5`;
- native version 2.3 through 2026-09-30, source `NOT_GRANTED` preserved.

These are repository-published identities, not a new live-data verification receipt.
Via NexusDock, OCI's original #515 worktree was clean at `98b57f4`; no uncommitted
P6 implementation was found there. The retained P6-R1 task is blocked and the earlier
Base probe task is completed. This is task-record evidence, not a current database ledger read.

The OCI directory `/srv/quanthus/dwd-dws-v2.1/base/dwd-base-20260930-v2.3` and
`BASE_CURRENT.json` exist. The current NexusDock `agentdock` account received
`PermissionError` reading the manifest/current pointer. No sudo, SSH, ACL change,
raw data transfer or production write was attempted. Therefore actual Base 2.3 row
qualification, a real Seed build, independent real-data reproduction and the latest
official ledger count remain unverified. The cloud workspace contains no market input.

## Delivered interface

The separate binding pins the source, 2026-01-01–2026-09-30 scope, SSE/SZSE, daily
unadjusted price basis and existing 120-session history requirement. The builder
produces `candidate-daily.parquet` with the existing P6 candidate input columns,
raw Decimal prices, source/knowledge lineage, diagnostics and an immutable manifest.
It emits no signals, fills, PnL, Job, activation, paper ledger or official observation.

Stages share one TMP projection. `build` never replaces an output; `verify` reuses
the sealed output. A separate cold build must have the same semantic content digest.
Bad or unavailable PIT rows are diagnosed, not retimed. A technical validation marker
cannot grant consumer admission. Actual P6 qualification remains open under #514/#516.

Run after placing the exact readable native image in an authorized development mount:

```bash
PYTHONPATH=src python -m quanthus.forward_validation.native_seed build \
  --binding config/forward-validation/seed/base23-offline.v1.json \
  --root /authorized/read-only/base23 \
  --output /authorized/offline/p6-seed-new \
  --code-revision <full-tested-git-sha>
PYTHONPATH=src python -m quanthus.forward_validation.native_seed verify \
  --output /authorized/offline/p6-seed-new
```

Exit 0 means only offline input validation. Exit 2 means blocked/invalid input.
Neither result authorizes production. A fresh output is required for an explicitly
requested independent reproduction; retained successful runs are not rerun as progress.

## Validation and remaining work

Cloud standard-library tests: 7 source/binding tests passed; 5 real-Parquet synthetic
integration tests skipped because DuckDB is unavailable. They cover pinned identities,
unknown versions, protected scope, unauthorized grants, payload mutation/symlinks,
unlisted files, source/output separation and no replacement of completed artifacts.

`make check` actually ran: doctor and compile passed, pytest failed to start because
pytest is not installed. Both GitHub git fetch and package installation were rejected
by the environment network proxy (CONNECT 403), including an approved fetch retry.
GitHub connector reads/writes remain available; no proxy bypass was attempted.
No full-suite PASS or skipped Parquet PASS is claimed. Remote CI results belong to the
exact Draft PR commit and will be recorded in the issue/PR continuation evidence.

Real-data next step: provide this cloud task an authorized read-only mount/export of the
declared 2026 subset and reference tables, or authorize the original NexusDock runtime
to read the exact source and run this GitHub-pinned CLI in an isolated output directory.
Do not edit the Base, sealed ledgers, old failed Jobs, frozen gates or timers. Production
deployment and a new 66-session prospective start are subsequent, separately authorized work.
