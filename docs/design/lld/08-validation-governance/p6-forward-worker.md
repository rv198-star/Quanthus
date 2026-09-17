# P6 Composite Forward Worker

| Field | Value |
| --- | --- |
| Parent | `../08-validation-governance.md` |
| Work item | `P6-GOV-002` |
| Design status | approved |
| Delivery gate | accepted for disabled rehearsal; official cycle still blocked |
| Runtime config | `config/forward-validation/runtime/p6-forward-worker.v1.json` |
| Job kind | `quanthus.p6-forward-cycle.v1` |
| Queue | existing `replay` |

## Boundary

P6-GOV-002 adds one static Forward job kind to the existing PostgreSQL Replay Worker. It adds no
queue, registry, broker, message transport, Redis, Celery or RabbitMQ. The worker inherits the
existing claim-generation fencing, lease renewal, cancellation and memory guard.

The immutable future stage graph is:

1. verify frozen bindings and activation;
2. verify one DWD release;
3. build one shared effective-view projection;
4. settle eligible prior paper intents;
5. produce outcome-blind expert observations and the aggregate decision;
6. append a Shadow record with no execution edge;
7. project the decision to an isolated paper intent;
8. settle matured targets;
9. build role-specific scorecards/loss attribution;
10. seal one immutable Forward Run.

The graph is not authority. At this delivery gate only `disabled_rehearsal` is executable.

## Disabled rehearsal

The rehearsal uses the real Replay Worker child-process, staging, immutable Run sealing and recovery
path, but carries no activation decision, DWD release or paper ledger. Therefore its required side
effects are exactly zero:

- DWD reads = 0;
- shared projection builds = 0;
- Shadow writes = 0;
- paper writes = 0;
- settlement writes = 0;
- scorecard writes = 0;
- notification/broker/live-capital actions = 0.

The Run records the full future stage graph as blocked by disabled activation. This proves the
worker lifecycle without reclassifying known 2026 data as prospective evidence.

## Recovery and failure semantics

A Run is addressed by the canonical input digest. An exact retry recovers the same sealed Run.
`READY` binds `manifest.json`, which in turn binds the rehearsal payload. A partial/tampered final
Run fails closed and is never silently overwritten.

Stale claim generation and lease loss publish nothing. Cancellation publishes no Run. The existing
Replay Worker memory guard stops the child before publication. API/Web do no DWD analysis; no P6
submission route is added by this child.

## Authority ceiling

P6-GOV-002 authorizes implementation and disabled rehearsal only. Official prospective execution,
paper runtime, product projection, notification, broker, live capital, new experts, Stage action
mapping, aggregator changes and sealed-partition access remain false. P6-PAPER-001 is the next child.
