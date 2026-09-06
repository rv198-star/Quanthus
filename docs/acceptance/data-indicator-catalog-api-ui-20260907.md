# P3-DICT-001 C/D: read-only Catalog API and Web UI acceptance

| Field | Value |
| --- | --- |
| Date | 2026-09-07 |
| Parent | P3-DICT-001 / #215 |
| Children | C #225, D #226 |
| Governing design | `../design/data-indicator-catalog.md` |
| Catalog projection | 664 entries / 8 explicit gaps |
| Authority | Read-only discovery surface only |

## 1. Platform API

The enabled Platform feature `quanthus-data-catalog-v1` adds no migration, startup hook, database
table or source capability. It admits exactly four authenticated GET operations:

```text
GET /api/platform/v1/catalog/summary
GET /api/platform/v1/catalog/search
GET /api/platform/v1/catalog/items/<entry_id>
GET /api/platform/v1/catalog/items/<entry_id>/relations
```

`catalog:read` is granted to viewer, operator and admin roles. Search bounds the query to 120
characters and result limit to 1..100; unknown/duplicated query parameters, invalid kinds/statuses
and unknown entries fail closed. Detail and relation endpoints use exact entry identities. No POST,
PATCH or DELETE Catalog route exists.

The API reads the deterministic authority projection built at composition time and does not query
market values or persist metadata in PostgreSQL.

## 2. Web product

The first-class `/catalog` route reuses the existing same-tab Platform bearer session. Anonymous
visitors see an explicit login-required state linking to `/operator`. Authenticated users receive:

- summary counts and visible gap ledger;
- free-text search plus exact kind/status/domain/family filters;
- deterministic result list;
- detail view with lifecycle/verification/accepted scope;
- authority path/pointer/SHA;
- typed definition/calculation details and caution text;
- outgoing and incoming typed relations with direct navigation;
- canonical-id copy action.

Both the attribution workbench and operator console link to `/catalog`. There is no formula editor,
indicator creator, Catalog write action or one-click strategy promotion.

## 3. Focused API verification

Python focused gate:

```text
32 passed
```

It covers Catalog core, viewer RBAC, authentication, summary/search/detail/relations, query bounds,
GET-only OpenAPI admission and Platform manifest boundaries. Ruff passed on the affected Python.

## 4. Web verification

After a disk-capacity issue exposed by an interrupted dependency install, only regenerable npm/npx
caches were cleaned; no DWD, Run, database, repository or unrelated project data was removed.
The exact `package.json` and lockfile were unchanged. Dependencies were then installed from the
existing lockfile with npm cache/logs directed away from persistent storage.

Observed Web gates:

```text
ESLint: PASS
vinext build: PASS
routes include /, /catalog, /operator
SSR/source contract tests: 17 passed / 0 failed
```

The build used Node 22.23.1 and the repository lockfile. npm reported five dependency audit findings
(1 moderate, 4 high); no automatic dependency upgrade was made because that would be a separate
version/security change.

## 5. Claim ceiling

C/D prove an authenticated read-only search/detail product over the frozen Catalog projection. They
do not prove every external source schema is catalogued, do not create calculation authority, and do
not authorize models, strategy changes, B6-B11 Structure work, Shadow, paper or live execution.
Drift/maintenance and full-repository closeout remain child E.
