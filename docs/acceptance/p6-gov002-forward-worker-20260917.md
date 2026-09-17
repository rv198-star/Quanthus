# P6-GOV-002 Composite Forward Worker Acceptance — 2026-09-17

| Field | Value |
| --- | --- |
| Status | accepted for disabled rehearsal only |
| Work item | `P6-GOV-002` |
| Job kind | `quanthus.p6-forward-cycle.v1` |
| Queue | existing `replay` |
| Runtime config | `config/forward-validation/runtime/p6-forward-worker.v1.json` |
| Official prospective cycle | not authorized |
| Next gate | `P6-PAPER-001` |

## Accepted outcome

The existing PostgreSQL Replay Worker now has one additive static P6 handler. It uses the existing
queue, claim-generation fencing, lease/cancel lifecycle and memory guard. No API route, new queue,
Worker registry, Redis/Celery/RabbitMQ dependency or new transport was added.

The future ten-stage Forward graph is frozen, but the only executable mode at this gate is
`disabled_rehearsal`. That mode carries no activation decision, DWD release or paper state and must
produce zero DWD/Shadow/Paper/settlement/scorecard side effects. It exists solely to exercise the
real child-process, sealing and recovery lifecycle before later dependencies are allowed to supply
real forward inputs.

## Verification

Focused P6 worker/data/scorecard/planning/traceability verification passed `59/59`.

The worker-specific coverage includes:

- canonical rehearsal submission/idempotency/concurrency keys;
- official-shaped input rejected before activation;
- real Replay Worker dispatch and child execution;
- immutable `READY -> manifest -> rehearsal` seal verification;
- exact retry recovery without child re-execution;
- partial/tampered final Run fail-close;
- stale claim generation publishes nothing;
- cancellation publishes no Run;
- lease loss publishes no Run;
- existing Replay Worker memory guard terminates the P6 child before publication;
- zero DWD, Shadow, Paper, settlement, scorecard, notification, broker and live-capital effects.

The predecessor P6-SCORE-001 full repository gate had already passed `2603` tests with `7` existing
environment skips and zero failures. A full repository gate is rerun after the current-state
closeout is reconciled.

## Recovery semantics

The P6 Run id is derived from canonical input identity. Exact retries find and verify the existing
sealed Run. Non-deterministic duplicate Runs are rejected. Final artifacts are immutable; a corrupt
or partial Run is an error, never permission to overwrite it.

## Authority ceiling

This acceptance does not activate P6 and does not consume prospective market evidence. It grants no
paper runtime, Web projection, Stage action authority, new expert, aggregator change, notification,
broker, live capital, 2022-2024 arbitration, or 2025 permanent-blind access. The next dependency is
`P6-PAPER-001`.
