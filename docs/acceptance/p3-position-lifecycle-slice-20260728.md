# P3 Position Lifecycle Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Scope | Legal empty/trial/dynamic/exiting/closed transitions and P2 ledger reconciliation |
| Governing design | `docs/design/lld/05-position-execution.md` |
| Governing work | `P3-POS-001` |
| Dependency evidence | `docs/acceptance/p2-conformance-acceptance-20260728.md` |

## 1. Decision

`P3-POS-001` passes its bounded lifecycle slice. `src/quanthus/position_lifecycle.py` implements a
versioned immutable state snapshot, exact legal transition table, typed source-evidence references,
and reconciliation against P2 `UnitState` before and after every transition.

This decision approves state topology only. It does not freeze trial/add/hold/cooldown parameters,
implement concentration or review rules, select an exit source, or create a Signal, OrderIntent,
Order, Fill, simulated position, notified position, or actual position.

## 2. Contract evidence

| Concern | Evidence |
| --- | --- |
| Exact legal lifecycle topology | `src/quanthus/position_lifecycle.py`, `tests/unit/test_position_lifecycle.py` |
| P2 ledger identity and quantity reconciliation | `tests/unit/test_position_lifecycle.py` |
| Fill, decision, order-status, settlement, and calendar evidence requirements | `tests/unit/test_position_lifecycle.py` |
| Transition/evidence idempotency and canonical serialization | `tests/unit/test_position_lifecycle.py` |
| Design and work-item traceability | `tests/contract/test_document_traceability.py` |

## 3. Accepted behavior

- Empty can become Trial only after a fill-backed entry whose P2 ledger changes from no position to
  a positive position; it cannot jump directly to Dynamic or full-exposure semantics.
- Trial becomes Dynamic only after an add fill. Dynamic add/reduce self-transitions require the P2
  settled quantity to move in the declared direction while preserving unit and security identity.
- Trial or Dynamic can enter Exiting only through a decision-backed exit request that does not
  mutate the P2 position.
- An order-status-backed untradable sell remains Exiting without changing quantity. A fill-backed
  full sell reaches Closed only when the P2 ledger is empty.
- Closed retains security/opportunity identity for audit and returns to Empty only with both unit
  settlement and versioned calendar evidence.
- Reused transition ids, reused evidence, non-monotonic times, cross-unit ledger views, and identity
  drift produce stable typed failures.
- Evidence and state serialization are canonical and deterministic.

## 4. Residual boundary

`P3-POLICY-001` must freeze trial/add/hold/cooldown values before numeric position behavior exists.
`P3-RISK-001`, `P3-POS-002`, `P3-EXIT-001`, and `P3-POS-003` own concentration, review/add rules,
exit arbitration, and operational cooldown/carried-exit behavior. P2 remains the sole owner of
Signal-to-OrderIntent-to-Order conversion, matching, fills, costs, and accounting.

## 5. Verification

- Focused position lifecycle suite: passed 11 tests.
- Focused Ruff lint/format and strict mypy: passed.
- Final full repository conformance run: passed 219 tests, plus doctor, compile, and planning
  preview checks.
