# P3-DICT-001: Data & Indicator Catalog final acceptance

| Field | Value |
| --- | --- |
| Date | 2026-09-07 |
| Parent | P3-DICT-001 / #215 |
| Children | #223-#227 |
| Governing design | `../design/data-indicator-catalog.md` |
| Accepted scope | Repository-authoritative read-only Catalog, Platform GET API and `/catalog` UI |
| Formula / strategy / execution authority | NONE |

## 1. Final product

Quanthus now has one deterministic read-only Catalog projection for human and AI discovery. The
Catalog does not own a parallel schema/formula database. It rebuilds from an explicit list of
repository authorities and checks a frozen source/projection digest manifest before Platform
composition serves it.

Current v1 projection:

```text
664 entries
  33 DWD tables
  524 DWD fields
  53 repository machine-declared ODS datasets
  9 Feature specs
  4 LabelCards
  20 semantic families
  21 semantic concepts
8 explicit gaps
```

Full deterministic projection digest:

`sha256:6f3d1a4dc804d6c1d5d05b47e1b466e3cc0c80d77f6ff0a7054c5201c184a682`

The small checked snapshot manifest is `config/data-indicator-catalog-snapshot.v1.json`; the full
projection is rebuilt from `config/data-indicator-catalog-sources.v1.json` rather than committed as
a second large metadata truth.

## 2. Authority and semantics

DWD entries come from the versioned machine domain model plus approved extension and include
inherited lineage/time-profile fields. ODS v1 remains dataset-level where the repository machine
contract is authoritative; the accepted external Base package manifest and complete ODS source
field schemas are not fabricated from documentation. Feature and Label formulas/rules remain owned
by their registries. Semantic concepts remain distinguishable from executable specs.

Required regressions are frozen:

- existing SMA20 is total-return-index based and not a raw-price MA20 alias;
- existing RSI14 is not silently converted to Wilder RSI;
- MACD remains a reserved concept without an accepted executable spec;
- DWD `turnover` is not silently presented as turnover-rate;
- source/time metadata cannot be upgraded into stronger historical availability claims.

Eight gaps remain first-class Catalog results rather than hidden omissions or zero values.

## 3. API and UI

Platform feature `quanthus-data-catalog-v1` adds no migration, startup hook, job or write path.
Viewer/operator/admin roles have `catalog:read`. The only Catalog HTTP operations are four GETs for
summary, bounded search, exact detail and typed relations.

`/catalog` is a first-class authenticated Vinext page with:

- search and exact filters;
- result/detail navigation;
- lifecycle, implementation, verification and accepted-scope badges;
- source authority path/pointer/SHA;
- formula/spec/detail display and caution annotations;
- visible gap ledger;
- outgoing/incoming typed relation navigation.

The attribution workbench and operator console link to the Catalog. There is no formula editor,
indicator authoring, metadata write backend or strategy-promotion action.

## 4. Maintenance / drift gate

The repository gate now checks:

- exact explicit Catalog source adapter inventory and source file existence;
- source/snapshot digest reconciliation;
- duplicate identities and dangling relations;
- exact DWD inherited/time/extension projection;
- Catalog Platform feature has no migration/write surface;
- `/catalog` and issue plan trace to the governing design;
- annotations are search/caution-only and cannot grant equivalence;
- required SMA/RSI/MACD/turnover regressions;
- generated projection drift through the small snapshot manifest.

Source authority changes that alter the projection require the snapshot manifest to change in the
same repository revision.

## 5. Verification

Focused Catalog/Platform/document gate:

```text
70 passed
Catalog snapshot --check: PASS
Ruff on changed Python: PASS
git diff --check: PASS
```

Repository gate before lifecycle closeout:

```text
make check:
1553 passed
7 skipped
```

The seven skips are existing unmounted production expert artifacts and unconfigured PostgreSQL
integration fixtures. They are not counted as passes.

Full Web gate:

```text
npm run lint: PASS
vinext build: PASS
routes: /, /catalog, /operator
npm test: 17 passed / 0 failed
```

The lockfile and package versions were unchanged. A partially attempted install exposed the host
root filesystem capacity limit; only regenerable npm/npx caches were cleared, and the final install
used the repository lockfile with npm cache/logging redirected away from persistent storage. No
DWD, Run, PostgreSQL, repository history or unrelated project data was removed. npm reported five
existing dependency audit findings (1 moderate, 4 high); no automatic dependency upgrade was made
under this work item.

## 6. Claim ceiling

This acceptance proves a versioned discovery and maintenance surface, not completeness of every
possible financial concept or indicator. It does not add MACD/CCI/KDJ/ATR calculation specs, prove
missing ODS schemas, change existing Feature formulas, open Structure B6-B11, train models, or grant
Shadow/paper/live execution authority. Catalog entries describe the authority that exists; they do
not create authority by being searchable.
