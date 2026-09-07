# Catalog exact-node identity / table-context readability production closeout — 2026-09-07

| Field | Value |
| --- | --- |
| Parent | `P3-DICT-002` / #232 |
| Requalified child | `P3-DICT-002-C` / #230 |
| Release child | `P3-DICT-002-D` / #231 |
| Production revision | `0c671f92e58834d49cdb182d3c58793d052d9814` |
| Previous production revision | `c9bbd0721ec45e3437c64d029c4cf5efbd37c64d` |
| Disposition | PASS |
| Data / formula / execution authority | unchanged |

## 1. User-visible repair

The triggering rendered graph showed multiple distinct adjustment-factor sources with the same
generic label and a second column of generic normalization boxes. That made the graph technically
connected but operationally unreadable.

The production Catalog now renders every lineage node with two separate identities:

1. a business-readable label;
2. the exact technical identity (canonical table/field/dataset/artifact/projection ID).

Dataset-level source context is now a direct source -> target-table relationship. A separate process
node is retained only when the transformation itself is meaningful evidence, such as a field-level
conversion, indicator calculation or Facts process.

For `dwd_adjustment_factor`, the visible source set is now unambiguous:

```text
带时间版本的复权因子来源       adjustment_factors_pit
日发布复权证据增量             adjustment_evidence_delta
日发布因子证据增量             factor_evidence_delta
周发布复权证据                 adjustment_evidence
                               -> dwd_adjustment_factor
```

No generic `process:release_artifacts-*` normalization boxes occur in that table-level focus.
`dwd_adjustment_factor.effective_from` separately retains its exact `field_proven` chain.

## 2. Semantic boundaries retained

DWD field proof classification remains:

```text
fields:             524
field_proven:        10
table_context_only: 514
unregistered:          0
```

The graph has 259 registered processing edges after removing redundant dataset-level process boxes.
This lower edge count is intentional simplification, not lost source evidence: direct dataset edges
retain the same authority path, SHA-256 and contract locator.

A parent-table context still does not mean every field consumes every table source. `contains`,
`field_of` and conceptual relations remain outside the processing graph. Static declared lineage is
not actual historical Run consumption evidence.

## 3. Repository verification

Focused Catalog/API and documentation checks:

```text
51 focused Catalog/API tests: PASS
68 combined document/Catalog requalification tests: PASS
Catalog snapshot --check: PASS
snapshot digest: sha256:540a4b13be382e27df9475404df8a30174404d7f892224ffe0c01f42ec2ebb8b
git diff --check: PASS
```

Web verification:

```text
npm run lint: PASS
npm test: 18 passed
Chromium Playwright: 3 passed
  - 1440px
  - 390px
  - anonymous boundary
```

Full repository gate before commit/push:

```text
make check PYTHON=/var/lib/devspace/worktrees/repo-d9c9fcbe/.venv/bin/python
1590 passed
7 skipped
```

The seven skips are the existing unconfigured PostgreSQL fixtures and unmounted production expert
artifacts and are not counted as passes.

## 4. Production deployment

Only API and Web were rebuilt/deployed. PostgreSQL, DWD and both Workers were outside the deployment
scope.

```text
API image: quanthus-platform-kernel:git-0c671f9
API digest: sha256:2b13bb2409062e276392ff0b4b5cae6a6b2195902b5358380d9b427b2b90cd05
Web image: quanthus-platform-web:git-0c671f9
Web digest: sha256:b58151cf6e8143543acc27723b9976a5799fbb5aaa45ac32dd58ab26276fa402
health: 200 / healthy
ready: 200 / postgres=true
web_ready: 200
production_dirty: false
```

Public-edge smoke:

```text
GET /catalog -> 200
GET /api/platform/v1/catalog/items/dwd_table:dwd_adjustment_factor/relations (anonymous) -> 401
GET /ready -> 200 / postgres=true
```

Authenticated graph semantics were exercised by the exact revision's API and Playwright suites;
this closeout does not create or expose a production credential merely to duplicate that check.

## 5. Final disposition

`P3-DICT-002-C`, `P3-DICT-002-D` and parent `P3-DICT-002` may close. The repair changes no ODS/DWD
values, Feature formulas, Label rules, Facts values, expert mappings, P2 behavior, Worker runtime,
Shadow/paper/live authority or historical accepted Run.
