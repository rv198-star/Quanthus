# P6-BASE-001 Base 2.3 offline Seed engineering — 2026-10-14

Offline design: `../design/lld/08-validation-governance/p6-native-seed-offline.md`.
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

Cloud standard-library tests: 7 source/binding tests passed, 6 Parquet tests skipped
because DuckDB is unavailable. The cloud `make check` passed doctor and compile but
could not start pytest; standalone `make planning-check` passed. Git fetch and package
installation received network-proxy CONNECT 403, including an approved fetch retry.
GitHub connector reads/writes remain available; no proxy bypass was attempted.

An isolated OCI QA checkout at
`/srv/agentdock/worktrees/quanthus-p6-offline-qa-20261014` consumed exact GitHub commits.
It used existing cached Python 3.10.12, DuckDB 1.5.6, PyArrow 25.0.1 and pytest 9.1.1,
without installing dependencies or accessing production data. PyArrow/pytest are outside
the project's declared `<24`/`<9` ranges: these receipts are not the locked CI matrix.

| Check | Actual result |
| --- | --- |
| Seed, reused probe, activation, P6 planning and production-control contracts at `9c4570b284f891ec3910cd435a365826c02ec273` | **37 passed, 10 subtests passed** |
| Six Parquet integration cases within that run | Actual synthetic Parquet build, cold digest reproduction, retained verification/tamper detection, existing P6 candidate reader, per-session PIT versions, late knowledge, duplicates, raw-price basis, missing calendar session |
| Ruff lint on new builder, reused Base23 helper and new tests | Passed |
| Full `make check` at behavior-equivalent `bdcae70cc5b78816839f281006feeaccd0c40343` | **3358 passed, 28 failed, 17 skipped, 364 subtests passed**; no full PASS claim |
| Baseline dual-lane suite on unchanged main `f5b87dab454a719240d56944cb2243ed8765113f` | Same **27 failed**, 13 passed, 3 subtests passed; frozen runtime rejects the cached versions |
| Remaining provider failure, rerun with absolute checkout `PYTHONPATH` | **1 passed**; relative `PYTHONPATH=src` lost the package when the child changed directory |
| GitHub Actions run [38632114137](https://github.com/rv198-star/Quanthus/actions/runs/38632114137) at `9c4570b` | Failed/cancelled, Python job steps `[]`; no code-test verdict |

The dual-lane fixture requires Python 3.10, DuckDB 1.5.5, Polars 1.43.0,
PyArrow 23.0.1 and installed Quanthus 0.1.0.dev0. No frozen runtime, contract or test
was relaxed to accommodate this QA environment. The full-suite skips concern unavailable
external artifacts, PostgreSQL test configuration and Torch. The original Actions log
request returned BlobNotFound; no specific CI service failure cause is asserted.

QA logs under `artifacts/p6-base23-qa/` include `focused-9c4570b.log`,
`make-check.log`, `baseline-dual-lane.log` and `provider-import.log`. The final
format-only change has identical Python AST; final-SHA checks and remote CI receipts
are recorded in #514/#515. Development also caught and fixed canonical JSON float
timings and restored the original frozen production design's exact SHA
`2336837cd5023f2aec475b8185df6a824566a9f1a779d13c477c6b0309cc3f67`.
The new design is an independent document; synthetic results never establish real
Base data quality, source admission or a new official observation.

Real-data next step: provide this cloud task an authorized read-only mount/export of the
declared 2026 subset and reference tables, or authorize the original NexusDock runtime
to read the exact source and run this GitHub-pinned CLI in an isolated output directory.
Do not edit the Base, sealed ledgers, old failed Jobs, frozen gates or timers. Production
deployment and a new 66-session prospective start are subsequent, separately authorized work.
