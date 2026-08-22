# P3-POS-004 Trial-Reduction Compatibility Acceptance (2026-08-22)

| Field | Value |
| --- | --- |
| Status | accepted for the formal development strategy |
| Work item | `P3-POS-004` / Issue #185 |
| Code revision | `777f216` |
| Production Worker Job | `job_75c48f2c52c04283884e7b3e3092b1fe` |
| Isolated reproduction Job | `job_24b97ddc00da48fcbe0eee6df56427b6` |
| Expert Run | `expert_run_9114b94917928090c67bd4068d4e09438a2fc4c2744cd2ffe556cde15a8e480e` |
| Run manifest digest | `sha256:9114b94917928090c67bd4068d4e09438a2fc4c2744cd2ffe556cde15a8e480e` |
| Input digest | `sha256:e6304adb7974c6cfceac82de978bc2976202b1a2f2fbbd9f5a8190d1b6c34a06` |

## Accepted compatibility

The human-approved strategy is exactly `candidate.p4-trend-risk@1.0.1` at semantic digest
`sha256:805ce33024ae82dc46a685c3ad7425528702e6a089ee4d09f27af2793d55ed0c`.
Relative to `1.0.0`, only the strategy version, product-candidate version, and position-lifecycle
binding changed. The admitted card binds position lifecycle `1.1.0`; lifecycle `1.0.0` and the
immutable `candidate.p4-trend-risk@1.0.0` card remain loadable and unchanged.

Lifecycle `1.1.0` adds only `Trial + REDUCE_FILLED -> Trial`. The transition requires one real P2
partial-sell Fill and a smaller positive same-security ledger quantity. P2 releases proportional
cost, retains the residual position, and does not perform unit-close settlement. Focused tests
cover the partial-sell accounting, transition evidence, one-reduction strategy limit, and legacy
full-sell path.

## Production compatibility proof

The production PostgreSQL Replay Worker completed the bounded January 2020 Sentinel in 260.008224
seconds. It bound DWD Base `dwd-base-202607-v2.1`, correction
`dwd-base-202607-v2.1-correction-20260813-001`, and parent Event Run manifest
`sha256:8b9d184c2947b82d1a98440e7e6313e3659735fa352ea5dd72ec4b90fc80050c`.
It evaluated 267 development events: 259 applicable, eight abstained, 104 `trial_entry`, and 163
`no_action`. The scorecard records `economic_evidence=false` and
`sealed_partition_access=false`.

The production and isolated reproduction used the same canonical input but separate Job ids,
PostgreSQL databases, Worker processes, and expert Run volumes. The isolated Run completed in
244.879961 seconds. Both sealed the same Run id and manifest, and all seven files were byte
identical. The inventory contains 1,579,570 bytes. Key file hashes are:

- `observations.parquet`: `sha256:2ad9cf8ce13b4420808a6402e581520cd2e5b832f51d57ac3424a8118d10f234`;
- `scorecard.json`: `sha256:6504faee12dcf39f7bb329fc87e38aadf7f8e51c4320859bf0e6be1e09e40373`;
- `sample-ledger.json`: `sha256:4479940b71b99bf3004b40d7ecea09cae0789206aac07ddee4328be1154ef477`.

The repository check passed 1,313 tests with four optional PostgreSQL-DSN skips. The Replay Worker
image import smoke passed and production retained its 16 GiB hard limit, 14 GiB working-set guard,
single concurrency, development-only partition, and serialized heavy queue. The temporary
reproduction database, Run volume, and network were removed after byte comparison.

## Boundary and next gate

This acceptance proves versioned trial-reduction compatibility and deterministic January topology
behavior. The Sentinel contains entry decisions only and makes no economic claim. It does not open
arbitration 2022-2024, permanent-blind 2025, Shadow, notification, governance admission, or
execution. Full held-position and economic effects remain owned by the preregistered complete-P2
Runs under `P3-ATTR-001`.
