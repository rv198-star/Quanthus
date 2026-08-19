# Replay Worker Lean V1 infrastructure checkpoint (2026-08-13)

| Field | Value |
| --- | --- |
| Status | infrastructure checkpoint accepted; producer loop open |
| Work items | `PK-WORKER-001` through `PK-WORKER-008` |
| Product URL | `https://quanthus.oci.061718.xyz/` |
| Governing decision | `docs/architecture/adr-0012-postgresql-worker-runtime.md` |
| Governing design | `docs/design/lld/11-platform-kernel.md` |

## Accepted at this checkpoint

- PostgreSQL migration `jobs-0002-lean-worker` preserves existing Jobs and adds immutable input,
  active concurrency, stage/progress, cancellation, lease, and monotonic generation fields.
- Replay Jobs are claimed with `FOR UPDATE SKIP LOCKED`; PostgreSQL time controls eligibility and
  lease expiry. Progress, renewal, cancellation, and terminal writes reject stale generations.
- Web/API persist, list, read, and cancel replay Jobs but contain no replay execution handler.
- One OCI Replay Worker at concurrency one supervises a Job-specific child process, renews leases,
  observes cancellation, and performs bounded TERM/KILL cleanup.
- Generation-specific staging and immutable file sealing exist outside PostgreSQL Job rows.
- The candidate list and detail routes show persisted Job lifecycle, stage, percentage, attempt,
  cancel, and explicit rerun. A read failure has its own retryable error state and cannot appear as
  an empty history.
- Production remains one PostgreSQL, one API, one Web, and one Replay Worker. Only Web joins the
  Traefik network; the Worker exposes no HTTP port.

## Production evidence

PostgreSQL retains `jobs-0001` and `jobs-0002-lean-worker`. Real two-consumer acceptance passed
mutual exclusion, lease reclaim, stale-generation rejection, and a generation-fenced terminal
write. The temporary test record was removed.

Job `job_c7c24e8bd98e4e25a9ebdaccd524a498` was submitted through the production API and canceled
before claim. It remains immutable acceptance history with state `canceled`, attempt zero, and
claim generation zero. This proves durable submit/list/cancel behavior without starting the long
analytical run.

The deployed local image identities are:

| Image | Identity |
| --- | --- |
| API | `sha256:b3d8ce36b296c4e87bb0a6eef77581b03dab0df33ac2363fb849dff176e1045c` |
| Web | `sha256:2b7b8fccb623d35fef02a4ea0961f680442b5b33bd2b1b0561cae878878e56f0` |
| Replay Worker | `sha256:f228a4e853a5e8e3a3e444da39f4c3e8aa34a4c81ae41e50bf0d65cdd822f6e9` |

Public readiness returned PostgreSQL healthy. Public `/metrics` returned 404 as required by the
edge contract. PostgreSQL, API, Web, and Replay Worker were healthy after deployment.

## Verification

- Platform suite: 56 passed, 1 skipped. The skipped disposable-PostgreSQL harness lacked
  `QUANTHUS_TEST_POSTGRES_DSN`; the production PostgreSQL concurrency acceptance above ran
  separately.
- Focused replay HTTP and queue suite: 11 passed.
- Generic operator/attribution Web suite: 13 passed.
- Product Vue production build: passed. The existing bundle-size warning remains non-blocking.
- Ruff and changed-file whitespace checks: passed.
- Public Playwright: 4 passed on desktop Chromium and Pixel 7. It covered the persisted canceled
  Job, explicit rerun, candidate detail, nonblank chart pixels, removed capabilities, and no
  horizontal overflow.

Screenshots:

- `artifacts/platform-kernel/worker-lean-v1/candidate-detail-desktop-chromium.png`, SHA-256
  `26a4aff1fee21f4fb63d1dc5f91178e771f0b9a479d806fa7164cdeb8622b33f`.
- `artifacts/platform-kernel/worker-lean-v1/candidate-detail-mobile-chromium.png`, SHA-256
  `31f37525e53a127e550a01094a12b89ce3fb1f08ffc61247acc6ad9b4ff4efa4`.

## Open producer-loop gates

This checkpoint does not close `PK-WORKER-000`, `PK-WORKER-004`, `PK-WORKER-005`, or
`PK-WORKER-008`:

- the current five-candidate child command verifies DWD v2.1 authority but still reads the shared
  replay projection; it is not yet a direct DWD/PIT frame provider;
- immutable Run files are sealed, but a database Run index and candidate active-evidence
  compare-and-swap are not implemented;
- the serving evidence projection is not switched from a successful Worker result;
- the approximately 99-minute five-candidate Worker replay, restart/lease recovery, crash
  injection, and non-destructive rollback have not run at this checkpoint.

No result from this checkpoint changes candidate review, governance, or execution authority.
