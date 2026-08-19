# ADR 0012: Lean PostgreSQL Worker Runtime for Personal Strategy Experiments

- Status: Accepted
- Date: 2026-08-13
- Decision id: `PK-WORKER-000`
- Design status: approved
- Delivery gate: open
- Governing epic: `PK-WORKER-000`

## Context

The accepted Platform Kernel persists finite Job envelopes in PostgreSQL but executes handlers in
background threads owned by the single Gunicorn API process. That adapter was sufficient for the
bounded attribution verifier. It is not sufficient for candidate replay: the current five-candidate
replay takes about 99 minutes on the four-core OCI host and must survive API restarts.

Quanthus also needs to avoid two opposite errors. Running batch work in the Web/API environment
couples product availability to analytical execution. Sending every warehouse read through an
asynchronous queue would make ordinary product queries unnecessarily slow and complex.

## Decision

### 1. Separate the control plane from execution

The Web and API are control-plane processes. They may validate and persist a Job request, read
published metadata and projections, request cancellation, and return Job or Run state. They must
not execute replay, backtest, or any other product-initiated batch handler.

Product-initiated heavy computation runs in an independently deployed Worker. Existing CN01,
Hermes, and OCI node-local collection, DWD maintenance, synchronization, and notification schedules
remain under their accepted `systemd` and operator-CLI contracts. They are not migrated merely to
unify orchestration. Lightweight reads of published metadata, Job/Run state, lineage, quality
reports, and serving projections remain synchronous.

### 2. Use PostgreSQL as the first durable queue transport

PostgreSQL is the Job state and queue authority for product-initiated work. API submission inserts
a durable immutable-input Job. Workers poll and pull compatible work with transactional
`FOR UPDATE SKIP LOCKED` leasing. `LISTEN/NOTIFY` is deferred until measured dispatch latency or
database load justifies its reconnect and notification-loss complexity.

Redis, Celery, RabbitMQ, and a separate scheduler are not admitted. A future transport may replace
the claim/wakeup adapter only after measured workload or topology evidence justifies it. Job, Run,
idempotency, cancellation, evidence, and publication contracts must remain transport-independent.

### 3. Deploy one statically admitted Replay Worker first

The initial production topology contains one Replay Worker with concurrency one. Its image,
`queue=replay`, admitted Job kinds, database role, read-only DWD mount, and writable Run root are
static deployment configuration. It needs no Worker registry, dynamic capability document,
resource class, or generalized routing layer.

The Replay Worker may perform replay, metrics, evidence construction, sealing, and the
candidate-active-evidence database compare-and-swap as stages of one logical Job. It cannot modify
authoritative DWD. Future Worker kinds receive separate queues and credentials only when a named
product workflow needs them.

#### P4 named extension: one static Event Worker

`P4-EVENT-002` / Issue #74 is the first named workflow that exercises that extension rule. Its
2012-2021 full-market forward scan is a long-running, product-initiated analytical task with a
separate immutable result family, so it receives one statically admitted `event-worker` with
`queue=event` and concurrency one rather than expanding Replay Worker permissions.

The Event Worker admits only `quanthus.p4-event-scan.v1`. Its immutable input binds the exact code
revision and the frozen P4 source-policy, direct-DWD provider, candidate-rule, and split-policy
paths. It receives read-only mounts for DWD Base V2.1, correction-001, and the frozen development
research references, plus one writable `/data/event-runs` root. It cannot modify DWD, submit
arbitrary commands, run replay Jobs, or open arbitration/blind partitions. A sealed Event Run keeps
the event-library package, its manifest/content digests, and the generation-fenced Job identity;
a reclaim validates the package before terminal publication.

This second Worker kind does not introduce a registry, resource classes, dynamic routing,
Redis/Celery/RabbitMQ, or another scheduler. PostgreSQL remains the queue authority and the same
lease, cancellation, stale-generation, immutable-Run, and recovery contracts apply.

### 4. Lease at least once; publish exactly one accepted result

