# LLD 01: Point-in-Time Data Foundation

| Field | Value |
| --- | --- |
| Phase | P1 |
| Design status | approved |
| Delivery gate | accepted-conditional |
| Gate source | `config/p1-gates.v1.json` |
| Governing requirements | FR-DATA-001 through FR-DATA-006 |
| Conditional boundary | C1 residual 4,173 is fail-closed under `p1-c1-conditional-20260725-v1` |
| Coverage follow-up | Issues #38/#40 resolved through OCI DWD revision `daily_ohlcv_none_dwd_v2_oci_20260725` |

## 1. Goal

Produce a complete and replayable view of what the system could know at a historical market
date. No downstream module may read vendor frames directly.

## 2. Scope

P1 includes SSE/SZSE daily bars from 2011 onward, security lifecycle, trading calendar,
corporate actions, adjustment factors, tradability, historical ST state, board-specific price
limits, delisted securities, static level-one industry mapping, and snapshot manifests.

P1 excludes BSE, B shares, explicitly configured non-A-share instruments, news, financial
statements, historical concept membership, and official minute features. The active frozen
`config/p1-universe.v2.json` is the explicit selection contract; it must name exceptional
exclusions with reasons rather than silently dropping them. Official 5-minute data begins in
2024-07 and its consumption is deferred until event-window work; acquisition continues to retain
recoverable history. One-minute data beginning 2026-03 is continuously acquired as archive-only
research data because its upstream retention window is short.

P1 acceptance establishes deterministic replay and fail-closed behavior. The initial S3 full build
exposed 276 securities with incomplete normalized unadjusted-price history and 639,009 affected
execution rows. Issue #38 restored their source history in a new OCI child revision. The rebuilt S3
has zero unexpected prior-close gaps; 72 exact first-supported-trade boundaries remain separately
coded and fail closed because no earlier tradable close exists inside the supported history.
Issue #40 subsequently repaired three active histories frozen at an old 2,000-row acquisition
checkpoint. Its OCI child retained the accepted parent and inserted 5,334 exact unadjusted tail
keys from independently checksummed raw evidence.

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

The first materialized layer is `tradability_coverage`: it crosses every in-scope security with
every open exchange session and classifies the row as `trading`, `suspended`, `not_listed`,
`delisted`, or `missing`. It uses only suspension records visible by the requested knowledge
cutoff, retains invalid observed bars with `data_valid=false`, and fails new entries from that
flag. Historical ST and price-limit fields are later S2/S3 enrichments; their absence may not be
represented as a tradable value.

The price-limit policy also carries the 2026-07-06 main-board risk-warning change from 5% to 10%
as an effective-dated regime. A historical replay selects the rule for that market date; it must
not project the current 10% rule backward.

`data_valid` describes a structurally valid source bar, not fillability. A visible suspension
takes precedence over a vendor placeholder bar. A structurally valid bar with zero volume or
amount has `is_executable=false` and reason `zero_liquidity`; downstream entry logic must not
simulate a fill from its close.

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
6. Publish canonical partitions and immutable manifests.
7. Build and gate a lineage-bound DWS market summary from the accepted daily DWD revision.
8. Advance the DWD and DWS dataset pointers; downstream snapshot materialization remains governed
   by the snapshot interface.

Each stage is idempotent by input manifest and transformation version.

Recurring production execution follows ADR 0003. Hermes separates issue-bound historical
bootstrap work from daily/weekly ODS acquisition and writes READY only after an immutable release
manifest. OCI runs the versioned `t-close-plus-5h-v1` stages, builds both the normalized daily DWD
partition and its DWS market summary, gates both revisions, and then advances their dataset
pointers. The DWS manifest binds the exact DWD revision and manifest checksum; it is a rebuildable
projection rather than an alternative canonical bar source. The accepted P1 full-history DWD
remains the base; daily T partitions are chained above it without copying the full history. Closed
sessions are evidenced by the release calendar and recorded as clean skips. Minute and
partial-session data are not inputs to this pipeline version. Terminal email delivery occurs only
after the durable run ledger is written; a notification failure is recorded but does not change a
successful data result.

For an open session, daily acquisition first requires BaoStock to return the target date for all
three contract indices. Empty successful responses are availability evidence and never trigger an
immediate reconnect; only provider/network errors do. The full stock scan starts after readiness,
retries only its bounded missing set, records request-local acquisition timestamps, and derives
dataset `knowledge_cutoff` no earlier than the latest observed acquisition. Hermes and CN01 use
the versioned early/late collection-window experiment in ADR 0004; both retain identical coverage
and READY gates. The rendered systemd service checks the contract's node-specific start plus
five-minute activation grace before invoking the lane runner, so restarting a timer after its
window cannot silently turn into an unplanned third observation.

### Implementation conformance map

