# Catalog lineage single-canvas requalification — 2026-09-07

## Scope

This requalification addresses the user-visible duplication for `table_context_only` DWD fields. The previous UI rendered an isolated field canvas and then a second large section titled “所属表已证明的加工上下文”. The semantic separation was correct but the product representation was redundant and consumed two canvas heights.

## Change

- The focus section title is now the neutral `血缘关系`.
- `field_proven` and normal registered nodes keep their existing exact lineage canvas.
- `table_context_only` fields now render exactly one canvas: the already-proven parent-table processing context.
- A compact inline notice says that the current field has no exact field-level mapping and that the displayed table context must not be interpreted as proof that the field participates in every shown relation.
- The old second-level heading and separate context panel are removed.
- Node/edge counts and proof level are reduced to a compact metadata line.
- Scope, truncation information and the edge/evidence list are collapsed under `范围、限制与加工依据`.
- Infinite viewport, minimap, zoom/pan controls, graph nodes, graph edges and evidence remain unchanged.

## Browser acceptance

Real Chromium was run at 1440x1000 and 390x844 plus the anonymous boundary case.

Acceptance assertions include:

- `dwd_adjustment_factor.effective_from` still displays `字段级血缘` and exact field lineage.
- `dwd_adjustment_factor.factor_value` displays `表级上下文`.
- the table-context focus section contains exactly one `lineage-canvas`.
- the former visible heading `所属表已证明的加工上下文` does not exist.
- the parent-table context remains visible through the existing `table-lineage-context` test hook.
- the compact warning preserves the non-equivalence between table context and exact field mapping.
- desktop/narrow infinite viewport behavior and page overflow checks still pass.

Result: Playwright `3 passed`.

## Engineering checks

- Web ESLint: PASS.
- Catalog strict TypeScript: PASS.
- Web build and Node tests: `18 passed`.
- No Catalog data contract, API route, lineage node/edge, DWD, Facts, strategy, execution permission or Worker change is part of this repair.

## Claim ceiling

This is a presentation compaction only. `table_context_only` remains table-level evidence and must not be upgraded to `field_proven` by the single-canvas presentation.
