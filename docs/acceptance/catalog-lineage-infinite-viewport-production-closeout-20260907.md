# P3-DICT-002 Infinite Viewport Production Closeout — 2026-09-07

## Decision

PASS. P3-DICT-002-C / #230 is requalified and P3-DICT-002-D / #231 may close. Parent P3-DICT-002 / #232 may close after planning/document state synchronization.

## Production release

Runtime commit:

```text
73f1b409dfce13daccbb690efb3f041cc285a231
```

Production update was Web-only.

- Web image: `quanthus-platform-web:git-73f1b40`
- Web image digest: `sha256:494416412b8f1c70fd05900c55ca03e3dd728a2a8bd0d54c47d1de1796b216b2`
- API image remains `quanthus-platform-kernel:git-0c671f9`
- PostgreSQL, DWD, replay-worker and event-worker were not redeployed.
- Production checkout is clean.

Production health after deployment:

```text
/catalog                                      200
/ready                                        200 postgres=true
/api/platform/v1/catalog/summary (anonymous)  401 authentication_required
API health                                    200 healthy
Web health                                    healthy
```

## User-visible correction

The previous fixed-height renderer still used DOM scroll offsets and was not a true infinite canvas. It has been replaced by an independent world-coordinate viewport:

```text
{x, y, zoom} -> translate(x,y) scale(zoom)
```

No graph-content scrollbar or finite scroll origin remains.

Current behavior:

- graph load / graph switch performs a true fit-and-center by world bounds;
- default graph center is aligned to viewport center without requiring a manual Center action;
- background drag pans without content-bound clamping;
- wheel zoom is anchored at pointer location;
- two-pointer pinch zoom/pan is implemented around gesture center;
- floating controls provide zoom in/out, fit-all, focus-center and 1:1;
- lower-right MiniMap shows graph distribution and current viewport, and supports click/drag recentering;
- dotted world background follows translation and zoom;
- nodes remain read-only and clickable only for the existing lineage-refocus behavior.

## Browser acceptance

`npx playwright test --config playwright.catalog.config.ts`:

```text
3 passed
```

Both 1440px desktop and 390px narrow runs verify:

- fixed outer frame height (520px desktop / 420px narrow);
- initial fit is genuinely centered: graph and viewport center delta < 3px;
- zoom controls and 1:1 work;
- fit-all re-centers the complete graph;
- focus-center places the current lineage center node at viewport center;
- real pointer drag changes world translation by >150px without scroll offsets;
- wheel interaction changes zoom;
- MiniMap is visible;
- exact node identities / existing lineage graph behavior remain available;
- no page-level horizontal overflow;
- anonymous readers still receive only the login boundary.

Web gates:

```text
npm run lint                              PASS
npx tsc --noEmit --project tsconfig.catalog.json  PASS
npm test                                  18/18 PASS
```

Full repository gate:

```text
1590 passed / 7 existing environment skips
```

The skips are unchanged environment-bound PostgreSQL / production-artifact tests and are unrelated to this Web-only repair.

## Semantic audit

PASS.

The runtime/data graph itself did not change. The repair modifies only:

- `web/app/catalog-lineage.tsx`
- `web/app/catalog-browser.module.css`
- Web interaction tests
- productization planning/design/acceptance records

It does not modify `src/quanthus/catalog_lineage.py`, `config/catalog-lineage.v1.json`, DWD, Facts, indicators, strategy rules or execution permissions. Existing `field_proven` / `table_context_only` claims and exact lineage identities therefore retain the previous evidence boundary.

## Reference / dependency boundary

React Flow / xyflow was reviewed as the primary open-source interaction reference because its viewport model follows slippy-map pan/zoom semantics and exposes fitView, Controls, MiniMap and Background. The production repair adopts that interaction contract but adds no new runtime dependency; the current SVG graph remains the renderer behind the independent viewport.

## Claim ceiling

“Infinite canvas” means unbounded viewport translation in the browser. It does not mean unbounded lineage retrieval. API depth and node limits still govern which nodes and edges exist in the graph, and panning cannot fabricate or fetch undeclared lineage.
