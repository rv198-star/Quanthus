# Low-RAM Sharded Event Execution

- Design status: approved
- Delivery gate: open
- Work item: `PERF-SHARD-001`
- GitHub issue: `#202`
- Parent: `docs/design/lld/06-event-library.md`

## Purpose

Allow the complete P4 Event batch to run on heterogeneous Worker nodes using fixed 3 GiB compute
units while preserving the accepted Event, Label, and Report semantics. The low-RAM path is an
alternative physical execution plan for the same logical Job; it does not create another research
dataset, strategy, policy, or governance identity.

## Resource contract

One resource unit is exactly 3 GiB of cgroup-accounted memory for one isolated analytical shard.
The unit includes the child process, DuckDB, Arrow and charged file cache; it is not a DuckDB
`memory_limit`. A Worker node statically configures its admitted unit count after reserving memory
for the host and Supervisor. Actual concurrency is the minimum of admitted memory units, CPU
capacity, measured storage concurrency, and the Job's configured maximum.

An 8 GiB Worker-only node may pilot two units under a 6 GiB Worker cgroup with swap disabled. A node
that also owns PostgreSQL, Web, API, or unrelated services does not infer two units from physical
RAM alone. Dynamic Worker discovery and resource advertisement remain out of scope.

## Execution graph

The parent Event Job owns one immutable input and one final accepted Run. Its heavy deterministic
stages may create disjoint shard tasks:

1. Direct-DWD physical projections use bounded date partitions and deterministic ordered merge.
2. Candidate decoding and per-security trigger evaluation use complete security-series buckets.
3. All raw triggers return to one global isolation, concentration and split finalizer. These rules
   never execute independently inside a shard.
4. Feature and label work use the same complete-security ownership so rolling and forward windows
   do not cross shard ownership.
5. Final Event, Label, and Report publication retains the existing canonical ordering, manifests,
   content digests, generation fencing, and exactly-one accepted result contract.

Shard assignment is deterministic and content-bound. A retry may replace only the same shard
identity. Partial, stale-generation, failed, or canceled shard output cannot enter finalization.

Direct-DWD output remains a Job-local TMP projection. Its shard plan must reproduce the standard
projection's schema, row count, canonical row order, and every row value. Parquet byte identity is
not a TMP contract because partition boundaries may change physical row groups. This exception does
not apply to governed Event, Label, or Report packages, which remain byte-identical to U16.

## Evidence-first activation

The first gate is an isolated-process benchmark on one node. It proves the algorithm and storage
path scale before PostgreSQL gains parent/shard leases. Passing that gate authorizes only the
smallest Event-specific leasing extension: PostgreSQL remains queue authority and each shard keeps
the existing at-least-once lease and generation fence. Redis, Celery, a generalized scheduler,
Worker registry, cross-Job cache, and new ADM product remain excluded.

The isolated-process gate was accepted on 2026-08-24. Two Direct-DWD pairs reached 2.034x and
2.076x speedup, and two candidate S3x2 runs remained byte-identical to production while completing
below the frozen ceiling. The production leasing and complete package A/B gates remain open.

## Acceptance

The admitted implementation must pass all of these gates twice on the same code, DWD identity,
scope, CPU class, and storage path:

- `S3x1` completes with one 3 GiB unit and swap disabled.
- `S3x2` completes with two 3 GiB units and swap disabled.
- `T(S3x1) / T(S3x2) >= 1.80`, which is at least 90 percent two-Worker parallel efficiency.
- `T(S3x2) <= 1.10 * T(U16)` against the same-node accepted unsplit plan.
- There are no OOMs, cgroup limit hits, Worker restarts, stale publications, or unbounded spill.
- Candidate, executable, not-executable and label counts remain accepted.
- Every file in the Event, Label, and Report package trees is byte-identical to the U16 baseline.

If scaling misses 1.80, the implementation must report the measured serial, storage-contention,
load-imbalance, merge, and coordination costs. The gate is not lowered to justify the design; the
parallel topology remains unaccepted until the cause is removed or the approach is rejected.
