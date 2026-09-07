# Catalog lineage single-canvas production closeout — 2026-09-07

## Release

- Production checkout: `d4e71585a58a229fa8c7ddbb9515e4038dcb2aeb`
- Web image: `quanthus-platform-web:git-d4e7158`
- API unchanged: `quanthus-platform-kernel:git-0c671f9`
- Deployment scope: Web only; no API rebuild, migration, DWD change or Worker release.

## Product change

For `table_context_only` DWD fields, the focus UI now uses a single lineage canvas. The previous isolated-field canvas plus second “所属表已证明的加工上下文” section has been removed. A compact `表级上下文` notice explains that the displayed parent-table processing graph is not exact field-level proof. `field_proven` nodes continue to display exact field lineage.

The focus heading is unified as `血缘关系`. Node/edge/proof summary is compact. Scope, truncation and evidence details are collapsed under `范围、限制与加工依据`.

## Verification

- Web ESLint: PASS.
- Catalog strict TypeScript: PASS.
- Web build/tests: `18 passed`.
- Chromium desktop/narrow/anonymous: `3 passed`.
- Focused Catalog/document tests: `68 passed`.
- Full repository gate: `1590 passed / 7 existing environment skips`.
- Catalog snapshot exact: 664 entries / 10 explicit gaps; digest unchanged.
- Production `/catalog`: 200.
- Production `/ready`: 200 with PostgreSQL true.
- Anonymous Catalog API: 401 as required.
- Production Web/API health: healthy.

## Semantic boundary

This release changes presentation only. It does not modify lineage nodes or edges, proof classification, ODS/DWD, Facts, feature formulas, strategy logic, execution authority, database schema or Worker runtime.
