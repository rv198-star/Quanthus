# Data & Indicator Catalog production deployment incident and resolution — 2026-09-07

| Field | Value |
| --- | --- |
| Work item | `P3-DICT-001-E` / parent `P3-DICT-001` |
| Incident status | RESOLVED |
| Final production revision | `f06a8882c33caa6e5442bf17e21fed8786939929` |
| API image | `quanthus-platform-kernel:git-f06a888` / `sha256:c19f1f3d98faa024c2ada0a24118a586ba33ed4ed4cffff2015ac1ec186d4001` |
| Web image | `quanthus-platform-web:git-f06a888` / `sha256:7d2b2a3a9d28f6ec04b2e4273abecd34bc83d6b7b86f37aff3bee8091816b6a4` |
| Final disposition | production API/Web healthy; Catalog closeout eligible |

## 1. Repository state before production deployment

The Catalog implementation and release packaging had already passed:

- deterministic Catalog snapshot: 664 entries, 8 explicit gaps, digest
  `sha256:6f3d1a4dc804d6c1d5d05b47e1b466e3cc0c80d77f6ff0a7054c5201c184a682`;
- read-only API and `/catalog` Web route tests;
- Web build and 17/17 Web contract tests;
- repository `make check`: 1554 passed, 7 expected environment skips;
- Platform image-boundary checks for the complete Catalog authority closure.

The main implementation was `d7e05cfa94aa41d12fbe5a48f80872bb8ee8e879`; the initial image
packaging repair was `a15ae68daf5c254309bc7dcc73a88320cfd4c21c`.

## 2. Incident sequence and root cause

The first governed API/Web deploy failed and rollback temporarily left the Web container created and
an API container cycling. Two issues were separated rather than treated as one Catalog defect:

1. the Web deploy/rollback path emitted an unintended registry-pull error while local Git-tag images
   were being selected;
2. the new API image failed after the launcher dropped from root to uid 10001 because the packaging
   repair used `chmod -R a-w ./config`. That removed write bits but did not guarantee world-readable
   config files copied from the build context. A production-equivalent isolated run reproduced the
   concrete error as `PermissionError: /app/config/platform-features.v1.json`.

The API image permission contract was repaired to make config directories `0555` and config files
`0444`. Focused tests passed 29/29 and the full repository gate remained 1554 passed / 7 skipped.
The repair commit is `f06a8882c33caa6e5442bf17e21fed8786939929`.

A production-Dockerfile image was then validated against an isolated PostgreSQL instance. Full
Platform composition, migrations and startup hooks completed. An isolated real login followed by
`GET /api/platform/v1/catalog/summary` returned 664 entries / 8 gaps.

## 3. Production recovery and deployment

After the old API recovered to a stable healthy state, deployment was split deliberately:

- API-only deployment to `f06a888` succeeded with `/health` 200 and `/ready` 200;
- Web-only dry-run then selected `quanthus-platform-web:git-f06a888`;
- Web-only deployment succeeded and the Web container became healthy/running.

Final production status reports:

```text
production_revision = f06a8882c33caa6e5442bf17e21fed8786939929
production_dirty    = false
API                  healthy / running
Web                  healthy / running
PostgreSQL           healthy
ready                 200
web_ready             200
Event Worker          unchanged at 723f22f
Replay Worker         unchanged at 259a649
```

The Catalog deployment therefore did not rebuild or modify DWD and did not alter the accepted
Structure Facts v2 Event Worker runtime.

## 4. Production smoke

Public production-edge checks succeeded:

- `GET /catalog` -> HTTP 200;
- `GET /ready` -> ready / PostgreSQL true;
- `/openapi.json` contains the four Catalog routes: summary, search, item detail and relations;
- unauthenticated `GET /api/platform/v1/catalog/summary` -> HTTP 401, proving the deployed edge does
  not bypass bearer authentication.

Production credentials were not accessed or fabricated. Successful authentication semantics were
verified with the exact final API image against an isolated PostgreSQL store: a temporary admin was
created, login succeeded, and the authenticated Catalog summary returned `664 / 8`. Repository
RBAC tests independently verify `catalog:read` for viewer/operator/admin and no Catalog write route.

## 5. Disk cleanup performed during recovery

At the user's request, expired Docker state on the DevSpace/rootless daemon was cleaned without
removing running containers or Docker volumes. Unreferenced old images were pruned, then two
3-day-old exited Slidethus full-test containers and their now-unreferenced images were deleted.
Available root filesystem space recovered from roughly 319 MiB to multiple GiB. The rootless cleanup
was separate from the rootful Quanthus production daemon.

## 6. Closeout

All previously blocking conditions are resolved. `P3-DICT-001-E` and parent `P3-DICT-001` may close
in dependency order. No Catalog formula, DWD schema, Facts v2 semantic, strategy policy, execution
authority or trading behavior was changed by the incident repair.
