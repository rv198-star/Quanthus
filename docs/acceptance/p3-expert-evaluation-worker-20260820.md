# Expert Evaluation Worker Acceptance (2026-08-20)

| Field | Value |
| --- | --- |
| Status | accepted |
| Work item | `P3-EXPERT-002` / Issue #147 |
| Governing design | `docs/design/lld/04-experts-aggregator/expert-strategy-layer/worker-evaluation-pipeline.md` |
| Job kind | `quanthus.expert-evaluation.v1` |
| Queue | existing `replay`, production concurrency one |
| Run schema | `quanthus.expert-evaluation-run.v1` |
| PostgreSQL migration | `jobs-0004-expert-evaluation-runs` |

## Accepted scope

The existing PostgreSQL Replay Worker now uses static two-kind dispatch for candidate replay and
expert evaluation. An expert Job accepts an exact non-empty canonical spec set, the effective DWD
identity, authorized development bounds, ordered interventions, parent Run digests, and an exact
producer binding. The API-facing submission helper derives its idempotency and concurrency keys
from canonical content; caller-selected filesystem paths and transport identity do not enter the
domain input.

The isolated child resolves registry keys under one configured root, verifies canonical v2 spec
bytes and digests, and rejects path escape, drift, partition mismatch, incomplete formal-strategy
closure, and unadmitted interventions before an operation producer can proceed. The only producer
admitted by this infrastructure issue is explicitly named as a deterministic protocol fixture. It
proves the pipeline and is never formal-strategy or expert-quality evidence. Concrete DWD target,
resolver, observation, settlement, and scorecard semantics remain owned by `P3-EXPERT-003`.

## Publication and recovery evidence

`run.json` is semantic-only: Job id, claim generation, Worker id, attempt, timestamps, absolute
paths, and retry metadata are excluded. Its SHA-256 defines `expert_run_<digest>`. The Run includes
canonical spec closure, real deterministic Parquet fixture artifacts, typed stage skips, bounded
summary, checksums, and a `READY` marker written last. Deep verification checks containment,
symlinks, exact physical inventory, every checksum, Parquet row counts, stage order, Job input, and
the content-derived Run id before registration.

Retry first adopts an already registered or orphaned sealed content Run after deep verification.
Partial attempt directories are generation-local and replaceable. PostgreSQL publication locks the
current running Job, includes `claim_generation`, rejects cancellation or stale ownership, and
allows only one Run for a canonical input digest. A second Job may adopt the identical Run; a
different Run for the same input fails closed. PostgreSQL stores one bounded Run summary/reference
row and no observation, settlement, opinion, score, or trade rows.

Cancellation was exercised at all eight stage boundaries and produced no published Run. Worker
shutdown or lease loss terminates the child without a terminal write so PostgreSQL lease recovery
can reclaim the Job. Candidate replay retains its existing command, artifact, active-pointer, and
publication paths.

## Verification

- full repository `make PYTHON=.venv/bin/python check`: 1,232 passed;
- disposable PostgreSQL 16: expert registration/adoption/nondeterminism fencing, candidate Run
  publication, and two-Worker lease competition all passed;
- focused Worker, artifact, migration, and boundary suite: 22 passed;
- focused Ruff and strict MyPy over all changed source modules: passed;
- deterministic fixture: two different Job/generation staging paths produced the same Run id and
  byte-identical Run inventory;
- `git diff --check`, environment doctor, compileall, and planning synchronization: passed.

GitHub Actions run `32336758653` did not execute a test step. Its check annotations state that the
jobs were not started because recent account payments failed or the spending limit must be
increased; the Python 3.12 matrix leg was then canceled because Python 3.10 had not started. This
is external CI unavailable evidence, not a passing or failing code result. The complete local and
disposable-PostgreSQL evidence above is the acceptance basis.

## Boundary and next gate

This acceptance proves infrastructure behavior, not that any expert predicts correctly or improves
strategy economics. It opens no arbitration, permanent-blind, P5/P6, notification, approval, or
execution authority. It adds no Redis, broker, scheduler, registry service, second queue, second
Worker, active expert pointer, or opinion-row table.

`P3-EXPERT-003` remains blocked until `P4-LABEL-001` / Issue #75 is accepted and the human-owned
target decision is recorded. Passing this Worker fixture cannot supply or infer that decision.
