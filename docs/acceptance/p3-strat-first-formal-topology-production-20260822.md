# P3-STRAT-002 First Formal Topology Production Acceptance (2026-08-22)

| Field | Value |
| --- | --- |
| Status | accepted for the bounded development engineering proof |
| Work item | `P3-STRAT-002` / Issue #149 |
| Code revision | `f81328f` |
| Production Worker Job | `job_caf70a06322b4c219d35f5719a9ce131` |
| Isolated reproduction Job | `job_917c0f57b020456e892102ed94574bed` |
| Expert Run | `expert_run_83f1c67b570387deb12f53ccd0c7b452ea1923905e11effdbc29a74825fde09c` |
| Run manifest digest | `sha256:83f1c67b570387deb12f53ccd0c7b452ea1923905e11effdbc29a74825fde09c` |
| Input digest | `sha256:ce976339847819dc0e4c9a5ff4f22a71b0ed7e452a134160a9ce071269a5a373` |

## Accepted bindings

The accepted strategy is exactly `candidate.p4-trend-risk@1.0.0` at semantic digest
`sha256:b0a37af05f14bf9cd23e4bf3fe22b546d88646dbad5696f4700dcfd7204a0716`.
Its executable topology contains exactly two active experts:

- `expert.trend-stability@1.0.0` is the sole positive entry scorer;
- `expert.position-risk-review@1.0.0` is a policy-only risk expert and supplies no independent
  positive entry authority.

The topology retains the seven system guardians declared by the approved card and binds the two
single fallbacks plus the required joint fallback. The Worker input binds DWD Base
`dwd-base-202607-v2.1`, correction
`dwd-base-202607-v2.1-correction-20260813-001`, and exact parent Event Run manifest
`sha256:8b9d184c2947b82d1a98440e7e6313e3659735fa352ea5dd72ec4b90fc80050c`.
The Sentinel reads only the January 2020 development window. Arbitration and permanent-blind data
remain sealed.

## Sentinel accounting

The production PostgreSQL Replay Worker completed in 262.302464 seconds. It evaluated all 267
January events, with 259 applicable assessments and eight explicit abstentions. The formal action
result contains 104 `trial_entry` decisions and 163 `no_action` decisions. The reason ledger is:

| Reason | Count |
| --- | ---: |
| `entry_score_threshold_met` | 104 |
| `block_new_veto` | 82 |
| `entry_score_below_threshold` | 73 |
| `all_scoring_abstained_block_new` | 8 |

The scorecard explicitly records `economic_evidence=false` and
`sealed_partition_access=false`. This Sentinel proves executable topology and audit behavior; it
does not claim economic effectiveness or authorize strategy admission, Shadow, notification, or
execution.

## Resource correction and containment

Four earlier submissions stopped before Run publication while the bounded implementation was
corrected. The final producer is
`producer.formal-p4-trend-risk-development@1.0.4` with policy
`literal-pushdown-materialized-source-projection-v4`. It places the event-security set directly in
Parquet predicates, applies the Sentinel end-date predicate to daily sources, materializes bounded
sources, and retains DuckDB's 10 GiB / two-thread execution limits and disposable spill directory.

The Replay container retains a 16 GiB hard memory and memory-plus-swap limit. Its 14 GiB proactive
guard uses the cgroup working set, `memory.current - inactive_file`, so reclaimable Parquet file
cache is not treated as unreclaimable process memory. Event and Replay heavy Jobs remain serialized
through PostgreSQL. Failed and canceled submissions published no Run and did not access a sealed
partition.

## Determinism and artifact verification

The sixth authorized submission ran through a separate PostgreSQL database, internal-only Docker
network, distinct Worker identity, and independent expert Run volume. It completed in 232.816349
seconds with the same canonical input digest, Run id, and manifest digest as production.

The production and reproduction inventories each contain seven files and 1,579,566 bytes.
`CHECKSUMS.sha256`, `READY`, `evaluation-specs.json`, `observations.parquet`, `run.json`,
`sample-ledger.json`, and `scorecard.json` are byte-identical; there are no missing, extra, or
different files. Key artifact identities are:

- observations: `sha256:b178c5c95cf17758898b9f12cee9e0bf4f01456f35d61bf802da7ce25f61b411`;
- scorecard: `sha256:aeceaacd321ae87004dd07c20f2c1c5bdb782e2b0b56b69b1e0fc64a2018a156`;
- sample ledger: `sha256:4479940b71b99bf3004b40d7ecea09cae0789206aac07ddee4328be1154ef477`.

Repository acceptance passed with 1,309 tests and four optional external-PostgreSQL-DSN skips.
Strict MyPy, Ruff, compilation, strategy-digest verification, Docker image import smoke, production
mount inspection, and PostgreSQL publication checks passed. The production Worker remained healthy
without OOM termination or restart.

## Boundary and next gate

This acceptance closes only `P3-STRAT-002`. It satisfies the predecessor of `P3-ATTR-001` but does
not start attribution. `P3-ATTR-001` remains blocked and requires an explicit deferred-gate
resumption plus three
persisted outcome-blind questions with metric and materiality rules before any baseline or
intervention result is accessed. `P3-EXPERT-004` remains blocked until that attribution evidence is
accepted.
