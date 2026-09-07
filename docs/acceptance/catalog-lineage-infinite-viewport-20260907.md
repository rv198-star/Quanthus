# P3-DICT-002-C Infinite Viewport Requalification — 2026-09-07

## Decision

PASS for implementation / browser requalification. Production release remains under P3-DICT-002-D.

This requalification replaces the accepted-but-unsatisfactory scroll-container view layer. It does not change Catalog graph semantics, lineage evidence, DWD, Facts, strategy rules or execution authority.

## External interaction reference

The redesign reviewed the current React Flow / xyflow interaction model as the primary open-source reference for node-based infinite canvases:

- the viewport uses an independent `{x, y, zoom}` coordinate system rather than DOM scroll offsets;
- default interaction follows slippy-map conventions: pointer drag pans, wheel/pinch zooms;
- `fitView` pans and zooms to fit all nodes;
- standard overlays include Controls, MiniMap and Background;
- React Flow is MIT licensed.

Cytoscape.js was also reviewed as a graph-oriented alternative with `center()` / `fit()` viewport primitives. tldraw is a stronger free-form infinite-canvas SDK, but its current production SDK license is not the preferred baseline for this read-only graph surface.

No new runtime package is introduced in this bounded repair. The existing SVG renderer now adopts the same viewport model and interaction contract so the change remains Web-only and does not expand dependency/build authority. A future renderer swap to `@xyflow/react` remains possible without changing the lineage API.

## Replaced behavior

Previous implementation:

```text
fixed container
  -> overflow:auto
  -> scrollLeft / scrollTop
  -> scaled SVG content
```

This had finite scroll bounds and could not guarantee a genuine centered default view.

Current implementation:

```text
fixed viewport (clips only)
  -> independent world coordinates
  -> translate(x,y) scale(zoom)
  -> x/y are not clamped to content bounds
```

The page and graph viewport have no graph-content scrollbars.

## Required interaction contract

- Desktop frame height: 520 px.
- Narrow frame height: 420 px.
- Initial load / graph switch: true fit-and-center using world bounds and live viewport size.
- Pan: background pointer drag, unlimited in either direction.
- Zoom: wheel anchored at pointer position; two-pointer pinch supported around gesture center.
- Controls: zoom in/out, fit-all, center current focus, 1:1.
- MiniMap: node distribution + current viewport rectangle; click/drag recenters the viewport.
- Background dots follow world translation and zoom.
- Nodes remain read-only/non-draggable; node click retains the existing lineage refocus action.
- All view operations are presentation-only.

## Verification

Repository / source contract:

- `web/app/catalog-lineage.tsx` no longer contains `scrollLeft`, `scrollTop`, `scrollWidth` or `scrollTo(`.
- viewport state is explicit `{x,y,zoom}` and rendered as one transform.
- `ResizeObserver` supplies live canvas dimensions for initial fit.
- exact lineage identities and proof/context boundaries from prior #230 requalifications are unchanged.

Web gates:

```text
npm run lint
npx tsc --noEmit --project tsconfig.catalog.json
npm test
npx playwright test --config playwright.catalog.config.ts
```

Observed:

- Web contract/unit bundle: 18/18 PASS.
- Chromium catalog suite: 3/3 PASS.
- Full repository `make check`: 1590 passed / 7 existing environment skips.
- 1440px and 390px runs both verify fixed frame height, true initial graph centering (<3 px center delta), zoom controls, fit centering, focus centering, real pointer drag translation, wheel zoom, visible MiniMap, preserved exact lineage nodes and no page-level horizontal overflow.
- anonymous boundary remains intact.

## Claim ceiling

This is an infinite *viewport interaction model*, not an infinite data query. Graph depth and node budgets remain governed by the existing Catalog relations API. Panning farther does not query or synthesize additional lineage. Static declared lineage remains distinct from actual Run consumption evidence.
