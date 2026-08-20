# Operational ADS Historical Base

| Field | Value |
| --- | --- |
| Parent | `../01-data-foundation.md` |
| Work item | `META-DATA-BASE-003` |
| Tracking issue | GitHub issue #46 |
| Design status | superseded for current operation under ADR 0013 |
| Delivery gate | historical evidence and cleanup candidate; no current consumer authority |
| Profile | `strategy_replay_operational_v1` |

## 1. Purpose and authority

This document records the former OCI operational ADS historical Base. Under ADR 0013 it is retained
only for historical evidence or exact reproduction and is not a current application authority. Its
physical roots below `/srv/quanthus/ads/operational/` are cleanup candidates after the separate
retirement audit proves no runtime, rollback, or reproduction dependency remains. Existing
development, arbitration, permanent-blind, recent-research, and other offline research bundles are
likewise historical artifacts rather than current ADM inputs.

The provisional package is `ads-base-through-20260730-v1`. After the July 31 close and exact final
upstream acceptance, a separate `ads-base-202607-v1` is created. Neither package may be appended,
rewritten, relabelled, or used as staging for the other. `BASE_CURRENT.json` selects the active
historical Base for controlled Base/research derivation. It is not a consumer release commit and
does not replace ADR 0005's sole consumer-visible `release-set_CURRENT.json`.

Every package remains `research_only=true` and `execution_authorized=false`. This work does not
publish a consumer release set, activate a strategy, change a timer, delete retained data, or
record `GO_FOR_CUTOVER`.

## 2. Exact-input build plan

`config/operational-ads-base.v1.json` freezes package identities, roots, upstream roles, table
contracts, reader roles, and publication safety. One external build plan binds the runtime inputs.
The plan contains:

- exactly one canonical catalog, DWD Base, DWS Base, PIT, identity-reference, and
  calendar-reference manifest role, each with a fixed manifest path, manifest SHA-256, logical id,
  and content digest;
- a complete inventory that classifies every current operational daily revision and retained
  research export, records its coverage and upstream-generation digest, and makes a `reuse`,
  `rebuild`, `exclude`, or `evidence_only` decision;
- exactly one sealed source binding per semantic table, including source role, table logical
  digest, lineage digest, and per-decision PIT evidence digest where required;
- one sorted generation-ledger row per decision session with catalog/DWD/DWS manifest hashes,
  accepted lane/generation, original `knowledge_cutoff`, and decision cutoff;
- context, decision, execution, and outcome ranges plus exact universe and corporate-action replay
  policy versions; and
- for the final package only, the provisional Base identity and exact accepted July 31 daily ADS
  id/digests.

Source manifest paths in the operator plan must be absolute, exact, non-symlink files and may not
contain or resolve `CURRENT`. Every `object_id` must equal a manifest logical id or its immutable
package-directory id; upstream and inventory bindings for that id must use the same manifest and
content hashes. Generation-ledger catalog/DWD/DWS hashes must equal their bound upstreams. Absolute
paths are omitted from the published package identity. The manifest retains only path-free logical
lineage; moving the complete Base to a clean path does not require producer storage.

## 3. Semantic tables and roles

The initial Base owns the current supported replay inputs plus the daily operational summary:

| Table | Role | PIT requirement |
| --- | --- | --- |
| `daily_ohlcv_none` | decision-visible | per decision session |
| `daily_universe` | decision-visible | per decision session |
| `daily_market_summary` | decision-visible | per decision session |
| `trading_calendar` | runner-only | exact calendar binding |
| `security_code_validity` | runner-only | retained validity lineage |
| `price_limit_execution_daily` | runner-only | execution evidence only |
| `corporate_actions_normalized` | runner-only | exact normalization lineage |
| `corporate_action_exceptions` | runner-only | same-version fail-closed ledger |
| `static_industry_mapping` | runner policy reference | static concentration use only |
| `benchmark_curve` | outcome-only | evaluator capability only |

Decision readers can open only decision-visible parts. Replay runners can open decision-visible,
runner-only, and runner-policy references. Evaluators can open only outcome tables. No configured
capability grants `label_only`. The runner constructs the existing typed historical frame reader
and passes only typed date/security views into strategy callbacks; callbacks receive neither the
Base root nor arbitrary part handles.

The current portable bootstrap PIT status
`capability_fixture_without_per_decision_publication_proof` is insufficient for a decision-visible
Base table. A retained export may donate bytes only after its logical content, original lineage,
role, schema, key, and an exact per-date PIT equivalence binding are verified. Otherwise the table
is rebuilt from the accepted Base/upstream family. Build time never replaces a source cutoff.

## 4. Build and verification

`src/quanthus/operational_ads_base.py` verifies every bound source manifest, source `READY`, and
part before creating unique staging. Each table must bind exactly one sealed semantic source and
one part. The builder verifies the source path, byte size, and SHA-256, copies the part
byte-for-byte, verifies the copied bytes, and runs one DuckDB vectorized quality pass for schema,
row count, nulls, duplicate business keys, and date bounds. Multi-source merge, deduplication, and
semantic conflict resolution belong in the accepted semantic-source normalization step; an ADS
package plan that still requires those operations is rejected before staging.

