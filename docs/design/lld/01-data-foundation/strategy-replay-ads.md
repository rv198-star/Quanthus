# Strategy Replay ADS

| Field | Value |
| --- | --- |
| Parent | `../01-data-foundation.md` |
| Work item | `META-DATA-PIPE-003` |
| Design status | approved |
| Delivery gate | accepted-conditional for the historical research bootstrap; catalog-native publication and sealed evaluation remain open |
| Profile | `strategy_replay` |

## 1. Purpose and boundary

This profile is the normal immutable input boundary for historical strategy replay. A research
node receives one explicit bundle and needs no ODS, DWD/DWS, PostgreSQL, mutable `CURRENT`, or
producer path. The bundle is research-only and cannot authorize an order, notification, or fill.

The 2012-2025 bootstrap is assembled from accepted snapshot
`p1-historical-replay-20260729-v1`. The additive 2026 research backfill is assembled from
`p1-historical-replay-2026-through-20260727-v1`. Both sources were materialized after their
historical decision dates. They supply self-contained historical capability fixtures, but do not
prove that an independently published snapshot existed on every historical decision date. Their
PIT maps therefore record `capability_fixture_without_per_decision_publication_proof` for every
decision session. Future catalog-native bundles must replace that limitation with exact per-date
canonical generation and publication lineage.

## 2. Scope and tables

The policy in `config/strategy-replay-ads.v1.json` freezes the original three separate bundle
scopes. `config/strategy-replay-ads.v2.json` retains those boundaries and adds one development-
readable recent-research scope without reopening arbitration or blind data:

| Scope | Decision partition | Reader role |
| --- | --- | --- |
| development | 2012-2021 | `development_research` |
| arbitration | 2022-2024 | `arbitration_operator` |
| permanent blind | 2025 | `blind_evaluation_operator` |
| recent research | 2026-01-01 through 2026-07-27 execution boundary | `development_research` |

The recent scope contains decision sessions from 2026-01-05 through 2026-07-24 and their next-open
execution context through 2026-07-27. It is an additive research backfill, has no `CURRENT`, and
does not modify or supersede the original three bundles.

Each bundle may contain earlier warm-up context under
`all_available_history_through_execution_end`, but cannot contain a row, benchmark observation, or
execution fact after its own final authorized execution session. Decision dates remain restricted
to the named partition. End intervals such as code validity and action blocks are clipped with
explicit right-censor flags and counts.

The seven replay tables are daily unadjusted OHLCV, daily universe, trading calendar,
security-code validity, execution/price-limit facts, normalized corporate actions, and the
same-version action-exception ledger. The bundle also embeds the frozen `sh000300` benchmark and
the static level-one concentration mapping. The latter remains
`static_non_pit_concentration_only`; it is not historical industry truth.

There is no mandatory calendar-year directory. `manifest.json` is the only discovery surface and
lists every semantic `tables[].parts[]` path. Consumers support one or more parts and do not infer
meaning from filenames or directory layout.

## 3. Integrity and identity

Publication writes all table parts, then `manifest.json`, then a `READY` file containing the exact
manifest SHA-256 before one directory rename. The manifest binds:

- the source snapshot, source manifests, policy and publisher runtime;
- decision, execution, context, role, and per-decision PIT limits;
- part SHA-256, bytes, rows, schema, date bounds, sort keys, and source lineage;
- a canonical logical-row SHA-256 for every Parquet table;
- the full-source identities and scoped-output identity of reference JSON; and
- a layout-bound content digest distinct from the exact manifest SHA-256.

Feature panels and backtest run manifests bind both ADS identities. A matching content digest does
not permit substituting a different manifest revision.

`check-strategy-replay-ads` is the publication/migration deep check and recomputes canonical
logical-row digests. Normal replay opening performs the bounded fast check: `READY`, manifest and
part hashes, schema, rows, keys, date ranges, censor counts, roles, and lineage. It does not pay the
full logical digest cost on every run.

## 4. Access and replay interface

Formal OCI bundles live below `/srv/quanthus/portable-ads/<scope>/<bundle-id>`. Publication runs as
root; development and recent research are group-readable by `ubuntu`, while arbitration and blind
bundles use distinct locked system principals. There is no portable ADS `CURRENT` pointer. Every
run names one immutable bundle id and its exact role.

The semantic deep check runs in the root-owned publisher with the scope's locked logical reader
role. A separate OS probe runs as the scope principal and must traverse and read every bundle path
without being able to modify any of them. Data principals do not receive access to the root-owned
deployment checkout or its virtual environment merely to execute the verifier.

The normal CLI inputs are `--ads-bundle` and `--ads-role`. The runner owns the ADS reader and passes
only typed decision views to strategy code. Runner-only execution/action methods increment an
access counter; reaching one during `decide()` fails the run. Repository-governed strategy code is
the trust boundary; this is not a hostile Python sandbox.

The pre-existing full 2012-2025 bootstrap snapshot was readable by the development operator before
these principals existed. Consequently, the physically isolated 2025 bundle is not evidence of a
sealed blind experiment for this historical candidate. It is retained only as a migration and
capability artifact. A valid future blind evaluation requires source isolation before access and a
separate authorization record.

The hidden legacy `--snapshot-dir` path remains only to reproduce frozen P3 acceptance artifacts.
It is not a normal research entry point and may not be used by a new strategy or P4/P5 workflow.

## 5. Conformance

| Concern | Governing path |
| --- | --- |
| Policy and scope identities | `config/strategy-replay-ads.v1.json`, `config/strategy-replay-ads.v2.json` |
| Publisher, verifier, role reader | `src/quanthus/strategy_replay_ads.py` |
| Normal CLI boundary | `src/quanthus/cli.py` |
| OS-role publication | `ops/bin/publish-strategy-replay-ads`, `ops/sysusers.d/quanthus-ads.conf` |
| Contract tests | `tests/unit/test_strategy_replay_ads.py`, `tests/contract/test_strategy_ads_boundary.py` |
| Operations | `docs/operations/dual-lane-data-platform-runbook.md` |
| Deployment evidence | `docs/acceptance/issue-45-portable-ads-20260730.md` |

The original migration acceptance requires three formal bundles, deep checks, exact P3 development
reproduction from the formal development bundle, role-permission probes, a dependency audit showing
strategy consumers do not resolve warehouse/snapshot pointers, and retained evidence of size and
resource behavior. A recent-research backfill additionally requires its exact v2 source, benchmark,
policy identities, continuous-frame audit, development-role probe, and evidence that no existing
bundle or pointer changed. None of those claims authorizes production dual-lane cutover or deletion
of legacy data.
