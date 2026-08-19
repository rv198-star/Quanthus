# P4 Event Worker Implementation Checkpoint

| Field | Value |
| --- | --- |
| Evidence status | implementation checkpoint; not #74 acceptance |
| Evidence date | 2026-08-20 |
| Governing work | `P4-EVENT-002` / GitHub Issue #74 |
| Governing design | `docs/design/lld/06-event-library.md`, `docs/design/lld/11-platform-kernel.md` |
| Worker decision | ADR 0012 named-workflow extension |
| Job kind | `quanthus.p4-event-scan.v1` |
| Queue | `event` |
| Production concurrency | one |

## Decision

The real 2012-2021 P4 scan remains a product-initiated heavy computation, but DevSpace does not
receive direct access to the protected DWD Base. The accepted PostgreSQL Worker Runtime is extended
with one statically admitted Event Worker instead. The Event Worker owns execution mechanics only;
P4 event semantics remain in `quanthus.event_library` and are invoked through fixed CLI contracts.

This checkpoint does not close Issue #74. Production acceptance still requires two independently
submitted Jobs with identical governed input and different idempotency keys to produce identical
Event Library identities and digests.

## Runtime boundary

The Event Worker:

- claims only `queue=event`;
- admits only `quanthus.p4-event-scan.v1`;
- requires an exact 40-character code revision;
- accepts only the frozen paths for P4 source authority, Direct-DWD provider, candidate rule, and
  split policy;
- executes `run-p4-event-scan` in an isolated child process;
- verifies the result through a separate fixed `verify-p4-event-scan` child invocation rather than
  importing P4 domain semantics into the Platform supervisor;
- inherits the existing PostgreSQL claim-generation, lease, cancellation, reclaim, and stale-write
  fences;
- writes only generation-specific staging and `/data/event-runs`;
- deep-verifies a sealed Run before recovery publication; and
- has no DWD mutation, replay-queue, arbitrary-command, arbitration, or blind-partition authority.

The production compose gives the Event Worker read-only mounts for:

- `dwd-base-202607-v2.1`;
- `dwd-base-202607-v2.1-correction-20260813-001`; and
- the frozen development research reference bundle.

The API receives `/data/event-runs` read-only so the restricted production capability can return
bounded Run metadata without giving DevSpace filesystem access.

## Image and recovery

`ops/bin/build-event-image` creates a dedicated `quanthus-event` image from an isolated build
context. Unlike the Platform API image it intentionally includes the P4 domain and Direct-DWD
modules required by the child process; the supervisor itself does not import those modules.

PK6 recovery packaging is extended without copying warehouse authority. The package now retains:

- the exact Event image identity;
- immutable `/data/event-runs/runs` artifacts;
- PostgreSQL Job rows and result references; and
- restore verification that succeeded P4 Job result digests still bind the restored Event Library
  manifests.

DWD Base, correction, and research references remain external read-only authorities and are not
copied into the recovery package.

## Verification at this checkpoint

Repository verification at this checkpoint:

- Event Worker / Platform / P4 boundary regression: 138 passed and 3 PostgreSQL-DSN tests skipped;
- full repository `make check` equivalent: 1,174 passed and the same 3 optional PostgreSQL-DSN
  tests skipped;
- doctor, compileall, planning preview, and `git diff --check`: passed;
- Ruff on Platform, Event Library, Direct-DWD, and affected tests: passed;
- `bash -n` on Event image build and Platform recovery scripts: passed;
- production and recovery Compose parsing: passed;
- dedicated API and Event images built in isolated rootless Docker: passed;
- API image imports `quanthus.platform.event_worker` while `quanthus.event_library` is absent,
  proving the Platform supervisor has no packaged P4-domain dependency;
- Event image source inspection proves the supervisor contains no direct Event-Library import and
  invokes the fixed `verify-p4-event-scan` child contract;
- Event image CLI exposes both `run-p4-event-scan` and `verify-p4-event-scan`.

Production deployment and real-corpus double-run evidence remain intentionally pending.

## Remaining #74 gate

1. merge the Event Worker implementation to current `main`;
2. deploy the exact API/Event Worker revision through the restricted Quanthus production deploy
   capability;
3. prove the Event Worker DWD/correction mounts remain read-only;
4. submit Run A with the governed P4 input;
5. wait for and deep-verify immutable Event Run A;
6. submit Run B with the exact same input and a different idempotency key;
7. require identical candidate count, ordered event identities, Event Library manifest SHA-256,
   and content digest;
8. record the two Job/Run ids and digests in final #74 acceptance evidence; and
9. only then close Issue #74 and advance to `P4-LABEL-001`.