Queue execution is at least once. A claim records `claimed_by`, a monotonically increasing
`claim_generation`, and `lease_expires_at`, using PostgreSQL time. An expired lease may be
reclaimed. Renewal, progress, terminal transition, and active-evidence publication all condition
their writes on the current Job id, generation, and `running` state. A stale generation cannot
update authority or publish a result.

The queue lifecycle and execution stage are independent:

- lifecycle: `queued`, `running`, `succeeded`, `failed`, or `canceled`;
- stage: job-kind-specific bounded values such as input validation, DWD resolution, frame build,
  replay, metrics, evidence validation, and publication.

Cancellation is the orthogonal `cancel_requested_at` field. The Web derives a canceling display
state while a running Job observes it. A delayed retry is `queued` with a future `available_at`;
claiming moves `queued` directly to `running` in one transaction.

Progress uses meaningful counters when a denominator exists. A percentage is optional, derived,
and explicitly estimated; it is never the state authority. Workers coalesce progress writes to
avoid turning PostgreSQL into a high-frequency event stream.

### 5. Execute each analytical attempt in an isolated child process

A Worker Supervisor owns claim, lease renewal, cancellation, timeout, and child-process
health. One analytical attempt runs in a separate child process with a Job-specific staging
directory. A failed or leaking strategy process cannot kill the Supervisor or retain a lease.
If lease renewal loses ownership, the Supervisor terminates and then kills the child within a
bounded deadline. Horizontal correctness is tested with two competing instances, but production
continues to run one instance until measured queue delay justifies another.

### 6. Keep immutable domain artifacts outside Job rows

PostgreSQL stores bounded Job metadata, Run index, active-evidence pointer, and result references.
It is not an event stream. Equity curves, trades, decisions, quality reports, attempt logs, and
evidence packages are immutable artifacts. Workers write generation-specific staging, validate
and seal it, then publish it under an immutable Run identity. Failed, canceled, or stale attempts
cannot change the active result.

For a candidate replay, only successful replay, evidence construction, validation, and atomic
publication create a new active `run_id` and `evidence_digest`. The new digest starts at evidence
review `pending-review`; prior Runs and approval histories remain addressable. Approval writes
continue rejecting stale evidence digests.

### 7. Freeze direct DWD consumption for ordinary replay

Replay Workers consume an exact manifest- and digest-bound authoritative DWD view through a shared
PIT provider. They may build disposable Job-local frames or caches, but ordinary reruns do not
require one maintained ADS per strategy. A separately frozen portable package remains justified
only for governance admission, cross-node transfer, or long-term archival.

## Consequences

The production process set adds one independently scalable Replay Worker role. The current in-process
`LocalJobRunner` becomes a test/ephemeral adapter and must not remain reachable in production once
the Worker Runtime is accepted. API availability no longer depends on analytical task duration.
Existing node-local data schedules retain their own state and recovery authority.

PostgreSQL receives additional queue traffic. Claims, lease renewals, progress, and indexes must
therefore be bounded and observable. Large payloads remain outside PostgreSQL. Queue database
roles use least privilege and do not receive general warehouse filesystem access by implication.

## Rejected alternatives

### API calls a Worker HTTP endpoint

Rejected because it requires online Worker discovery, load balancing, timeout handling, and
redelivery while still lacking a durable task authority.

### Redis/Celery from the first replay

Rejected because current concurrency does not justify another operational dependency. The stable
contract is the Job/Run protocol, not a particular message library.

### Build a general multi-Worker platform now

Rejected because task diversity in a personal strategy laboratory does not imply distributed-scale
concurrency. Registry, event stream, resource classes, five Worker families, and always-on scale-out
would increase implementation and failure surface before measured need exists.

### Queue every warehouse read

Rejected because published metadata and serving projections are ordinary synchronous queries.
Only product-initiated expensive scans or computation require Worker execution. Existing accepted
node-local schedules do not become Worker Jobs without a named interactive workflow or a measured
recovery/observability defect.