| Capability | Governing implementation | Contract and evidence |
| --- | --- | --- |
| PIT identity, tradability, and snapshots | `src/quanthus/adjustments.py`, `src/quanthus/tradability.py`, `src/quanthus/snapshot_publish.py`, versioned P1 config | `tests/p1/`, `tests/contract/test_p0_decisions.py`, P1 acceptance files |
| Immutable ODS handoff and daily DWD/DWS | `src/quanthus/pipelines/`, `config/pipelines/` | `tests/unit/test_ods_handoff.py`, `tests/unit/test_pipeline_runner.py`, `tests/p1/test_daily_warehouse.py` |
| Repository-owned ODS schedule | `src/quanthus/pipelines/lane_runner.py`, `config/schedules/`, `ops/bin/`, `ops/systemd/` | `tests/unit/test_lane_runner.py`, `tests/unit/test_schedule_contracts.py`, ADR 0004 and its runbook |

## 7. Work items

| ID | Work | Dependency | Acceptance evidence |
| --- | --- | --- | --- |
| P1-DATA-001 | Define storage layout, schemas, and manifests | Bootstrap | Round-trip typed fixture |
| C1 | Rebuild effective-date adjustment factors | P1-DATA-001 | Ex-right continuity and no-future-factor tests |
| C2 | Classify every security-calendar gap | S4, security master | No unclassified gap in acceptance corpus |
| S1 | Measure and complete delisted coverage | Security master | Reference-universe reconciliation |
| S2 | Build binary historical ST state from Baostock daily evidence; retain announcements for audit | Landing data | Prior-session PIT shift and dated known-case reconciliation |
| S3 | Resolve board/ST/IPO price limits on actual security sessions | S2, security master | Regime-boundary and suspension-crossing golden cases |
| S4 | Build authoritative trading calendar | Source ingestion | Exchange-session reconciliation |
| P1-UNIVERSE-001 | Implement daily point-in-time universe policy | Status and bars | Historical-universe fixture |
| P1-SNAPSHOT-001 | Implement atomic snapshot catalog/API | Canonical datasets | Cutoff and revision contract tests |
| P1-ACCEPT-001 | Run Chapter 01 acceptance suite | C1/C2/S1 and snapshot | D1/D2/D3 evidence bundle |
| META-DATA-PIPE-001 | Operate Hermes ODS releases and OCI T+5h publication | Accepted file-first platform | Open/closed-session rehearsals and durable run ledger |
| META-DATA-PIPE-002 | Retire the legacy weekly monolith and split Hermes ODS schedules | META-DATA-PIPE-001 accepted | Shadow run, explicit cutover, rollback, and no overlapping writers |
| P1-DATA-002 | Restore incomplete none histories detected by S3 replay | S3 coverage audit | Exact-key source backfill and reconciled child revision |
| P1-DATA-003 | Restore truncated active none histories detected by daily coverage | P1-DATA-002 | Exact-key backfill, active-universe coverage, and OCI child revision |

The S1 reconciliation report measures each in-scope delisted security over the supported daily
history window. It distinguishes observed bars, known suspension sessions, exact versioned
terminal-boundary exceptions visible at the requested knowledge cutoff, unexplained missing
sessions, and invalid observed bars. Terminal-boundary rows must be evidenced exact delist-date
keys; they are never a blanket delisting exemption. No report may present a suspension-only
history as a missing history. The report and the frozen universe-policy version are required
acceptance evidence.

## 8. Acceptance

- **D1**: 10 historical dates by 10 securities replay against independent references.
- **D2**: 20 known limit or suspension dates reconcile exactly.
- **D3**: returns around selected ex-right dates contain no artificial jumps.
- C1, C2, and S1 are closed with stable evidence paths.
- Repeating a build from identical manifests produces identical canonical checksums.

The evidence bundle is bound to one published snapshot. It records passed C2/S1 evidence and either
a full C1 pass or the explicit versioned C1 conditional-acceptance policy,
the full 10-by-10 D1 date/security matrix with independent reference ids, at least 20 unique D2
limit or suspension cases, and non-empty D3 ex-right cases with no artificial jump. Passing the
bundle validator is necessary but does not itself change the release gate configuration.

OCI rebuilds this bundle with `quanthus build-p1-evidence`. The command verifies each snapshot
partition checksum before it replays D1 from the Baostock reference pack, D2 from the immutable
coverage matrix and PIT suspension records, and D3 from linked factor rows plus adjacent
none/qfq returns. A mismatched report checksum, missing case, or changed replay result fails the
build. Conservative factor-visibility exceptions remain explicit C1 `open` evidence; the command
cannot silently turn them into a passed blocker.

The gate file and evidence bundle now record this conditional acceptance, so P2 implementation is
open. A future gate regression or invalidated evidence bundle blocks further P2 promotion; prose
in this document cannot keep the gate open by itself.
