# P6-BASE-001 Base 2.3 offline Seed engineering — 2026-10-14

Offline design: `../design/lld/08-validation-governance/p6-native-seed-offline.md`.
This is engineering evidence, not source qualification or P6 production acceptance.

Current recovery point: the 2026-10-15 continuation below resolved read access, built
the real input and independently reproduced its **PIT NO_PASS** result. The 2026-10-14
sections retain the earlier evidence; their permission blocker is no longer current.

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

The 2026-10-14 recovery point was to provide an authorized read-only mount/export of the
declared 2026 subset and reference tables, or authorize the original NexusDock runtime
to read the exact source and run this GitHub-pinned CLI in an isolated output directory.
Do not edit the Base, sealed ledgers, old failed Jobs, frozen gates or timers. Production
deployment and a new 66-session prospective start are subsequent, separately authorized work.

## 2026-10-15 authorized real-input continuation

Owner replied “允许” at 05:00 UTC to the precisely scoped UID995 read-only access
request. Before execution, authority and scope were recorded in
[#514 comment 6184821078](https://github.com/rv198-star/Quanthus/issues/514#issuecomment-6184821078).
Existing administrator capability was available; no sudoers or persistent privilege was added.
Selected source children had existing named-user/group ACLs masked to no access. Raising
their masks would widen other principals' effective access, so the authorized alternative
was an independent OCI-local read-only copy, recorded before copying in
[#514 comment 6184895116](https://github.com/rv198-star/Quanthus/issues/514#issuecomment-6184895116).

The new input root is the QA checkout's `artifacts/p6-base23-input-20261015`.
It contains exactly three metadata files and 105 selected Parquet files, 155,128,843 bytes
in total, with original relative paths and matching sealed hashes. Ordinary UID995 verified
all 108 file opens, directory traversal, no member write access and no new other-principal
access. Original source owners, modes, ACLs, size, mtime and inode were identical before
and after. The original Base still denies UID995. `BASE_CURRENT.json` is unnecessary and
was neither copied nor granted. Only 2026 daily bar/state/calendar and all three approved
reference-table subtrees were copied; no 2025 price or minute partition was opened.
This is an access projection, not a new governed data product or source repair.

The source identities listed above were now verified from actual metadata and file bytes.
Selected payload is 104,963,709 bytes:

| Native table | Files | Source rows |
| --- | ---: | ---: |
| dwd_security_bar, 2026/1d | 49 | 932,003 |
| dwd_tradability_state, 2026 | 49 | 974,797 |
| dwd_trading_calendar, 2026 | 4 | 888 |
| dwd_listing, all | 1 | 5,868 |
| dwd_security, all | 1 | 5,868 |
| dwd_adjustment_factor, all | 1 | 139,935 |

Real layout exposed three adapter defects: monthly leaf partitions had been mistaken for
year partitions; nullable interval predicates produced an expensive nested-loop join;
and native calendar `close_at` shadowed the computed policy cutoff. The final code checks
each selected leaf's counts, preserves interval semantics with hashable joins, explicitly
projects calendar columns and selects the version visible at each session close. Known
outside-venue records are excluded from orphan diagnostics; genuinely unknown keys still
block. No source timestamp, cutoff, frozen gate or original data was changed.

Five newly added regression methods passed: monthly leaf inventories; inclusive starts,
exclusive ends and unknown starts; outside-venue versus unknown calendar keys; nullable
native close columns; and a visible old calendar version before a late revision. The final
three ran on OCI at `1299ded6c0654895074e1497f659f056662d4a90` (0.669 seconds).
Changed-file Ruff lint/format, compile and diff checks passed. Previously completed tests
and accepted research batches were not repeated. The earlier full-suite limitations above
remain; this is not a new locked-environment or full-suite PASS claim.

### Real construction, PIT and independent reproduction

Final executable revision: `1299ded6c0654895074e1497f659f056662d4a90`.
Binding digest: `sha256:558dcef9be03dd6b6115e4a3f1884dad10f5a09a5401a8f5ff804f08264509af`.
Both runs used separate processes, new staging/output directories and no artifact cache.

| Result | build-d | build-e-cold |
| --- | ---: | ---: |
| Candidate rows / distinct open sessions | 932,022 / 181 | 932,022 / 181 |
| Source verification seconds | 0.439158 | 0.436883 |
| Shared Seed construction and diagnosis seconds | 13.234545 | 12.748303 |
| Candidate Parquet bytes | 751,719 | 751,719 |
| Input validation / CLI exit | NO_PASS / 2 | NO_PASS / 2 |
| Retained artifact integrity | verified | verified |
| OFFLINE_VALIDATED marker | absent | absent |

Both content digests are
`sha256:8d524c59065a3c83b708f783405f16e2b471e3854669746606aff5216935bf6d`;
both Parquet hashes are
`sha256:3e12bebeb68299c9902fefd20f15b35a4c8f74142c3b3078ed4abf5bba18d514`.
The diagnostic maps, input counts, binding and code identities also match. Manifest hashes
differ because their nonsemantic stage timings differ. Retained verification rebuilt no input.
This proves independent reproduction of an unqualified diagnostic projection, not PIT acceptance.

Final reason counts: all 932,022 candidate rows have no visible bar/state/calendar at their
session close; 727,350 have no visible listing/security identity. Corresponding missing-lineage
counts reflect those absent joins. Calendar-key orphans, duplicate listing/session keys,
empty open sessions and insufficient history are zero. The 42,775 state rows whose calendar
venue is outside SSE/SZSE are excluded. Zero invalid-OHLC/factor counts on unavailable joined
rows do not establish price or factor qualification.

Independent source-clock admission census found every one of 932,003 bars and 974,797 state
records later than its historical session close: knowledge_from ranges from
2026-08-03T23:59:00Z to 2026-10-09T05:51:52.382704Z. All listing/security/factor knowledge
starts are 2026-08-03T23:59:00Z. These warehouse clocks do not prove historical provider
availability and are not backdated. The NO_PASS applies to the pinned per-session PIT use;
it is not a claim that Base2.3 is unusable for every later-as-of purpose. Such a different use
requires an explicit qualified source disposition, not an implicit reinterpretation here.

Intermediate build-a stopped on the monthly-layout mismatch; build-b was stopped before
publication after its nested-loop plan was diagnosed; build-c is superseded diagnostic
evidence with the calendar-column defect. None was an accepted research batch. Their logs
remain alongside the final runs; only build-d/e support the final results above.

### Evidence and remaining input boundary

All paths below are under `/srv/agentdock/worktrees/quanthus-p6-offline-qa-20261014/`.
Run artifacts and market bytes are not committed to Git.

| Evidence | SHA256 |
| --- | --- |
| `artifacts/p6-base23-access-20261015/readonly-input-receipt.json` | `872a3bf38d3ee67a155133fe28ba04acd7e06a428dbc856a25c282c0a2127d77` |
| `artifacts/p6-base23-access-20261015/uid995-verification.json` | `f3070c169b8748a3c991baac0ef5c90808111c8f016ed54aa1bc89814a7dd14d` |
| `artifacts/p6-base23-real-20261015/cold-verification-receipt.json` | `d0d589f63d4514f6e1b993259a4d3c8b16768314cb537eb6b017c2f1bdcdc4e0` |
| `artifacts/p6-base23-real-20261015/source-temporal-census.json` | `7d4125d2598b4cc73de79c53c686db10bcc3a75d525fe6633719078a9e5f4cc6` |

The clock census includes build-c's intermediate timing/digest for provenance; final
construction results come from the cold-verification receipt. Access receipt records the
full selected inventory and before/after metadata; the UID995 receipt records actual opens.

Source qualification is registered as
[P10-DATA-P6-BASE23-001 / #526](https://github.com/rv198-star/Quanthus/issues/526).
Producer admission remains NOT_GRANTED. Existing #516 remains its Base2.2 HOLD fallback.
P6 disposition is to defer the affected historical replay/Seed acceptance and activation,
retain negative evidence, and deliver the completed adapter and offline validation work.
No acquisition, source recovery, timestamp repair or new quality pipeline is undertaken.

The full historical replay contract still requires seven datasets. Qualified source and
consumer mapping are needed for daily_ohlcv_none, daily_universe,
price_limit_execution_daily, security_code_validity and trading_calendar; all are affected
by the current PIT finding. It also requires corporate_actions_normalized and
corporate_action_exceptions, with source/authority/report digests, normalization policy,
matching exception-ledger version/digest and security_fail_closed disposition. Those inputs
are outside this six-table access scope, and no purpose-qualified Base2.3 handoff has been
established. Adjustment factors cannot replace them; old Base2.1 correction authority is not
inherited. This is an unavailable handoff, not evidence that the files globally do not exist.
Accordingly full replay is **NOT_RUN_INPUT_NOT_QUALIFIED**, not a failed strategy experiment.

Executable revision CI [38687659934](https://github.com/rv198-star/Quanthus/actions/runs/38687659934)
failed/cancelled before a code-test verdict; final documentation-head CI is recorded in #514/#515.
Draft #515 stays open and #513 stays separate. No production deployment, activation or ledger
write occurred; this work added zero official observations and does not assert a freshly read
production 0/66 count. Frozen production design retains SHA256
`2336837cd5023f2aec475b8185df6a824566a9f1a779d13c477c6b0309cc3f67`.

Resume from the retained final outputs and #526's exact source handoff. Review its qualified
purpose and new input inventory before expanding access or building a successor replay bundle;
do not rebuild these unchanged diagnostics as progress. Production rollout would separately
require a versioned deployment/activation, the qualified Base-to-Daily bridge, frozen-runtime
checks and rollback, then future eligible sessions and independent reproduction. Those steps
were neither performed nor newly authorized by this offline execution.
