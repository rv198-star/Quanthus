# P3 Governance Aggregation Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Scope | Fixed veto precedence, context rebinding, constraint intersection, and typed conflicts |
| Governing design | `docs/design/lld/04-experts-aggregator.md` |
| Governing work | `P3-AGG-001` |
| Dependency evidence | `docs/acceptance/p3-expert-protocol-slice-20260728.md` |

## 1. Decision

`P3-AGG-001` passes its bounded governance slice. `src/quanthus/aggregator.py` canonically orders
typed veto and constraint outputs, revalidates their evidence against one ExpertContext, applies
the fixed required-action precedence, and intersects constraints only when no veto terminates.

This decision does not approve score calculation or action mapping. The output remains governance
evidence and cannot create a trial entry, add, reduce, exit, Signal, OrderIntent, or Order.

## 2. Contract evidence

| Concern | Evidence |
| --- | --- |
| System-before-strategy ordering and fixed veto precedence | `src/quanthus/aggregator.py`, `tests/unit/test_aggregator.py` |
| Mandatory applicable system veto | `tests/unit/test_aggregator.py` |
| Constraint intersection and conflict failure | `tests/unit/test_aggregator.py` |
| Abstention without implicit defaults | `tests/unit/test_aggregator.py` |
| Context evidence rebinding and duplicate rejection | `tests/unit/test_aggregator.py` |
| Input-order-independent output identity | `tests/unit/test_aggregator.py` |
| Design and work-item traceability | `tests/contract/test_document_traceability.py` |

## 3. Accepted behavior

- Veto evaluation order is system authority, strategy authority, expert id, and expert version.
- Required-action precedence is force-liquidate, reduce-only, block-new, then none. Every expert
  tied at the winning action remains in the terminating audit set.
- At least one applicable system veto is required. A missing or entirely abstained system layer
  fails closed even when a strategy veto says allow.
- An actionable veto terminates governance before constraint intersection, so a lower-priority
  constraint cannot override or replace it.
- Without an actionable veto, applicable position bounds intersect as the greatest minimum and
  smallest maximum. All contributing stop/hold rules and output digests are retained.
- An empty intersection, no applicable constraint, duplicate expert output, or evidence outside the
  aggregation context produces a stable typed failure.
- Permuting veto or constraint input order cannot change canonical output or digest.

## 4. Residual boundary

`P3-AGG-002` owns confidence-adjusted equal-weight scoring and the versioned mapping from score plus
governance bounds to no-op, trial entry, add, reduce, or exit. `P3-EXP-002` still owns concrete
experts. Until both pass, GovernanceResult has no route to P2 execution.

## 5. Verification

- Focused governance aggregation suite: passed 9 tests.
- Focused Ruff lint/format and strict mypy: passed.
- Final full repository conformance run: passed 207 tests, plus doctor, compile, and planning
  preview checks.
