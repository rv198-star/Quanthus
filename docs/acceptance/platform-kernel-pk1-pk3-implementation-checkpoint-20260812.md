# Platform Kernel PK1-PK3 Implementation Checkpoint

> Superseded on 2026-08-12 by
> `docs/acceptance/platform-kernel-pk1-pk3-acceptance-20260812.md` after the isolated Docker
> PostgreSQL/Gunicorn acceptance passed. This file remains historical evidence of the earlier
> provisional state and is not the current delivery status.

| Field | Value |
| --- | --- |
| Evidence status | passed-with-runtime-blocker |
| Evidence date | 2026-08-12 |
| Delivery gate | PK1 Minimal Boot remains open |
| Current work | `PK-BOOT-001` |
| Feature Manifest | `config/platform-features.v1.json` |
| Governing design | `docs/design/lld/11-platform-kernel.md` |
| Operations | `docs/operations/platform-kernel-runbook.md` |

## Result

The first provisional PK1-PK3 code slice is implemented and its in-process vertical slice passes.
This checkpoint does not accept PK1, PK2, or PK3. Real PostgreSQL clean migration, exact schema,
one-worker Gunicorn readiness, and retained workflow smoke remain unexecuted. The Codex sandbox
cannot access the OCI Docker socket, so the host must execute the repository-owned isolated Docker
acceptance runner and return its JSON and JUnit evidence before this checkpoint can be superseded.

## Implemented Surface

- one versioned Feature Manifest covers all 29 frozen QuantDinger capabilities as exactly admitted
  or excluded and controls routes, hooks, roles, migrations, dependencies, and Web surface;
- dependency-free WSGI transport exposes only health, readiness, metrics, OpenAPI, local auth/MFA,
  user administration, one Quanthus gate query, and finite job operations;
- PBKDF2 password hashing, opaque hashed sessions, encrypted TOTP secrets, role permissions, login
  throttling, and append-only security audit contain no billing or entitlement behavior;
- module migrations create only the platform ledger, users, sessions, security audit, and jobs;
- the local single-process JobRunner persists before dispatch and supports idempotency, progress,
  cooperative cancel, restart dispatch, and artifact-only result references;
- `quanthus.gate-query.v1` reads versioned repository facts and
  `quanthus.attribution-verification.v1` calls the authoritative deep release verifier; and
- the container/Compose skeleton contains only one API worker and PostgreSQL, with attribution
  releases mounted read-only.

## Verification Evidence

- `tests/platform` plus `tests/contract/test_platform_kernel_planning.py`:
  `28 passed, 1 skipped`; the skip is solely the explicit real PostgreSQL acceptance test.
- The broader focused regression including the existing attribution publication suite:
  `47 passed, 1 skipped`; the same PostgreSQL runtime test is the only skip.
- The final jobs, PostgreSQL adapter contract, and HTTP concurrency/regression subset after atomic
  terminal-state fixes: `12 passed`.
- Ruff and Ruff format for `src/quanthus/platform` and `tests/platform`: passed.
- Strict mypy for all 14 `src/quanthus/platform` source files: passed.
- Compileall for the Platform package and tests: passed.
- Feature Manifest and Platform Kernel gate JSON parsing: passed.
- Production and isolated acceptance Compose static rendering: passed. The acceptance topology has
  an internal-only network, no published PostgreSQL port, a PostgreSQL tmpfs data directory, a
  read-only repository mount, stale-evidence deletion, and project-scoped automatic cleanup.
- Real system `cryptography` Fernet/TOTP path over the composed kernel: passed; the stored value did
  not contain the plaintext TOTP secret.
- PostgreSQL adapter contract test: ordered advisory lock, transaction completion, exact migration
  order, ledger writes, readiness query, and second-run no-op passed against a fake DB-API carrier.
  This is adapter evidence only and not a database-runtime substitute.
- Real synthetic attribution chain:
  `AttributionPipeline -> AttributionPublisher -> deep release verifier -> LocalJobRunner ->`
  artifact-only result reference passed, including duplicate idempotency submission.

## Open Runtime Blocker

`tests/platform/test_postgres_acceptance.py` is the only acceptance blocker. It must run with
`QUANTHUS_TEST_POSTGRES_DSN` and pass clean/upgrade migration, exact table allowlist, exact ledger,
one-worker Gunicorn readiness, login/MFA/RBAC, gate query, attribution verification job,
idempotency, encrypted MFA persistence, and logout against real PostgreSQL. A skipped result, a
fake cursor test, or the in-memory store cannot close PK1.

The repository now provides `make platform-docker-acceptance`, which performs that gate in the
fixed `quanthus-pk-acceptance-20260812` Compose project and writes ignored machine evidence under
`artifacts/platform-kernel/`. Docker daemon access remains impossible from the Codex sandbox even
after user authorization; the command must be run in the OCI host terminal. Until its new JSON and
JUnit evidence pass inspection, `minimal_kernel_only_admits_approved_surfaces` stays `pending`, PK1
stays `open`, and PK2/PK3 stay blocked from gate acceptance despite their provisional code being
present.
