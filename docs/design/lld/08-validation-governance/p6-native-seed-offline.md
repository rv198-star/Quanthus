# P6 Native Base Historical Seed — Offline Interface

| Field | Value |
| --- | --- |
| Parent | `../08-validation-governance.md` |
| Design status | draft |
| Delivery gate | open for offline engineering; production blocked |
| Work item | `P6-BASE-001`, GitHub #514 |
| Execution authority | Owner continuation recorded in #514 comment 6177175829 |
| Source binding | `config/forward-validation/seed/base23-offline.v1.json` |

This additive offline design leaves the frozen `p6-forward-validation.md` bytes and
production acceptance gate unchanged.

The Base 2.3 historical stage uses the separate, versioned
`config/forward-validation/seed/base23-offline.v1.json` binding and
`src/quanthus/forward_validation/native_seed.py`. The Owner's 2026-10-14 continuation
admits this offline construction work without an October Daily, minute-sync or backup
predecessor. The frozen production runtime and its Base 2.1 activation binding stay separate.

The dependency graph is pinned metadata → verified 2026 source subset → one job-local
candidate Seed → validation. Each source file is hashed once. The materialized Seed is
shared by diagnostics and export; elapsed time, rows, bytes and reuse disposition are
recorded. It is an immutable offline artifact, not ADM, a Forward Run, a paper ledger or
a replacement for the full seven-table replay bundle. No official samples are added.

The minimum candidate-input closure is daily bars, tradability, calendar, listing,
security and adjustment factors. Before file access, the manifest inventory selects only
2026 daily bar/state/calendar partitions; identity and factor reference tables are distinct
from protected price/result partitions. Manifest, content, MATERIALIZATION, READY, exact
member bytes and row counts are bound. Reference-table presence alone never establishes PIT.
The native raw price basis is `UNADJUSTED`, as used by the native consumer materializer;
the older Base 2.2 metadata probe did not establish row-level price values.

The adapter maps Decimal OHLC/turnover and lineage into the existing P6 candidate-daily
column interface. Listing/security effective intervals and every market-row knowledge
interval are evaluated at the actual session close. Late/missing rows remain blocked;
their timestamps are never moved to that close. Orphan session keys, ambiguous listing
sessions, invalid OHLCV, missing lineage and insufficient history fail offline validation.
No source repair, correction reparenting or inherited P4 research authority occurs.

`build` refuses an existing output. `verify` checks the retained artifact without another
source build; an independent cold construction uses a fresh output and compares content
digests. `OFFLINE_VALIDATED` means only input engineering validation; producer
`NOT_GRANTED`, P6 consumer authorization, protected-window gates and activation are separate.
There is no Worker/production call path to this adapter. A later production change still
requires purpose-qualified input and the explicit successor deployment/activation decision.

Tests: `tests/unit/test_forward_validation_native_seed.py`. Current evidence and runnable
commands: `docs/acceptance/p6-base23-offline-seed-20261014.md`.

