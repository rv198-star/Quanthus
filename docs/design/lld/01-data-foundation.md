# LLD 01: Point-in-Time Data Foundation

| Field | Value |
| --- | --- |
| Phase | P1 |
| Status | Implementation allowed; acceptance blocked |
| Governing requirements | FR-DATA-001 through FR-DATA-006 |
| Blocking work | C1, C2, S1 |

## 1. Goal

Produce a complete and replayable view of what the system could know at a historical market
date. No downstream module may read vendor frames directly.

## 2. Scope

P1 includes SSE/SZSE daily bars from 2011 onward, security lifecycle, trading calendar,
corporate actions, adjustment factors, tradability, historical ST state, board-specific price
limits, delisted securities, static level-one industry mapping, and snapshot manifests.

P1 excludes BSE, B shares, explicitly configured non-A-share instruments, news, financial
statements, historical concept membership, and official minute features. The frozen
`config/p1-universe.v1.json` is the explicit selection contract; it must name exceptional
exclusions with reasons rather than silently dropping them. Official 5-minute data begins in
2024-07 and is deferred until event-window work. One-minute data beginning 2026-03 is
archive-only research data.

## 3. Data layers

| Layer | Mutability | Contract |
| --- | --- | --- |
| Landing | Append-only | Original payload plus source, request, acquisition time, checksum |
| Normalized | Rebuildable/versioned | Canonical columns, types, lifecycle, and source revision |
| Curated | Versioned | Adjustment factors, tradability, gaps, and derived state |
| Snapshot | Immutable | Atomic dataset references at a knowledge cutoff |

Raw files never receive in-place corrections. A source correction creates a new revision and
new downstream snapshot id.

The curated daily view filters the versioned universe but retains an in-scope raw row that
violates a market-data invariant with `data_valid=false`. Downstream new-entry logic fails
closed from that flag and the corresponding gap/tradability state; it must not delete the row
or invent a structural-break exit.

## 4. Canonical datasets

### `security_master`

Primary key: `security_id`.

Required fields: code, exchange, board, list date, delist date, name validity interval,
security status, source, source revision, and knowledge cutoff.

Codes are attributes, not identity. A code or name change must not create a new instrument
unless the exchange identity changes.

### `trading_calendar`

Primary key: exchange and market date. Fields include session state, previous/next session,
and source revision.

### `bars_daily`

Primary key: security id and market date. Store unadjusted OHLC, volume, amount, source,
source revision, acquisition time, and row checksum. Prices must satisfy low <= open/close <=
high and non-negative volume/amount.

### `corporate_actions` and `adjustment_factors`

Corporate actions retain announcement, registration, ex-right, and payment dates separately.
Factors are effective-date versioned and reproducible from action inputs. A historical query
may use only factors known at its knowledge cutoff.

### `tradability`

Primary key: security id and market date. Include suspension, ST state, list days, upper/lower
limit prices, limit regime, data-valid flag, and reason codes. The 2020-08 ChiNext 20 percent
regime change is explicit policy data, not an ad hoc conditional.

### `data_gaps`

Primary key: security id and market date. Every absent expected bar is one of `not_listed`,
`suspended`, `delisted`, or `missing`. Unknown is not an allowed accepted-state value.

### `industry_map`

Static level-one CSRC or Shenwan mapping. It is valid only for concentration controls and its
non-point-in-time limitation must be present in lineage. It cannot enable the sector expert.

### `dataset_manifest`

Include dataset name/version, source revisions, row count, partitions, schema hash, content
checksums, creation time, knowledge cutoff, and parent manifests.

## 5. Snapshot interface

Conceptual call:

```python
get_snapshot(
    market_date,
    knowledge_cutoff,
    universe_policy_version,
    dataset_versions,
) -> SnapshotRef
```

Publication is atomic. Resolution requires an exact dataset-version mapping and universe-policy
version, chooses only the newest snapshot whose timezone-aware `knowledge_cutoff` is no later
than the requested cutoff, and fails on ambiguity. It returns `market_date`,
`knowledge_cutoff`, `source_revision`, and `snapshot_id`. Downstream queries receive only views
bound to that snapshot. A held security with missing data produces no synthetic structural-break
value.

## 6. Pipeline sequence

1. Acquire and checksum source payloads.
2. Normalize identities, dates, numeric units, and source metadata.
3. Reconcile the exchange calendar and security lifecycle.
4. Build adjustment factors and tradability state.
5. Run schema, invariant, gap, and cross-source checks.
6. Publish canonical partitions and an immutable manifest.
7. Materialize and register the point-in-time snapshot.

Each stage is idempotent by input manifest and transformation version.

## 7. Blocking work

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P1-DATA-001 | Define storage layout, schemas, and manifests | Bootstrap | Round-trip typed fixture |
| C1 | Rebuild effective-date adjustment factors | P1-DATA-001 | Ex-right continuity and no-future-factor tests |
| C2 | Classify every security-calendar gap | S4, security master | No unclassified gap in acceptance corpus |
| S1 | Measure and complete delisted coverage | Security master | Reference-universe reconciliation |
| S2 | Derive historical ST state from announcements | Landing data | Dated known-case reconciliation |
| S3 | Derive board-specific price limits | S2, security master | Regime-boundary golden cases |
| S4 | Build authoritative trading calendar | Source ingestion | Exchange-session reconciliation |
| P1-UNIVERSE-001 | Implement daily point-in-time universe policy | Status and bars | Historical-universe fixture |
| P1-SNAPSHOT-001 | Implement atomic snapshot catalog/API | Canonical datasets | Cutoff and revision contract tests |
| P1-ACCEPT-001 | Run Chapter 01 acceptance suite | C1/C2/S1 and snapshot | D1/D2/D3 evidence bundle |

## 8. Acceptance

- **D1**: 10 historical dates by 10 securities replay against independent references.
- **D2**: 20 known limit or suspension dates reconcile exactly.
- **D3**: returns around selected ex-right dates contain no artificial jumps.
- C1, C2, and S1 are closed with stable evidence paths.
- Repeating a build from identical manifests produces identical canonical checksums.

P2 remains blocked until the gate file and evidence bundle both record this acceptance.
