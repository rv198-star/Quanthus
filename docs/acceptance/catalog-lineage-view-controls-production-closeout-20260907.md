# Catalog 血缘画布视图控制生产收尾 — 2026-09-07

| 字段 | 值 |
| --- | --- |
| Work item | `P3-DICT-002-D` / #231；parent #232 |
| Disposition | PASS / production accepted |
| Runtime checkout | `d485508f8cac3e5c590d34afb36e963022da05ce` |
| Web image | `quanthus-platform-web:git-d485508` |
| Web image digest | `sha256:eecae51706caee24235268d9115dd5654834b210b423e17f7952c70771d88e60` |
| API image | unchanged `quanthus-platform-kernel:git-0c671f9` |
| Data / DB / Worker change | none |

## 1. Production change

Only the Web component was deployed from exact origin/main commit
`d485508f8cac3e5c590d34afb36e963022da05ce`. The dry-run plan admitted exactly `web`; API,
PostgreSQL, Replay Worker, Event Worker, DWD and governed data products were not rebuilt or modified.

The deployed Catalog lineage canvases now share the same view-management contract:

- desktop fixed viewport height 520px;
- narrow viewport height 420px;
- zoom out / current percentage reset to 100% / zoom in;
- fit-all based on current viewport and graph geometry;
- center on current focus node, or graph center when no focus exists;
- blank-area pointer drag pan inside the canvas.

These controls affect browser viewport state only. Lineage nodes, edges, evidence, proof level,
query depth and semantic identities are unchanged.

## 2. Production health and edge smoke

Deployment tool reported Web healthy after replacement. Post-deploy production status confirms:

- production checkout `d485508f8cac3e5c590d34afb36e963022da05ce`;
- Web image `quanthus-platform-web:git-d485508`, healthy;
- API remains `quanthus-platform-kernel:git-0c671f9`, healthy;
- PostgreSQL healthy and readiness `postgres=true`;
- Event/Replay Workers remained on their pre-existing images.

Public edge smoke:

```text
/catalog -> 200
/ready -> {"checks":{"postgres":true},"status":"ready"}
unauthenticated Catalog relations -> 401
```

## 3. Verification evidence

Implementation evidence is retained in
`catalog-lineage-view-controls-20260907.md`:

- Catalog-scoped TypeScript PASS;
- ESLint PASS;
- Web build + Node tests 18/18 PASS;
- Chromium desktop / 390px / anonymous 3/3 PASS, including exact fixed heights, zoom, reset,
  fit, center and real drag-pan interaction;
- focused Python/document checks 68 PASS;
- repository `make check` under the project accepted virtual environment: 1590 PASS / 7 existing
  environment skips.

The first plain `make check` attempt used `/usr/bin/python3`, which did not contain pytest and stopped
before tests began. The same gate under the project environment completed successfully; no test
failure was hidden.

## 4. Claim ceiling

This closeout proves the Catalog browser has bounded view controls over the previously accepted static
lineage graph. It does not expand exact field-lineage coverage, add run-time consumption evidence,
modify formulas/Facts/labels, or grant strategy/execution authority.

Conclusion: `P3-DICT-002-D` and parent `P3-DICT-002` may close again. The runtime remains the exact
Web release above; later documentation-only closeout commits do not require a production rebuild.
