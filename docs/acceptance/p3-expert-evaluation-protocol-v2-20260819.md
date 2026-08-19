# Expert Evaluation Protocol v2 Acceptance (2026-08-19)

| Field | Value |
| --- | --- |
| Status | accepted |
| Work item | `P3-EXPERT-001` / Issue #146 |
| Governing design | `docs/design/lld/04-experts-aggregator/expert-strategy-layer/evaluation-protocol-v2.md` |
| Implementation | `src/quanthus/expert_evaluation/` |
| Golden manifest SHA-256 | `c3d8aabf68d6fee7a5e6d994e3a7e617ba6d95f111ca581b1254da79e84608ec` |
| Compatibility inventory SHA-256 | `34b43214aec7426da3cb97029f41bb9e724402ed1aad46a0d23a17bd3622b147` |

## Accepted scope

The additive domain package freezes seven exact, self-digesting protocol artifacts:
`ExpertEvaluationSpec`, `ForecastSpec`, `ExpertObservation`, `PolicyEffect`,
`OutcomeSettlement`, `ExpertScorecard`, and `ExpertLifecycleDecision`. Canonical serialization
uses ASCII JSON, sorted keys, normalized finite decimals, and explicit UTC timestamps. Exact-field
decoders reject unknown, missing, duplicate, mixed-version, or noncanonical input.

The graph validator fails closed on point-in-time violations, broken assessment/forecast/policy
bindings, incomplete observation-to-policy coverage, invalid settlement closure, undeclared
metrics, inconsistent sample ledgers, and lifecycle/Shadow conflicts. Assessment identity is
separate from policy-mapping identity: later settlement or policy remapping cannot change an
existing prediction identity.

## Compatibility evidence

The v2 package is a sidecar and changes no accepted v1 production module or golden fixture. The
frozen inventory verifies the file bytes for `expert_protocols.py`, `aggregator.py`, the governed
strategy configuration, and the accepted P2/P3 golden Runs. It also verifies semantic digests for
the three v1 expert outputs, aggregation/action results, governance result, and DecisionEvents.

Golden round trips cover every v2 artifact. Typed adversarial cases cover schema, digest, field,
timestamp, decimal, PIT, settlement, ledger, metric, lifecycle/Shadow, and v1 compatibility
failures. The final related regression suite passed 102 tests, including the v1 protocols,
aggregator, governed strategy, documentation traceability, and planning contracts.

Full repository `make check PYTHON=.venv/bin/python` passed environment diagnostics, compilation,
planning synchronization checks, and 1,190 tests. Three PostgreSQL acceptance tests were skipped
because `QUANTHUS_TEST_POSTGRES_DSN` was not configured; this protocol-only issue neither changes
nor claims those external-runtime paths.

## Boundary and next gate

This acceptance proves protocol identity, serialization, validation, and v1 byte compatibility.
It does not prove a concrete target, a real expert's accuracy, score calculation, DWD evaluation,
Worker execution, immutable Run publication, strategy economics, product UI, Shadow operation, or
sealed-partition access. Those remain owned by later dependency-gated work.

`P3-EXPERT-002` is now ready to integrate this protocol with the existing PostgreSQL Worker. No
Redis, broker, Worker registry, new always-on service, or per-opinion database table is authorized
by this acceptance.
