# Platform Kernel PK1-PK3 Acceptance

| Field | Value |
| --- | --- |
| Evidence status | accepted |
| Evidence date | 2026-08-12 |
| Accepted gates | PK1 Minimal Boot; PK2 Structural Slimming; PK3 Quanthus Integration |
| Next gate | PK4 Operator Web open |
| Feature Manifest | `config/platform-features.v1.json` |
| Gate source | `config/platform-kernel-gates.v1.json` |
| Governing design | `docs/design/lld/11-platform-kernel.md` |
| Operations | `docs/operations/platform-kernel-runbook.md` |

## Result

PK1, PK2, and PK3 are accepted in sequence. The extracted Platform Kernel boots as a bounded
single-worker API with real PostgreSQL, contains only the admitted identity, operational, job,
and Quanthus-adapter surfaces, and preserves Quanthus authority over domain facts and actions.
PK4 Operator Web is now open. PK5 remains deferred and PK6 remains blocked.

## Real Runtime Evidence

The repository-owned `make platform-docker-acceptance` runner passed against PostgreSQL 16.14 and
the production Gunicorn app factory on 2026-08-12:

- one test passed in 6.09 seconds with no failure or skip;
- clean migration created exactly `platform_jobs`, `platform_schema_migrations`,
  `platform_security_audit`, `platform_sessions`, and `platform_users`;
- the migration ledger contained exactly `identity-0001` and `jobs-0001`, a second migration was a
  no-op, readiness passed, and the disposable schema was removed;
- login, encrypted TOTP MFA, RBAC, gate query, attribution verification, idempotent duplicate
  submission, and logout passed over a real HTTP socket;
- the removed-route probe returned 404 and the attribution job reached `succeeded` with one
  persisted job after duplicate submission; and
- Compose used an internal-only network, PostgreSQL tmpfs, no published database port, a read-only
  repository mount, fresh evidence files, and project-scoped cleanup.

Generated machine evidence is intentionally ignored by Git. The accepted run is bound by these
digests:

| Generated evidence | SHA-256 |
| --- | --- |
| `artifacts/platform-kernel/platform-kernel-postgres-runtime.json` | `aa46ce89de34bf93363387ee3a2cb4e36057397601e8a62b5cdb0a6d7fb2c785` |
| `artifacts/platform-kernel/platform-kernel-postgres-junit.xml` | `ca77054942bcae20183f597c681ba15c0c9f430a27c05ddb03dfce949071269e` |

## Production Image Evidence

The production image `quanthus-platform-kernel:pk1-pk3-20260812` was built through the exact-context
repository builder and inspected. This avoids legacy Docker failing while it stats unrelated,
restricted repository paths before applying `.dockerignore`:

| Property | Accepted value |
| --- | --- |
| Image id | `sha256:aeebaa83dd7cebd503c2f2e84d50919a4f97ca655eaac5c2ffd1628ba93aefe9` |
| Size | 60,910,599 bytes |
| Architecture | `linux/arm64` |
| Runtime user | `quanthus` (`uid=10001`) |
| Default command | `gunicorn --bind=0.0.0.0:8080 --workers=1 quanthus.platform.server:create_app()` |

The image contains the `quanthus.platform` package, the five exact attribution/policy modules it
calls, and the two Platform JSON configurations. Provider, backtest, event-library, MCP, AI,
broker, payment, mobile, Redis, and Celery implementations are absent. The `quanthus-platform`
command is present and its help command passes. During final image inspection, the source file
mode caused both copied JSON files to become root-only. The Dockerfile now normalizes them to
`0444`; a contract assertion guards the build instruction, uid 10001 reads both files, and the
image's default Gunicorn command connected to disposable PostgreSQL and returned a healthy response
over a real published socket.

## Focused Regression Evidence

- Platform and planning contracts: `29 passed, 1 skipped`.
- Platform plus existing attribution-publication regression: `151 passed, 1 skipped`.
- In both runs, the only skip was the host-DSN variant of the real PostgreSQL acceptance test; the
  Docker run above executed that same test and passed with no skip.
- Ruff, Ruff format, strict mypy for the Platform source, compileall, JSON parsing, Compose static
  rendering, Platform Doctor, planning preview, and TPlan Mission schema validation passed.

Repository-wide `make check` could not traverse pre-existing P4 event-library source and three
event tests owned by `nobody:nogroup`; document-traceability collection encounters the same ACL.
Those files were not changed, their permissions were not widened, and the limitation is not
counted as Platform evidence. The bounded Platform and attribution regressions above cover the
accepted PK1-PK3 surface.

## Gate Conclusions

### PK1 Minimal Boot: accepted

The Feature Manifest and sole composition root admit only approved routes, migrations, process
roles, dependencies, and operational checks. Real PostgreSQL/Gunicorn boot, retained workflows,
negative reachability, and exact schema assertions pass.

### PK2 Structural Slimming: accepted

Module migrations replace the shared bootstrap, the production image has the bounded dependency
and module closure, and identity/MFA/RBAC have no credit, membership, payment, provider, broker,
or strategy-runtime dependency.

### PK3 Quanthus Integration: accepted

`quanthus.gate-query.v1` and `quanthus.attribution-verification.v1` provide the selected query and
finite-job paths. The attribution path calls the authoritative Quanthus deep verifier, returns an
artifact reference, and passes authority-import, idempotency, recovery, cancellation, and
concurrency contracts.

This record supersedes
`docs/acceptance/platform-kernel-pk1-pk3-implementation-checkpoint-20260812.md`, which captured the
earlier provisional implementation state before Docker daemon access was available.
