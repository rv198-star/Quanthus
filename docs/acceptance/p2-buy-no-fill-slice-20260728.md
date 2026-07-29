# P2 Buy No-Fill Slice Acceptance Record

| Field | Value |
| --- | --- |
| Evidence status | bounded slice passed |
| Evidence date | 2026-07-28 |
| Cases | `P2-BUY-SUSPENDED-001`, `P2-BUY-LIMIT-UP-001`, `P2-BUY-MISSING-EVIDENCE-001`, `P2-BUY-CASH-SHORTAGE-001` |
| Scope | Conservative termination of next-open buy orders without a fill |
| Governing design | `docs/design/lld/02-backtest-engine.md` |
| Governing work | `P2-MATCH-001`, `P2-ORDER-001`, `P2-AUDIT-001`, `P2-ACCEPT-001` |

## 1. Decision

The second P2 slice passes its bounded acceptance cases. The engine now distinguishes a market
condition that prevents execution from an invalid order or capital request:

| Condition | State transition | Reason code |
| --- | --- | --- |
| Security suspended | `created_to_cancelled` | `security_suspended` |
| Buy blocked at limit-up | `created_to_cancelled` | `buy_blocked_limit_up` |
| Execution evidence missing | `created_to_cancelled` | `missing_execution_evidence` |
| Unit cash below gross plus commission | `created_to_rejected` | `insufficient_unit_cash` |

Every case keeps cash unchanged, emits no Fill or LedgerEntry, and publishes a deterministic
zero-position daily snapshot. This advances the matcher and order lifecycle but does not complete
either work item.

## 2. Evidence map

| Concern | Evidence |
| --- | --- |
| Typed market state and no-invented-price contract | `src/quanthus/backtest/contracts.py` |
| Cancellation/rejection precedence | `src/quanthus/backtest/engine.py` |
| Hand-calculated oracle | `tests/fixtures/golden/p2_buy_no_fill.v1.json` |
| Behavioral verification | `tests/unit/test_backtest_engine.py` |

`OpenExecutionState` retains directional limit meaning: a buy-blocked limit-up observation is not
a generic non-trading flag. Suspended and missing-evidence sessions carry no fabricated prices;
price-bearing states require positive open and close observations.

## 3. Residual boundary

This evidence does not derive limit state from P1 price-limit rows, model partial fills, process
sells, carry a blocked sell across sessions, or reconcile an existing position through a
suspension. Production costs, full exchange regimes, corporate actions, candidate competition,
external-pool flows, persistence, metrics, and notification remain open.

## 4. Verification

- Focused policy and engine suite: passed 12 tests.
- Ruff and formatting on the P2 package and focused tests: passed.
- Mypy on `src/quanthus/backtest`: passed.
- Full repository `make check PYTHON=.venv/bin/python`: passed 130 tests, environment doctor,
  compilation, and planning synchronization preview.
