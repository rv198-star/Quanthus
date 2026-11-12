# Blueprint history import v1

| Field | Value |
| --- | --- |
| Design status | approved — independent import code/contract review passed on ab2c07c5; import implementation unchanged in 37aaa47a |
| Delivery gate | accepted: final independent audit PASS; #635 ordinary merge 2d7a8bed39c12648b5774801e99676df6f172679; see post-merge closeout |
| Work item | META-RESEARCH-INTEGRATION-001 / #633 |
| Parent | [Blueprint-linked delivery progress](blueprint-progress.md) |
| Runtime | `src/quanthus/blueprint_progress.py` |
| Contract tests | `tests/unit/test_blueprint_progress.py` |

## Problem and bounded decision

Main and a retained research branch appended independently to the same 101-checkpoint
baseline. Main has 106 records; R4 has 135. Appending R4's 34 new records after main
preserves both histories, but their original recorded times precede main's latest record.
The existing chronology check correctly rejects that naive append.

This candidate adds a separately versioned import receipt, while keeping the ledger at
`quanthus.blueprint-progress.v1`. It preserves main's exact record prefix and the complete
source suffix as JSON objects, including original IDs, times, evidence and order. An
`imported_at` clock records when that intact block joins the target ledger. Rewriting old
times, sorting histories together, dropping records, or disabling chronology was rejected
because each loses an existing audit invariant.

This is a one-way integration of retained history. Work still starts from current main;
independent branches do not become permanent parallel progress authorities.

## Receipt contract and clocks

`history_imports` is an optional append-only list. Each receipt has exactly these fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | `quanthus.blueprint-history-import.v1` |
| `id`, `actor`, `reason` | Unique import identity and accountable integration rationale |
| `imported_at` | Timezone-aware ingestion time, at least every source recorded time and the previous effective target time |
| `start_index` | Zero-based length of the exact target record prefix |
| `target`, `source` | Each contains a full 40-character Git revision and the SHA256 of that revision's raw `config/planning/learning-progress.v1.json` bytes |

The checker reads both blobs directly from Git. A missing revision or wrong hash fails;
no network fetch or mutable working-copy fallback happens in the validator. Clone with
the required history or fetch the pinned revisions before checking.

The target snapshot's receipt list must equal all preceding receipts. Its records must
match the current prefix exactly. The checker finds the longest common object prefix
of target and source, requires it to be nonempty, and requires a nonempty source suffix.
The entire suffix must follow the target unchanged. Record IDs remain globally unique;
overlapping import spans fail. Version 1 rejects a source that already contains imports,
avoiding ambiguous nested import semantics.

Ordinary checkpoints retain their `recorded_at` chronology. Only the exact validated
imported span uses `imported_at` for target chronology; all source recorded times still
must be monotonic. A later ordinary checkpoint must be at or after the import time.
Generated views show the two clocks separately and include source and target revisions.

## Semantic preservation and failure behavior

Every combined checkpoint still passes normal evidence, scope, admission-authority and
`supersedes` checks. An imported branch cannot silently supersede a competing target
module/facet update. Such a conflict requires a separate reviewed resolution, outside
this bounded import format.

For each issue observed in the imported suffix, its final observation must either equal
the target's final observation or have a strictly newer `updated_at`. An older observation
or different content at the same timestamp fails. Historical observations inside the
source block stay intact. Current affected GitHub metadata is reconciled by new ordinary
checkpoints after integration.

`check --base-ref FULL_SHA` preserves both old record and old import-receipt prefixes.
New receipts cannot retroactively cover records already present in that base. The import
receipt does not alter research outcomes, consumer admission, sealed requests, cutoffs,
parameters, or historical execution identities.

## Acceptance and recovery

Contract tests construct real Git ledger blobs. They cover successful import and normal
post-import recording; timestamp/content/order/deletion tampering; missing or incorrect
identities; chronology errors despite correct hashes; duplicate IDs; stale facet heads;
issue observation regression; missing evidence/authority; and receipt rewrite/removal or
retroactive annotation. The original direct-append failure remains in the integration
evidence.

This specific integration must also demonstrate exact preservation of main's 106-record
prefix and all 34 R4-only records, both catalog deltas, the exploration conclusion ledger,
frozen research artifacts, and the candidate's complete engineering checks.

The [integration acceptance report](../acceptance/research-main-integration-20261112.md)
records the independent import review, complete candidate checks and the separate final gate.
The original import receipt retains its original target identity, clock and then-pending text.

The [post-merge closeout](../acceptance/research-main-closeout-20261112.md) records final
independent acceptance, ordinary main merge, PR/reference disposition and issue reconciliation.

The original branch commits and pre-import main remain available. Before merge, discard
or revise only this candidate to recover. After merge, any reversal follows a separately
reviewed successor change; old ledger records and receipts stay immutable.
