# PERF-SHARD-001 Algorithm Benchmark

- Status: isolated-process gate accepted; production leasing and complete package A/B remain open
- Issue: `#202`
- Node: OCI aarch64, 4 vCPU, 23 GiB RAM
- Direct-DWD image: `quanthus-event:issue202-shard-v7`
- Candidate images: `v7` for both S3x2 runs and the first S3x1 run; `v8` for the second S3x1
  confirmation after correcting only the benchmark entry to use the production no-filter reader
- DWD: `dwd-base-202607-v2.1`
- Correction: `dwd-base-202607-v2.1-correction-20260813-001`
- Scope: history `2011-01-01`, execution through `2021-12-31`
- Swap: disabled in every measured container

## Frozen gates

- One resource unit is a hard 3 GiB cgroup.
- `T(S3x1) / T(S3x2) >= 1.80`.
- `T(S3x2) <= 1.10 * T(U16)`.
- No cgroup max event, OOM, or swap use is accepted.
- Candidate output must be byte-identical to the accepted production `events.jsonl`.
- Direct-DWD TMP projections must retain identical schema, row count, row order, and row values.
- Final Event, Label, and Report package trees remain subject to byte-identical production A/B.

## Direct-DWD result

The accepted low-RAM plan uses 12 deterministic date partitions. The 2011-2020 ranges are annual,
and 2021 is split into two half-year ranges to remove the two-Worker tail. Each non-initial range
scans up to 31 preceding days for `prior_close` but emits only its owned dates. Heavy ranges are
submitted first. Four independent daily-table merges share the same Worker limit and retain
chronological partition order.

| Run | Mode | Elapsed | Peak cgroup | Max/OOM/swap | Result |
| --- | --- | ---: | ---: | --- | --- |
| U16-1 | standard, 16 GiB, 2 CPU | 429.219 s | 15.43 GiB | 0 / 0 / 0 | baseline pass |
| U16-2 | standard, 16 GiB, 2 CPU | 416.336 s | 14.78 GiB | 0 / 0 / 0 | baseline pass |
| 1 | S3x1, 3 GiB, 1 CPU | 834.758 s | 2.28 GiB | 0 / 0 / 0 | pass |
| 1 | S3x2, 6 GiB, 2 CPU | 410.347 s | 4.25 GiB | 0 / 0 / 0 | pass |
| 2 | S3x1, 3 GiB, 1 CPU | 835.802 s | 2.29 GiB | 0 / 0 / 0 | pass |
| 2 | S3x2, 6 GiB, 2 CPU | 402.650 s | 4.23 GiB | 0 / 0 / 0 | pass |

Run 1 speedup is `2.034x` with 101.7 percent two-Worker efficiency. Run 2 speedup is `2.076x`
with 103.8 percent efficiency. S3x2 is respectively 95.6 and 96.7 percent of U16 elapsed time,
below the 110 percent ceiling.

All seven table counts match U16. The three daily tables each contain 8,260,825 rows. Whole-row
XOR and sum hashes match U16, and an ordered 8,260,825-row comparison reports zero row mismatches
for each table. The four smaller tables are byte-identical. The three large TMP Parquet files are
not byte-identical because bounded partition boundaries change Parquet physical encoding; this is
accepted only for the Job-local TMP projection. It does not relax final governed package identity.

## Candidate scan result

The candidate scan uses deterministic LPT security buckets. Each child owns complete security
series and returns raw triggers. The parent alone applies isolation, concentration, split policy,
canonical ordering, and finalization.

| Run | Mode | Elapsed | Peak cgroup | Candidate digest | Result |
| --- | --- | ---: | ---: | --- | --- |
| 1 | S3x1, 3 GiB, 1 CPU | 1109.734 s | 0.37 GiB | `sha256:26fa0b...ffd20c` | pass |
| 1 | S3x2, 6 GiB, 2 CPU | 595.407 s | 5.10 GiB | `sha256:26fa0b...ffd20c` | pass |
| 2 | S3x1, 3 GiB, 1 CPU | 1157.689 s | 0.36 GiB | `sha256:26fa0b...ffd20c` | pass |
| 2 | S3x2, 6 GiB, 2 CPU | 593.791 s | 5.10 GiB | `sha256:26fa0b...ffd20c` | pass |

Run 1 speedup is `1.864x`; run 2 speedup is `1.949x`. Both S3x2 runs remain below the frozen
616.52-second ceiling. They produce 36,820 candidates,
53,527 raw triggers, and every accepted counter unchanged. The observed candidate JSONL digest is
`sha256:26fa0b9907762bb746e0068aefc9066bcb594cbb46b326f9ca3ef84de9ffd20c`, exactly the accepted
production baseline.

## Fail-closed findings

The benchmark rejected four invalid intermediate plans instead of weakening a contract:

1. A temporary-table semi-join and then a plain `IN` subset both allowed DuckDB to change physical
   row order. Explicit canonical subset ordering fixed the issue while retaining the order check.
2. A 2 GB DuckDB budget reached the 3 GiB cgroup ceiling. A 1.5 GB shard budget removed all max
   events.
3. Per-year validation incorrectly required early partitions to contain company actions. Shard
   validation now requires the four date-partitioned tables; the merged result retains the full
   seven-table requirement.
4. Eleven annual tasks and serial merges produced only 1.60-1.75x speedup. Parallel table merge,
   heavy-first submission, and splitting the last year removed the measured serial tail.

## Remaining gate

This evidence authorizes the smallest Event-specific PostgreSQL parent/shard lease extension. It
does not accept `PERF-SHARD-001` as complete. Completion still requires two full production-style
S3x1/S3x2/U16 Jobs with byte-identical Event, Label, and Report trees, unchanged publication
fencing, and no stale or partial result publication.