The builder emits the copied sealed part for each initial table. Parquet tables reuse the accepted
source logical digest and fingerprint. JSON tables retain the source artifact digest in
`source_bindings[].table_content_digest` and record the independently reproducible ADS canonical
row digest in the table-level `table_content_digest`. Every table retains path-free lineage and
records the copied part's actual logical schema, row and null counts, zero duplicate/conflict
facts, date bounds, byte size and SHA-256, schema SHA-256, and sort keys together with exact
upstreams, inventory, generation ledger, runtime, code revision, and plan digest. An upstream
schema identifier may name its own logical contract; the ADS manifest always records the schema
computed from the copied bytes.
`manifest.json` follows all table parts; `READY` is written last and binds the exact manifest
SHA-256 before one directory rename.
The package content digest binds logical table content, schema, lineage, policy, and upstream
identity; it excludes each generated part's encoding-dependent byte size and SHA-256. Those
physical facts remain mandatory in the manifest and are bound by the manifest SHA-256 and `READY`.

An append-only final table may use `canonical_logical_append_chain_sha256_v1` instead of
rehashing its complete historical row stream. The chain binds the accepted Base logical digest
and row count to one non-overlapping, date-ordered delta logical digest, row count, and date range.
The normal build still materializes one complete table part and checks its full business-key
cardinality and date bounds. Explicit deep verification recomputes both logical components from
the complete part; the append chain does not permit replacement, overlap, or deduplication of the
accepted Base rows.

The generation ledger keeps market session T as the decision date. When canonical publication is
only available overnight, its timezone-aware decision cutoff may be on T+1 but must remain before
the `09:30 Asia/Shanghai` open. The replay frame retains the T snapshot and T+1 execution date; it
does not relabel T+1 as the decision session or expose execution-day observations to the strategy.

The normal verifier starts only from `manifest.json` and `READY`, checks the complete declared file
set, part bytes, schema, and sealed metadata, and reads no build-plan or producer path. Build runs
the one required vectorized quality pass; normal check, replay, copy verification, and promotion
do not repeat it and do not invoke Python logical-row hashing. `check-operational-ads-base
--deep-logical-content` remains an explicit offline audit that recomputes Parquet statistics and
logical row digests when a new semantic investigation requires it. It is not a publication-loop
gate. An identical logical input rebuild must reproduce table bytes and table/package content
digests; `built_at`, publisher code revision, and publisher runtime are outside logical content
identity but remain bound by manifest SHA-256.

## 5. Replay and publication sequence

`load_operational_ads_replay_reader` rebuilds the seven existing replay `DatasetManifest` values
from verified Base parts and retained source revisions. It validates the same corporate-action
normalization contract, creates PIT bindings from the per-date generation ledger, and then opens
`StrategyReplayADSFrameReader`. A bounded `smoke-operational-ads-replay` audit must succeed before
publication can move the Base descriptor.

The root operator sequence is:

```text
exact plan -> DWD/DWS current-descriptor preflight -> sealed source gates
           -> byte-copy plus one vectorized quality pass -> package READY
           -> seal check -> clean-path existing-reader smoke
           -> final DWD/DWS current-descriptor preflight -> BASE_CURRENT compare-and-swap
```

Each upstream preflight requires the complete versioned descriptor shape, resolves its exact
`package_path` to the plan-bound sealed manifest, verifies the package manifest identity and input
ledger, and recomputes the DWD/DWS pair digest. Input-ledger verification requires its safe exact
package-relative path, manifest file-inventory size and SHA-256, actual file bytes, canonical
semantic digest, and matching contract, phase, and through-date; a missing file, symlink/path
escape, byte change, or outer-checksum-resealed semantic forgery fails preflight. The second
preflight prevents a descriptor change during the build and smoke interval from being promoted as
though it still matched the plan.

`promote-operational-ads-base` copies when necessary, seal-verifies the copy, refuses an existing id
with different bytes, and compares the prior descriptor manifest SHA-256 (or explicit `absent`)
under an OCI process lock before one atomic replacement and directory sync. A failure before
descriptor CAS leaves the prior descriptor and all existing packages unchanged. Retry over
identical inputs is idempotent.

## 6. Conformance

| Concern | Governing path |
| --- | --- |
| Policy and package contract | `config/operational-ads-base.v1.json` |
| Builder, verifier, capabilities, replay adapter, pointer CAS | `src/quanthus/operational_ads_base.py` |
| CLI | `src/quanthus/cli.py` |
| Root publication sequence | `ops/bin/publish-operational-ads-base` |
| Tests | `tests/unit/test_operational_ads_base.py`, `tests/contract/test_operational_ads_base_boundary.py`, `tests/contract/test_strategy_ads_boundary.py` |
| Operations | `docs/operations/dual-lane-data-platform-runbook.md` |
| Current evidence | `docs/acceptance/meta-data-base-003-ads-base-202607/` |

Repository tests prove contract behavior with synthetic semantic data. They do not prove the real
provisional or final package. Runtime acceptance requires the exact #47/#48 packages, complete OCI
inventory/mapping ledger, real clean-path replay smoke, resource measurements, reproducibility,
and retained manifests/checksums.
