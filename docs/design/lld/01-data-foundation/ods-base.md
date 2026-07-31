# ODS Base

| Field | Value |
| --- | --- |
| Work item | `META-DATA-BASE-001` |
| Design status | approved |
| Delivery gate | open |
| Provisional acceptance | accepted through 2026-07-30; final July package remains open |
| Governing decision | ADR 0005 |
| Executable policy | `config/pipelines/ods-base.v1.json` |
| Implementation | `src/quanthus/pipelines/ods_base.py` |
| Contract tests | `tests/unit/test_ods_base.py` |
| Operations | `docs/operations/ods-base-runbook.md` |
| Tracking | GitHub issue #47 |
| Acceptance evidence | `docs/acceptance/meta-data-base-001-ods-base-202607/README.md` |

## 1. Purpose And Boundary

The ODS Base is the normal historical reconstruction seed for target-chain ODS. Hermes is the
historical source authority because it holds the broadest governed source history. Hermes and
CN01 each publish the same immutable package bytes; OCI does not retain this ODS Base.

"Complete" means the maximal verified set of currently governed ODS source assets needed by
current or planned downstream reconstruction. It does not mean copying every working directory,
backup, failed attempt, or derived DWD artifact, and it does not claim that an unacquired source
family exists. Missing governed data or an unclassified source blocks publication. A source family
that has never been formally acquired is named as a scope gap rather than fabricated or silently
treated as present.

One Base is one package identity and manifest. Heterogeneous source datasets remain separate files
inside the package. Consumers resolve members through `manifest.json`; path globs, filenames, and
producer-local absolute paths have no semantic authority.

## 2. Source Classes

| Class | Base treatment |
| --- | --- |
| Canonical source files | Included under `payload/canonical/` after checksum, schema, key, date, and integrity inspection |
| Historical minute archives | Included as per-security 1m and 5m Parquet trees; all 4,992 governed parts are required |
| Immutable daily releases | Select one complete market role and one complete event role for every open date in scope |
| Immutable intraday releases | Select one complete 1m and 5m release for every open date in scope |
| Retained accepted/revision evidence | Included under `payload/source-evidence/` with a non-canonical role |
| Backups, mutable workspaces, failed attempts, logs | Classified with a reason and excluded from payload |
| Derived DWD quality/tradability products | Excluded from ODS and rebuilt downstream |

The canonical source set covers adjusted and unadjusted daily bars, valuation, quarterly financial
summary, announcements, company actions, dividends, adjustment evidence, shareholder counts,
security identity, ST, suspensions, stock universe, indices, and the trading calendar. The
historical 1m and 5m archives are combined logically with selected immutable daily increments; the
Base builder does not rewrite source timestamps or business values.

The retained 2026-07-24 weekly-corrections release is source evidence only. It predates the current
weekly contract, lacks industry membership and reconciliation-key artifacts, and contains an empty
financial revision, so it cannot supersede the richer canonical files. The old
`dataset_manifest.parquet` is also retained only as `legacy_source_lineage`; it is known to describe
older row counts and is not the Base catalog.

The recurring schedule now defines governed collectors, schemas, restartable checkpoints, and
per-security receipts for raw F10 and complete three-statement responses. This design change does
not retroactively place those assets in the provisional Base. The final package must retain and
verify a successful full-market acquisition before adding the new files and removing the existing
scope gap; until then the accepted Base continues to describe the holdings honestly.

The retained valuation history currently ends on 2026-07-18. The bounded July repair uses the
repository-owned BaoStock range collector in `src/quanthus/providers/valuation_history.py`, not a
slice of that retained Parquet. It records one receipt per requested security, checkpoints fixed
security batches, and fails on provider errors, empty responses, identity/date drift, or an
all-null valuation response. Normal post-repair valuation remains owned by the daily market lane;
the repair collector is not a second recurring writer.

## 3. Selection And Quality Gates

The builder first freezes a source inventory and a deterministic selection digest. Every top-level
member of `/root/work/astock/data` and `/root/work/astock/minute/data` must be included or excluded
by a versioned reason. Mixed roots such as `data/revisions` and `data/shadow` are expanded until
each discovered source unit is explicitly included or inherits a reviewed exclusion; a broad
`mixed` label cannot hide an unresolved child. Unknown members fail closed.

The inventory also carries machine-readable source-family scope gaps. The current provisional
policy still names raw F10 and full financial statements as `not_formally_acquired`; this remains
true until the first accepted acquisition is retained, the policy selects those exact bytes, and a
new Base revision passes the same gates. Merely merging collector code cannot close the gap.

Selected files bind source path, target path, byte size, SHA-256, format, schema hash, row count,
null counts, and declared date bounds. Keyed Parquet datasets additionally require zero null keys
and zero duplicate keys across all parts. SQLite sources pass `PRAGMA integrity_check`. Daily and
intraday release bytes must match their immutable release manifests and `READY` digests.

Hermes is the default source. An alternate retained Hermes revision or CN01 gap-fill is legal only
for a specific intraday date and frequency named by policy. The exception binds the source node,
safe source path, reason, and expected release-manifest digest. No row-level favorable merge or
implicit fallback is allowed.

The build re-evaluates the exact source selection after the inventory is frozen. Any byte,
selection, schema, or policy change fails the inventory binding and requires a new inventory and,
when identity changes, a new package id.

## 4. Package And Lifecycle

```text
/srv/quanthus/ods/
├── base/
│   ├── ods-base-through-20260730-v1/
│   ├── ods-base-202607-v1/
│   └── BASE_CURRENT.json
├── daily/
├── weekly/
└── CATALOG_CURRENT.json
```

Packages are built in unique hidden staging directories, verified, and atomically renamed into an
immutable package id. `READY` is written last. `BASE_CURRENT.json` advances only after a deep
verification and compare-and-swap validation. It does not change a legacy ODS pointer or consumer
publication pointer.

The provisional package ends at 2026-07-30. After the normal July 31 market, event, 1m, and 5m
releases are accepted on both nodes, the final July package may be built as a child of the
provisional package. Unchanged files may be hard-linked locally, but the final package receives a
new manifest and content identity bound to the July 31 releases. Passing this close-release gate
authorizes candidate construction only. Final promotion also requires the standard daily/weekly
publication surface, node-local ODS catalog, governed recurring-source completeness dispositions,
coverage matrix, and accepted normal daily and weekly cycles defined by Issue #47 Phase B.

The cadence tree is an ODS concern. DWD/DWS bind one exact verified ODS catalog and ADS normally
binds one exact canonical DWD/DWS release set; neither derived layer independently reinterprets the
precedence of Base, daily, weekly, or correction releases. The provisional Base and source releases
stay retained; deletion is outside this work item.

## 5. Recovery Contract

Hermes and CN01 must independently deep-verify the same package id, manifest SHA-256, and content
digest. A clean-path restore must discover and read every member using only the package manifest,
without access to `/root/work/astock` or another producer path. Interrupted build or transfer
staging has no `READY` and cannot advance `BASE_CURRENT.json`.

The ODS Base is a reconstruction and recovery seed, not a daily merge target. Normal post-Base ODS
remains immutable daily release files. A later compaction creates a new Base revision; it never
appends into or edits an existing Base.
